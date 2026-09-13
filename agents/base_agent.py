"""
Base agent infrastructure supporting Gemini 3.8 / 3.7 / 3.5 LLM inference with automated rate-limit failover and token governance.
"""

import json
import time
import logging
from typing import Dict, Any, Optional
import httpx
from config import config
from storage.state_store import StateStore
from analytics.token_budget import TokenBudgetManager

logger = logging.getLogger(__name__)

# Global model health circuit breaker: model_name -> degraded_until (unix timestamp)
MODEL_CIRCUIT_BREAKER: Dict[str, float] = {}
CIRCUIT_BREAKER_TTL = 60.0  # seconds to bypass a degraded model

# Persistent HTTP/2 connection pool for Gemini API calls to eliminate TLS handshake latency
_HTTP_CLIENT: Optional[httpx.Client] = None
try:
    import h2  # noqa
    _HAS_HTTP2 = True
except ImportError:
    _HAS_HTTP2 = False

def get_agent_http_client() -> httpx.Client:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        _HTTP_CLIENT = httpx.Client(
            http2=_HAS_HTTP2,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50, keepalive_expiry=60.0),
            timeout=30.0
        )
    return _HTTP_CLIENT


class BaseAgent:
    def __init__(self, name: str, role_description: str, state_store: Optional[StateStore] = None):
        self.name = name
        self.role_description = role_description
        self.use_llm = config.use_llm and bool(config.gemini_api_key)
        self.state_store = state_store or StateStore(config.db_path)
        self.token_manager = TokenBudgetManager(
            self.state_store,
            daily_token_limit=config.daily_token_limit,
            max_tokens_per_cycle=config.max_tokens_per_cycle
        )
        self.api_key = config.gemini_api_key
        self.model_name = config.model_name
        self.model = self.model_name if self.use_llm else None

    def query_llm_json(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.2,
        api_key: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Sends a prompt to Gemini requesting a JSON object with resilient candidate failover.
        """
        active_key = api_key if api_key is not None else self.api_key
        if not active_key:
            return None

        # Check token budget status
        if config.enable_token_governance:
            status = self.token_manager.check_budget_status()
            if status.budget_exhausted:
                return None

        candidate_models = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-flash-latest"]

        if self.model_name and self.model_name not in candidate_models:
            candidate_models.insert(0, self.model_name.replace("models/", ""))

        # Prioritize healthy models over temporarily degraded ones
        now = time.time()
        healthy_models = [m for m in candidate_models if MODEL_CIRCUIT_BREAKER.get(m, 0.0) <= now]
        degraded_models = [m for m in candidate_models if MODEL_CIRCUIT_BREAKER.get(m, 0.0) > now]
        ordered_models = healthy_models + degraded_models

        full_prompt = prompt
        if system_instruction:
            full_prompt = f"SYSTEM INSTRUCTIONS:\n{system_instruction}\n\nUSER PROMPT:\n{prompt}\n\nIMPORTANT: Respond ONLY with valid JSON."
        else:
            full_prompt = f"{prompt}\n\nIMPORTANT: Respond ONLY with valid JSON."

        prompt_tokens_est = self.token_manager.estimate_tokens(full_prompt)

        payload_standard = {
            "contents": [{"parts": [{"text": full_prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": temperature
            }
        }
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": active_key
        }

        for model_to_try in ordered_models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_to_try}:generateContent"

            try:
                client = get_agent_http_client()
                resp = client.post(url, json=payload_standard, headers=headers, timeout=25.0)
                if resp.status_code == 200:
                    MODEL_CIRCUIT_BREAKER.pop(model_to_try, None)
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if not candidates:
                        continue

                    text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
                    completion_tokens_est = self.token_manager.estimate_tokens(text)

                    # Record token usage including reasoning thinking tokens
                    usage_meta = data.get("usageMetadata", {})
                    p_tok = usage_meta.get("promptTokenCount", prompt_tokens_est)
                    c_tok = usage_meta.get("candidatesTokenCount", completion_tokens_est) + usage_meta.get("thoughtsTokenCount", 0)

                    self.token_manager.record_usage(
                        agent_name=self.name,
                        prompt_tokens=p_tok,
                        completion_tokens=c_tok,
                        model_name=model_to_try
                    )

                    # Clean markdown code blocks if any
                    if text.startswith("```json"):
                        text = text[7:]
                    if text.startswith("```"):
                        text = text[3:]
                    if text.endswith("```"):
                        text = text[:-3]

                    # Extract first valid JSON block if model returns conversational markdown
                    clean_text = text.strip()
                    if "{" in clean_text and "}" in clean_text:
                        start_idx = clean_text.find("{")
                        end_idx = clean_text.rfind("}") + 1
                        clean_text = clean_text[start_idx:end_idx]

                    return json.loads(clean_text)
                elif resp.status_code in [401, 403]:
                    logger.warning(f"Gemini API returned {resp.status_code} ({resp.text[:100]}). Aborting model attempts.")
                    return None
                elif resp.status_code in [429, 503]:
                    MODEL_CIRCUIT_BREAKER[model_to_try] = time.time() + CIRCUIT_BREAKER_TTL
                    logger.warning(f"Model {model_to_try} returned {resp.status_code}. Circuit breaker tripped for {CIRCUIT_BREAKER_TTL}s.")
                    continue
                elif resp.status_code == 404:
                    continue
            except Exception:
                continue

        return None


    def query_llm_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        api_key: Optional[str] = None,
        enable_grounding: bool = False
    ) -> Optional[str]:
        """
        Sends a prompt to Gemini requesting formatted Markdown text with resilient candidate failover
        and optional Google Search grounding.
        """
        active_key = api_key if api_key is not None else self.api_key
        if not active_key:
            return None

        if config.enable_token_governance:
            status = self.token_manager.check_budget_status()
            if status.budget_exhausted:
                return None

        candidate_models = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-flash-latest"]

        if self.model_name and self.model_name not in candidate_models:
            candidate_models.insert(0, self.model_name.replace("models/", ""))

        now = time.time()
        healthy_models = [m for m in candidate_models if MODEL_CIRCUIT_BREAKER.get(m, 0.0) <= now]
        degraded_models = [m for m in candidate_models if MODEL_CIRCUIT_BREAKER.get(m, 0.0) > now]
        ordered_models = healthy_models + degraded_models

        full_prompt = prompt
        if system_instruction:
            full_prompt = f"SYSTEM INSTRUCTIONS:\n{system_instruction}\n\nUSER PROMPT:\n{prompt}"

        prompt_tokens_est = self.token_manager.estimate_tokens(full_prompt)

        payload_standard: Dict[str, Any] = {
            "contents": [{"parts": [{"text": full_prompt}]}],
            "generationConfig": {
                "temperature": 0.4
            }
        }
        if enable_grounding:
            payload_standard["tools"] = [{"googleSearch": {}}]

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": active_key
        }

        for model_to_try in ordered_models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_to_try}:generateContent"

            try:
                client = get_agent_http_client()
                resp = client.post(url, json=payload_standard, headers=headers, timeout=30.0)

                if resp.status_code == 200:
                    MODEL_CIRCUIT_BREAKER.pop(model_to_try, None)
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if not candidates:
                        continue

                    finish_reason = candidates[0].get("finishReason", "")
                    parts = candidates[0].get("content", {}).get("parts", [])
                    text = "".join([p.get("text", "") for p in parts if "text" in p]).strip()

                    # Handle case where grounding or function call malformed
                    if not text or finish_reason in ["MALFORMED_FUNCTION_CALL", "SAFETY"]:
                        logger.warning(f"Model {model_to_try} returned finishReason={finish_reason} with empty text.")
                        if enable_grounding:
                            try:
                                ungrounded = {
                                    "contents": [{"parts": [{"text": full_prompt}]}],
                                    "generationConfig": {"temperature": 0.4}
                                }
                                fb_resp = client.post(url, json=ungrounded, headers=headers, timeout=25.0)
                                if fb_resp.status_code == 200:
                                    fb_data = fb_resp.json()
                                    fb_cand = fb_data.get("candidates", [])
                                    if fb_cand:
                                        fb_parts = fb_cand[0].get("content", {}).get("parts", [])
                                        fb_text = "".join([p.get("text", "") for p in fb_parts if "text" in p]).strip()
                                        if fb_text:
                                            return fb_text
                            except Exception as fb_err:
                                logger.warning(f"Ungrounded retry failed on {model_to_try}: {fb_err}")
                        continue

                    completion_tokens_est = self.token_manager.estimate_tokens(text)

                    usage_meta = data.get("usageMetadata", {})
                    p_tok = usage_meta.get("promptTokenCount", prompt_tokens_est)
                    c_tok = usage_meta.get("candidatesTokenCount", completion_tokens_est) + usage_meta.get("thoughtsTokenCount", 0)

                    self.token_manager.record_usage(
                        agent_name=self.name,
                        prompt_tokens=p_tok,
                        completion_tokens=c_tok,
                        model_name=model_to_try
                    )

                    return text
                elif resp.status_code in [401, 403]:
                    logger.warning(f"Gemini API returned {resp.status_code} in query_llm_text. Aborting model attempts.")
                    return None
                elif resp.status_code in [429, 503]:
                    MODEL_CIRCUIT_BREAKER[model_to_try] = time.time() + CIRCUIT_BREAKER_TTL
                    logger.warning(f"Model {model_to_try} returned {resp.status_code}. Circuit breaker tripped for {CIRCUIT_BREAKER_TTL}s.")
                    continue
                elif resp.status_code == 404:
                    continue
                else:
                    logger.warning(f"Model {model_to_try} returned unexpected status {resp.status_code}: {resp.text[:150]}")
            except Exception as ex:
                logger.warning(f"Exception during LLM request on {model_to_try}: {ex}")
                continue

        return None

