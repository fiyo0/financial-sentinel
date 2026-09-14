"""
Token Budget & Cost Management Engine.
Monitors token consumption, enforces daily/cycle quotas, estimates USD costs,
and handles graceful fallback to deterministic heuristics when budgets are reached.
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

from storage.state_store import StateStore
from config import config


PRICING_PER_1M_TOKENS = {
    "gemini-3.8-flash": {"input": 0.15, "output": 0.60},
    "gemini-3.7-flash": {"input": 0.15, "output": 0.60},
    "default": {"input": 0.15, "output": 0.60}
}



class TokenUsageRecord(BaseModel):
    agent_name: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    cached_tokens: int = 0
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class TokenBudgetStatus(BaseModel):
    daily_budget_tokens: int
    daily_tokens_used: int
    daily_cost_usd: float
    remaining_daily_tokens: int
    budget_exhausted: bool
    fallback_mode_active: bool
    current_model: str


class TokenBudgetManager:
    def __init__(
        self,
        state_store: StateStore,
        daily_token_limit: int = 500_000,
        user_daily_token_limit: int = 100_000,
        max_tokens_per_cycle: int = 50_000
    ):
        self.state_store = state_store
        self.daily_token_limit = daily_token_limit
        self.user_daily_token_limit = user_daily_token_limit
        self.max_tokens_per_cycle = max_tokens_per_cycle

    def estimate_tokens(self, text: str) -> int:
        """Rough estimation: ~4 chars per token for English text."""
        return max(1, len(text) // 4)

    def calculate_cost(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        model_name: str,
        cached_tokens: int = 0
    ) -> float:
        pricing = PRICING_PER_1M_TOKENS.get(model_name.lower(), PRICING_PER_1M_TOKENS["default"])
        # Standard input price for non-cached tokens; cached tokens discounted at 25% of standard rate
        non_cached_prompt = max(0, prompt_tokens - cached_tokens)
        input_cost = (non_cached_prompt / 1_000_000.0) * pricing["input"]
        cached_cost = (cached_tokens / 1_000_000.0) * (pricing["input"] * 0.25)
        output_cost = (completion_tokens / 1_000_000.0) * pricing["output"]
        return round(input_cost + cached_cost + output_cost, 6)

    def record_usage(
        self,
        agent_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        model_name: str,
        user_id: Optional[str] = None,
        cached_tokens: int = 0
    ) -> TokenUsageRecord:
        total = prompt_tokens + completion_tokens
        cost = self.calculate_cost(prompt_tokens, completion_tokens, model_name, cached_tokens=cached_tokens)

        record = TokenUsageRecord(
            agent_name=agent_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total,
            estimated_cost_usd=cost,
            cached_tokens=cached_tokens
        )

        # Persist in state database with tenant isolation
        self.state_store.record_token_usage(
            agent_name=agent_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
            model_name=model_name,
            user_id=user_id,
            cached_tokens=cached_tokens
        )
        return record

    def check_budget_status(self, user_id: Optional[str] = None) -> TokenBudgetStatus:
        global_stats = self.state_store.get_today_token_usage()
        global_used = global_stats.get("total_tokens", 0)
        global_exhausted = global_used >= self.daily_token_limit

        if user_id:
            user_stats = self.state_store.get_today_token_usage(user_id=user_id)
            user_used = user_stats.get("total_tokens", 0)
            user_cost = user_stats.get("total_cost_usd", 0.0)
            user_exhausted = user_used >= self.user_daily_token_limit
            exhausted = user_exhausted or global_exhausted
            remaining = max(0, self.user_daily_token_limit - user_used)
            return TokenBudgetStatus(
                daily_budget_tokens=self.user_daily_token_limit,
                daily_tokens_used=user_used,
                daily_cost_usd=round(user_cost, 4),
                remaining_daily_tokens=remaining,
                budget_exhausted=exhausted,
                fallback_mode_active=exhausted,
                current_model=config.model_name
            )

        cost = global_stats.get("total_cost_usd", 0.0)
        remaining = max(0, self.daily_token_limit - global_used)
        return TokenBudgetStatus(
            daily_budget_tokens=self.daily_token_limit,
            daily_tokens_used=global_used,
            daily_cost_usd=round(cost, 4),
            remaining_daily_tokens=remaining,
            budget_exhausted=global_exhausted,
            fallback_mode_active=global_exhausted,
            current_model=config.model_name
        )

