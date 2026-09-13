"""
Interactive Two-Way Telegram Bot Service for Financial Sentinel.
Allows triggering scans, checking portfolio status, exploring moonshots,
and chatting with the Gemini AI Portfolio Analyst directly from Telegram on mobile.
Supports multi-user routing and Bring-Your-Own-Key (BYOK) per-user encryption.
"""
import time
import threading
import logging
import re
from typing import Optional, Dict, Any, List
import httpx
from config import config
from analytics.market_data import update_portfolio_live_prices

logger = logging.getLogger("TelegramBot")

RESERVED_COMMANDS = {
    # System & Navigation
    "start", "help", "menu", "schedule", "times", "status", "ping", "settings",
    "feedback", "stop", "test", "admin", "login", "reset", "logout", "id", "whoami",
    "info", "about", "version", "faq",
    # Conversational Greetings & General Command Words
    "hello", "hi", "hey", "today", "now", "stocks", "stock", "alert", "alerts",
    "rates", "rate", "macro", "crypto", "market", "markets", "trade", "trades",
    "news", "check", "quote", "summary", "daily", "digest", "update", "updates",
    "report", "reports", "signals", "signal", "top", "view", "show", "get", "list",
    "all", "rank", "ranking", "watch", "watchlist", "chat", "ask", "search", "find",
    # Daily Briefings
    "premarket", "morning", "briefing", "midmarket", "midday", "postmarket", "eod", "close",
    "weekend", "sunday", "weekendeod", "earnings", "earning", "calls",
    # Portfolio & Scanning
    "portfolio", "holdings", "positions", "scan", "runscan", "opportunity", "opportunities",
    "opp", "alpha", "moonshots", "moonshot", "cash", "balance",
    # Analysis & Deep Dives
    "analysis", "analyze", "ticker", "deepdive",
    # Trade Execution
    "add", "buy", "rm", "remove", "sell", "del"
}


def parse_add_args(text: str) -> Optional[Dict[str, Any]]:
    tokens = text.strip().replace("$", "").split()
    if not tokens:
        return None
    if tokens[0].lower() in ("/add", "add", "/buy", "buy"):
        tokens = tokens[1:]
    if not tokens:
        return None
    ticker = tokens[0].upper()
    shares = 1.0
    price = None
    if len(tokens) >= 2:
        try:
            shares = float(tokens[1])
        except ValueError:
            pass
    for t in tokens[2:]:
        if t.lower() in ("@", "at"):
            continue
        try:
            price = float(t)
            break
        except ValueError:
            pass
    return {"ticker": ticker, "shares": shares, "price": price}


def parse_rm_args(text: str) -> Optional[Dict[str, Any]]:
    tokens = text.strip().replace("$", "").split()
    if not tokens:
        return None
    if tokens[0].lower() in ("/rm", "rm", "/remove", "remove", "/sell", "sell", "/del", "del"):
        tokens = tokens[1:]
    if not tokens:
        return None
    ticker = tokens[0].upper()
    shares = None
    price = None
    if len(tokens) >= 2:
        if tokens[1].lower() == "all":
            shares = "all"
        else:
            try:
                shares = float(tokens[1])
            except ValueError:
                pass
    for t in tokens[2:]:
        if t.lower() in ("@", "at"):
            continue
        try:
            price = float(t)
            break
        except ValueError:
            pass
    return {"ticker": ticker, "shares": shares, "price": price}


class FinancialSentinelTelegramBot:
    def __init__(
        self,
        orchestrator=None,
        identity_service=None,
        portfolio_service=None,
        analysis_service=None,
        briefing_service=None,
    ):
        from orchestrator import FinancialSentinelOrchestrator
        from channels.telegram import TelegramChannel
        from services import IdentityService, PortfolioService, AnalysisService, BriefingService

        self.orchestrator = orchestrator or FinancialSentinelOrchestrator(config.db_path)
        self.identity_service = identity_service or IdentityService(self.orchestrator.state_store)
        self.portfolio_service = portfolio_service or PortfolioService(self.orchestrator)
        self.analysis_service = analysis_service or AnalysisService(self.orchestrator)
        self.briefing_service = briefing_service or BriefingService(self.orchestrator)

        self.bot_token = config.telegram_bot_token
        self.chat_id = config.telegram_chat_id
        self.telegram_channel = TelegramChannel(self.bot_token, self.chat_id)
        self.is_running = False
        self._thread: Optional[threading.Thread] = None
        self.last_update_id = 0

    def is_configured(self) -> bool:
        return bool(self.bot_token)

    def get_effective_chat_id(self) -> Optional[str]:
        return self.chat_id or self.telegram_channel.get_effective_chat_id()

    def start_polling(self):
        """Starts the Telegram bot listener in a daemon thread."""
        if not self.is_configured():
            return
        if self.is_running:
            return
        self.is_running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("Telegram Bot listener started.")

    def stop(self):
        self.is_running = False

    def _split_message(self, text: str, max_length: int = 3900) -> List[str]:
        if len(text) <= max_length:
            return [text]
        chunks = []
        lines = text.split("\n")
        current_chunk = ""
        for line in lines:
            if len(current_chunk) + len(line) + 1 > max_length:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = line
            else:
                current_chunk = f"{current_chunk}\n{line}" if current_chunk else line
        if current_chunk:
            chunks.append(current_chunk)
        return chunks

    def send_message(self, text: str, chat_id: Optional[str] = None) -> bool:
        import re
        target_chat = chat_id or self.chat_id
        if not target_chat or not self.bot_token:
            return False

        chunks = self._split_message(text)
        success = True

        for chunk in chunks:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": target_chat,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            try:
                resp = httpx.post(url, json=payload, timeout=12.0)
                if resp.status_code != 200:
                    logger.warning(f"Telegram HTML send returned {resp.status_code} ({resp.text}). Retrying with plain text...")
                    plain = re.sub(r"<[^>]+>", "", chunk)
                    resp_retry = httpx.post(url, json={"chat_id": target_chat, "text": plain, "disable_web_page_preview": True}, timeout=12.0)
                    if resp_retry.status_code != 200:
                        logger.error(f"Telegram plain text fallback failed: {resp_retry.text}")
                        success = False
            except Exception as e:
                logger.error(f"Failed to send Telegram message: {e}")
                try:
                    plain = re.sub(r"<[^>]+>", "", chunk)
                    httpx.post(url, json={"chat_id": target_chat, "text": plain}, timeout=10.0)
                except Exception:
                    success = False

        return success

    def send_message_returning_id(self, text: str, chat_id: Optional[str] = None) -> Optional[int]:
        """
        Sends a single status message and returns the created Telegram message_id for live progressive updates.
        """
        import re
        target_chat = chat_id or self.chat_id
        if not target_chat or not self.bot_token:
            return None

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": target_chat,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        try:
            resp = httpx.post(url, json=payload, timeout=8.0)
            if resp.status_code == 200:
                return resp.json().get("result", {}).get("message_id")
            else:
                plain = re.sub(r"<[^>]+>", "", text)
                retry = httpx.post(url, json={"chat_id": target_chat, "text": plain}, timeout=8.0)
                if retry.status_code == 200:
                    return retry.json().get("result", {}).get("message_id")
        except Exception as e:
            logger.error(f"Error in send_message_returning_id: {e}")
        return None

    def edit_message(self, text: str, chat_id: str, message_id: int) -> bool:
        """
        Updates an existing Telegram message in-place with genuine live progress telemetry.
        """
        import re
        if not chat_id or not message_id or not self.bot_token:
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/editMessageText"
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        try:
            resp = httpx.post(url, json=payload, timeout=8.0)
            if resp.status_code == 200:
                return True
            elif "message is not modified" in resp.text:
                return True
            else:
                plain = re.sub(r"<[^>]+>", "", text)
                retry = httpx.post(url, json={"chat_id": chat_id, "message_id": message_id, "text": plain}, timeout=8.0)
                return retry.status_code == 200
        except Exception as e:
            logger.error(f"Error in edit_message: {e}")
            return False

    def setup_webhook(self, base_url: str, secret_token: Optional[str] = None) -> bool:
        if not self.bot_token or not base_url:
            return False
        clean_base = base_url.rstrip("/")
        webhook_url = f"{clean_base}/api/telegram/webhook"
        token_to_use = secret_token or config.resolved_telegram_webhook_secret
        url = f"https://api.telegram.org/bot{self.bot_token}/setWebhook"
        payload: Dict[str, Any] = {
            "url": webhook_url,
            "drop_pending_updates": False,
        }
        if token_to_use:
            payload["secret_token"] = token_to_use
        try:
            resp = httpx.post(url, json=payload, timeout=10.0)
            if resp.status_code == 200 and resp.json().get("ok"):
                logger.info(f"Telegram Webhook configured: {webhook_url} (secret_token: {bool(token_to_use)})")
                return True
            else:
                logger.warning(f"Telegram setWebhook returned: {resp.text}")
        except Exception as e:
            logger.error(f"Failed to configure Telegram Webhook: {e}")
        return False

    def process_webhook_update(self, update: Dict[str, Any]) -> Dict[str, Any]:
        msg = update.get("message") or update.get("edited_message") or {}
        text = msg.get("text", "").strip()
        chat = msg.get("chat", {})
        from_user = msg.get("from", {})
        username = str(from_user.get("username", "")).lower().replace("@", "")
        chat_id = str(chat.get("id", ""))
        logger.info(f"Received Telegram webhook update from @{username} (Chat ID: {chat_id}) [chars: {len(text)}]")
        print(f"TELEGRAM_INCOMING: username={username} chat_id={chat_id} text_len={len(text)}", flush=True)


        if text:
            import threading
            threading.Thread(
                target=self._handle_incoming_message,
                args=(text, chat_id, from_user.get("first_name", "Investor"), username),
                daemon=True
            ).start()

        return {"status": "ok"}

    def _poll_loop(self):
        while self.is_running:
            try:
                url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates?offset={self.last_update_id + 1}&timeout=20"
                resp = httpx.get(url, timeout=25.0)
                if resp.status_code == 200:
                    data = resp.json()
                    for update in data.get("result", []):
                        self.last_update_id = max(self.last_update_id, update.get("update_id", 0))
                        msg = update.get("message", {})
                        text = msg.get("text", "").strip()
                        chat = msg.get("chat", {})
                        from_user = msg.get("from", {})
                        username = str(from_user.get("username", "")).lower().replace("@", "")
                        chat_id = str(chat.get("id", ""))

                        if text:
                            self._handle_incoming_message(text, chat_id, from_user.get("first_name", "Investor"), username)
                elif resp.status_code in (401, 404):
                    time.sleep(30)
                else:
                    time.sleep(3)
            except Exception:
                time.sleep(5)

    def _execute_ticker_analysis(self, target_ticker: str, chat_id: str, user_id: str, user_gemini_key: Optional[str]):
        """
        Executes an institutional-grade, multi-agent single ticker deep dive via AnalysisService.
        Progressively streams status to Telegram, computes technical momentum,
        social sentiment velocity, news catalysts, fundamental moats, and portfolio fit.
        Automatically preserves the generated analysis in the user's web Deep Dive Archive.
        """
        if not user_gemini_key:
            self.send_message(
                "🔒 <b>Gemini API Key Required:</b>\n\nPlease ensure GEMINI_API_KEY is configured on the server or enter your personal key in Dashboard Settings.",
                chat_id
            )
            return

        clean_sym = target_ticker.strip().upper().replace("$", "")
        status_id = self.send_message_returning_id(
            f"🔬 <b>Analyzing {clean_sym}... [20%]</b>\n<i>Fetching real-time market quote and order flow...</i>",
            chat_id
        )

        def progress_cb(pct: int, msg: str):
            if status_id and pct < 100:
                self.edit_message(f"🔬 <b>Analyzing {clean_sym}... [{pct}%]</b>\n<i>{msg}</i>", chat_id, status_id)

        try:
            res = self.analysis_service.run_single_ticker_analysis(
                ticker=clean_sym,
                user_id=user_id,
                api_key=user_gemini_key,
                progress_callback=progress_cb
            )
            analysis_msg = res["analysis"]
            if status_id and len(analysis_msg) < 4000:
                self.edit_message(analysis_msg, chat_id, status_id)
            else:
                self.send_message(analysis_msg, chat_id)
        except ValueError as ve:
            if "not recognized" in str(ve).lower():
                self.send_message(
                    f"⚠️ <b>Ticker '{clean_sym}' Not Recognized:</b>\n\n"
                    f"Unable to verify live trade data for <code>{clean_sym}</code>. "
                    f"Please verify the symbol (e.g. <code>/NVDA</code>, <code>/AAPL</code>, <code>/MSFT</code>).\n\n"
                    f"<i>If you intended to ask a portfolio question, send plain text without a leading slash.</i>",
                    chat_id
                )
            else:
                self.send_message(f"⚠️ {ve}", chat_id)
        except Exception as e:
            self.send_message(f"⚠️ Analysis error for {clean_sym}: {e}", chat_id)

    def _handle_incoming_message(self, text: str, chat_id: str, user_name: str, username: str = ""):
        clean_text = text.strip()
        parts = clean_text.split()
        raw_cmd = parts[0].lower() if parts else ""
        base_cmd = raw_cmd.split("@")[0]
        cmd_token = base_cmd.lstrip("/").lstrip("$").lower()

        # Check for direct ticker shorthand: e.g. /NVDA, /aapl, $TSLA, /ceg
        is_ticker_shorthand = False
        shorthand_ticker = None
        if (base_cmd.startswith("/") or base_cmd.startswith("$")) and cmd_token not in RESERVED_COMMANDS:
            if re.match(r"^[a-z]{1,6}$", cmd_token) and (len(parts) == 1 or (len(parts) == 2 and parts[1].lower() in ("deepdive", "analysis", "dive", "report"))):
                is_ticker_shorthand = True
                shorthand_ticker = cmd_token.upper()

        # -------------------------------------------------------------
        # 1. Multi-User Dynamic Resolution & Linking
        # -------------------------------------------------------------
        user = self.identity_service.resolve_user_from_telegram(chat_id=chat_id, username=username)

        # Unlinked user onboarding guidance
        if not user:
            display_user = username or "your_username"
            logger.info(f"Unregistered Telegram interaction from @{username} (Chat ID: {chat_id})")
            link_help = (
                f"👋 <b>Welcome to Financial Sentinel, {user_name}!</b>\n\n"
                f"This private AI terminal provides real-time portfolio risk governance, stock deep dives, and scheduled intelligence.\n\n"
                f"<b>To link this chat to your account:</b>\n"
                f"1. Open your web console: <code>https://financial-sentinel-272533633552.us-central1.run.app</code>\n"
                f"2. Log in or create your user account.\n"
                f"3. In <b>⚙️ Settings</b>, enter your Telegram username (<code>@{display_user}</code>).\n"
                f"4. Once saved, send <code>/portfolio</code> or <code>/help</code> here to activate your account!"
            )
            self.send_message(link_help, chat_id)
            return

        user_id = user["id"]
        user_role = user.get("role", "user")
        user_gemini_key = self.identity_service.resolve_api_key(user_id)

        # Auto-update active chat_id for user
        if chat_id and user.get("telegram_chat_id") != chat_id:
            self.identity_service.link_telegram_chat_id(user_id, chat_id)
            if user_role == "admin":
                self.chat_id = chat_id
                config.telegram_chat_id = chat_id
                self.telegram_channel.persist_chat_id(chat_id)

        # -------------------------------------------------------------
        # 2. Command Handlers
        # -------------------------------------------------------------
        if base_cmd in ("/start", "/help", "help", "/menu", "menu", "/hello", "hello", "/hi", "hi", "/hey", "hey"):
            help_text = (
                f"👋 <b>Welcome to Financial Sentinel, {user_name}!</b>\n\n"
                f"<b>Daily Market Briefings (PST):</b>\n"
                f"🌅 <b>/premarket</b> — 6:30 AM PST Pre-Market & Opening Catalysts\n"
                f"☀️ <b>/midmarket</b> — 10:00 AM PST Mid-Market Momentum & Fed Pulse\n"
                f"🌙 <b>/postmarket</b> — 3:00 PM PST Post-Market Wrap, Earnings & Hot Movers\n"
                f"🌟 <b>/weekend</b> — 9:00 PM PST Weekend Macro & Week-Ahead Preview\n"
                f"📅 <b>/earnings</b> — This week's corporate earnings calendar & sentiment\n"
                f"⏰ <b>/schedule</b> — View automated notification timeline\n\n"
                f"<b>Portfolio Management & Execution:</b>\n"
                f"➕ <b>/add [ticker] [shares] @ [price]</b> — Add shares or open a position (e.g. <code>/add NVDA 1 @ 123</code>)\n"
                f"🗑️ <b>/rm [ticker] [shares]</b> — Trim or remove a holding (e.g. <code>/rm NVDA 1</code> or <code>/rm NVDA</code>)\n"
                f"💼 <b>/portfolio</b> — Dynamic live holdings, equity, and PnL\n"
                f"💰 <b>/cash</b> — Deployable cash reserves & dry powder\n"
                f"⚡ <b>/scan</b> — Multi-agent risk & opportunity discovery cycle\n\n"
                f"<b>Alpha Discovery & Stock Deep Dives:</b>\n"
                f"🔬 <b>/[ticker]</b> or <b>/analysis [ticker]</b> — Institutional deep dive & Buy/Hold verdict (e.g. <code>/NVDA</code>, <code>/AAPL</code>)\n"
                f"🔭 <b>/opportunity</b> — Top market opportunities tailored to your portfolio\n"
                f"🚀 <b>/moonshots</b> — High-asymmetry deep tech alpha radar\n\n"
                f"💬 <i>Or ask any market/portfolio question directly!</i>"
            )
            self.send_message(help_text, chat_id)

        elif base_cmd in ("/schedule", "schedule", "/times", "times"):
            from datetime import datetime
            from zoneinfo import ZoneInfo
            now_pst = datetime.now(ZoneInfo("America/Los_Angeles"))
            time_str = now_pst.strftime("%I:%M %p %Z")
            date_str = now_pst.strftime("%a, %b %d")
            schedule_msg = (
                f"📅 <b>AUTOMATED DAILY INTELLIGENCE SCHEDULE</b>\n"
                f"<i>Timezone: America/Los_Angeles (PST/PDT)</i>\n"
                f"Current Time: <code>{time_str} ({date_str})</code>\n\n"
                f"<b>Weekdays (Mon – Fri):</b>\n"
                f"• <b>06:30 AM PST</b>: 🌅 Pre-Market Intelligence & Opening Catalysts\n"
                f"• <b>10:00 AM PST</b>: ☀️ Mid-Market Macro & Momentum Pulse\n"
                f"• <b>03:00 PM PST</b>: 🌙 Post-Market Earnings & Hot Movers Wrap\n\n"
                f"<b>Weekends (Sat & Sun):</b>\n"
                f"• <b>09:00 PM PST</b>: 🌟 Weekend Macro & Week-Ahead Preview\n\n"
                f"💡 <i>All briefings are delivered automatically or can be triggered on demand.</i>"
            )
            self.send_message(schedule_msg, chat_id)

        elif base_cmd in ("/premarket", "premarket", "pre-market", "/morning", "morning", "/briefing", "briefing", "/today", "today", "/daily", "daily"):
            if not user_gemini_key:
                self.send_message(
                    "🔒 <b>Gemini API Key Required:</b>\n\nPlease add your personal Gemini API key in Dashboard Settings to generate AI pre-market briefings.",
                    chat_id
                )
                return
            self.send_message("🌅 <i>Generating 6:30 AM PST Pre-Market Intelligence Briefing...</i>", chat_id)
            try:
                self.briefing_service.generate_briefing("premarket", target_chat_id=chat_id, user_id=user_id, auto_dispatch=True)
            except Exception as e:
                self.send_message(f"⚠️ Pre-market generation error: {e}", chat_id)

        elif base_cmd in ("/midmarket", "midmarket", "mid-market", "/midday", "midday"):
            if not user_gemini_key:
                self.send_message(
                    "🔒 <b>Gemini API Key Required:</b>\n\nPlease add your personal Gemini API key in Dashboard Settings to generate AI mid-market briefings.",
                    chat_id
                )
                return
            self.send_message("☀️ <i>Generating 10:00 AM PST Mid-Market Momentum Pulse...</i>", chat_id)
            try:
                self.briefing_service.generate_briefing("midmarket", target_chat_id=chat_id, user_id=user_id, auto_dispatch=True)
            except Exception as e:
                self.send_message(f"⚠️ Mid-market generation error: {e}", chat_id)

        elif base_cmd in ("/postmarket", "postmarket", "post-market", "eod", "/eod", "/close", "close"):
            if not user_gemini_key:
                self.send_message(
                    "🔒 <b>Gemini API Key Required:</b>\n\nPlease add your personal Gemini API key in Dashboard Settings to generate AI post-market briefings.",
                    chat_id
                )
                return
            self.send_message("🌙 <i>Generating 3:00 PM PST Post-Market Wrap & Hot Movers...</i>", chat_id)
            try:
                self.briefing_service.generate_briefing("postmarket", target_chat_id=chat_id, user_id=user_id, auto_dispatch=True)
            except Exception as e:
                self.send_message(f"⚠️ Post-market generation error: {e}", chat_id)

        elif base_cmd in ("/weekend", "weekend", "/sunday", "sunday", "/weekendeod", "weekendeod"):
            if not user_gemini_key:
                self.send_message(
                    "🔒 <b>Gemini API Key Required:</b>\n\nPlease add your personal Gemini API key in Dashboard Settings to generate AI weekend previews.",
                    chat_id
                )
                return
            self.send_message("🌟 <i>Generating 9:00 PM PST Weekend Macro & Week-Ahead Preview...</i>", chat_id)
            try:
                self.briefing_service.generate_briefing("weekend", target_chat_id=chat_id, user_id=user_id, auto_dispatch=True)
            except Exception as e:
                self.send_message(f"⚠️ Weekend briefing error: {e}", chat_id)

        elif base_cmd in ("/earnings", "earnings", "earning", "calls", "/calls"):
            if not user_gemini_key:
                self.send_message(
                    "🔒 <b>Gemini API Key Required:</b>\n\nPlease add your personal Gemini API key in Dashboard Settings to generate AI earnings intelligence reports.",
                    chat_id
                )
                return
            self.send_message("📅 <i>Compiling this week's scheduled corporate earnings calls and market sentiment...</i>", chat_id)
            try:
                self.briefing_service.generate_briefing("earnings", target_chat_id=chat_id, user_id=user_id, auto_dispatch=True)
            except Exception as e:
                self.send_message(f"⚠️ Earnings report error: {e}", chat_id)

        elif base_cmd in ("/opportunity", "/opportunities", "opportunity", "opportunities", "/opp", "opp", "/alpha", "alpha"):
            if not user_gemini_key:
                self.send_message(
                    "🔒 <b>Gemini API Key Required:</b>\n\nPlease add your personal Gemini API key in Dashboard Settings to discover AI alpha opportunities.\n\n<i>Your key is AES-256 encrypted at rest.</i>",
                    chat_id
                )
                return
            self.send_message("🔭 <i>Scanning real-time market opportunities & cross-sector alpha tailored to your portfolio...</i>", chat_id)
            try:
                portfolio = self.orchestrator.get_active_portfolio(user_id=user_id)
                news_items = self.orchestrator.news_agent.ingest_all_feeds()
                opps = self.orchestrator.opportunity_agent.scan_opportunities(news_items, portfolio, api_key=user_gemini_key)

                if not opps:
                    self.send_message("No high-conviction asymmetric opportunities surfaced at this time.", chat_id)
                    return

                tot_eq = portfolio.total_equity()
                cash_pct = (portfolio.cash / tot_eq * 100.0) if tot_eq > 0 else 0.0

                lines = [
                    "🚀 <b>HIGH-CONVICTION MARKET OPPORTUNITIES</b>",
                    f"<i>Tailored to your {len(portfolio.holdings)} holdings and ${portfolio.cash:,.2f} ({cash_pct:.1f}%) deployable cash</i>\n"
                ]
                for opp in opps[:3]:
                    horizon_val = opp.horizon.value if hasattr(opp.horizon, 'value') else opp.horizon
                    lines.append(f"• <b>{opp.ticker}</b> — {opp.name} ({opp.sector})")
                    lines.append(f"  <b>Theme:</b> {opp.theme} ({horizon_val})")
                    lines.append(f"  <b>Target:</b> +{opp.estimated_upside_pct:.1f}% Upside | Stop: -{opp.suggested_stop_loss_pct:.1f}% | R/R {opp.asymmetric_ratio}:1")
                    lines.append(f"  <b>Catalyst:</b> {opp.catalyst_description}")
                    lines.append(f"  <b>Portfolio Synergy:</b> {opp.portfolio_synergy}\n")

                first_tick = opps[0].ticker if opps else "VRT"
                lines.append(f"💡 <i>Type <code>/{first_tick}</code> or <code>/analysis {first_tick}</code> for a comprehensive deep dive, fundamental catalysts, and buy recommendation.</i>")
                self.send_message("\n".join(lines), chat_id)
            except Exception as e:
                self.send_message(f"⚠️ Opportunity discovery error: {e}", chat_id)

        elif base_cmd in ("/analysis", "/analyze", "analysis", "analyze", "/ticker", "ticker", "/deepdive", "deepdive"):
            if len(parts) >= 2 and parts[1].strip():
                target_ticker = parts[1].strip().upper().replace("$", "")
                self._execute_ticker_analysis(target_ticker, chat_id, user_id, user_gemini_key)
                return

            if base_cmd in ("/deepdive", "deepdive"):
                self.send_message(
                    "🔬 <b>Deep Dive Usage:</b>\n\n"
                    "Send <code>/&lt;ticker&gt;</code> (e.g. <code>/NVDA</code>, <code>/AAPL</code>) or <code>/deepdive &lt;ticker&gt;</code> to analyze any stock.",
                    chat_id
                )
                return

            if not user_gemini_key:
                self.send_message(
                    "🔒 <b>Gemini API Key Required:</b>\n\nPlease ensure GEMINI_API_KEY is configured on the server or enter your personal key in Dashboard Settings.",
                    chat_id
                )
                return

            status_id = self.send_message_returning_id(
                "📊 <b>Executing Portfolio-Wide AI Analysis... [10%]</b>\n"
                "<i>Updating real-time trade quotes for portfolio holdings...</i>",
                chat_id
            )
            def on_tg_progress(stage: str, percent: int, msg: str):
                if status_id:
                    self.edit_message(f"📊 <b>AI Analysis in Progress [{percent}%]:</b>\n<i>{msg}</i>", chat_id, status_id)

            try:
                portfolio = self.orchestrator.get_active_portfolio(user_id=user_id)
                briefing = self.orchestrator.run_monitoring_cycle(
                    portfolio=portfolio,
                    live=True,
                    force_fresh=True,
                    user_id=user_id,
                    api_key=user_gemini_key,
                    on_progress=on_tg_progress
                )
                tg_msg = self.orchestrator.notification_agent.format_telegram_digest(briefing)
                if status_id and len(tg_msg) < 4000:
                    self.edit_message(tg_msg, chat_id, status_id)
                else:
                    self.send_message(tg_msg, chat_id)
            except Exception as e:
                self.send_message(f"⚠️ Analysis error: {e}", chat_id)

        elif is_ticker_shorthand and shorthand_ticker:
            self._execute_ticker_analysis(shorthand_ticker, chat_id, user_id, user_gemini_key)

        elif base_cmd in ("/scan", "scan", "/runscan", "run scan"):
            if not user_gemini_key:
                self.send_message(
                    "🔒 <b>Gemini API Key Required:</b>\n\nPlease add your personal Gemini API key in Dashboard Settings to run AI multi-agent scans.\n\n<i>Your key is AES-256 encrypted at rest.</i>",
                    chat_id
                )
                return

            status_id = self.send_message_returning_id(
                "⏳ <b>Executing Live Multi-Agent Scan... [10%]</b>\n<i>Pulling real-time market trade quotes...</i>",
                chat_id
            )
            def on_scan_progress(stage: str, percent: int, msg: str):
                if status_id:
                    self.edit_message(f"⏳ <b>Live Multi-Agent Scan [{percent}%]:</b>\n<i>{msg}</i>", chat_id, status_id)

            try:
                portfolio = self.orchestrator.get_active_portfolio(user_id=user_id)
                briefing = self.orchestrator.run_monitoring_cycle(
                    portfolio=portfolio,
                    live=True,
                    force_fresh=True,
                    user_id=user_id,
                    api_key=user_gemini_key,
                    on_progress=on_scan_progress
                )
                msg = self.orchestrator.notification_agent.format_telegram_message(briefing)
                if status_id and len(msg) < 4000:
                    self.edit_message(msg, chat_id, status_id)
                else:
                    self.send_message(msg, chat_id)
            except Exception as e:
                self.send_message(f"⚠️ Scan error: {e}", chat_id)

        elif base_cmd in ("/portfolio", "portfolio", "holdings", "/holdings", "/positions", "positions", "/stocks", "stocks"):
            try:
                portfolio = self.orchestrator.get_active_portfolio(user_id=user_id)
                update_portfolio_live_prices(portfolio)
                tot_eq = portfolio.total_equity()
                holdings_val = sum(h.market_value for h in portfolio.holdings)
                cash_pct = (portfolio.cash / tot_eq * 100.0) if tot_eq > 0 else 0.0

                lines = [
                    "💼 <b>ACTIVE PORTFOLIO SUMMARY</b>",
                    f"Total Equity: <b>${tot_eq:,.2f}</b>",
                    f"Holdings Value: <b>${holdings_val:,.2f}</b> ({len(portfolio.holdings)} Assets)",
                    f"💰 Deployable Cash: <b>${portfolio.cash:,.2f}</b> ({cash_pct:.1f}% Dry Powder)\n"
                ]
                for h in portfolio.holdings:
                    pnl_sign = "+" if h.unrealized_pnl_pct >= 0 else ""
                    lines.append(f"• <b>{h.ticker}</b>: ${h.current_price:.2f} ({h.weight_pct:.1f}% | {pnl_sign}{h.unrealized_pnl_pct:.1f}%)")
                self.send_message("\n".join(lines), chat_id)
            except Exception as e:
                self.send_message(f"⚠️ Portfolio error: {e}", chat_id)

        elif base_cmd in ("/cash", "cash", "/balance", "balance"):
            try:
                val = self.portfolio_service.get_portfolio_valuation(user_id=user_id, update_prices=True)
                portfolio = val["portfolio"]
                tot_eq = val["total_equity"]
                cash_pct = (portfolio.cash / tot_eq * 100.0) if tot_eq > 0 else 0.0
                msg = (
                    f"💰 <b>DEPLOYABLE CASH & CAPITAL POSTURE</b>\n\n"
                    f"• Deployable Cash: <b>${portfolio.cash:,.2f}</b> ({cash_pct:.1f}% Dry Powder)\n"
                    f"• Portfolio Equity: <b>${tot_eq:,.2f}</b> across {len(portfolio.holdings)} holdings"
                )
                self.send_message(msg, chat_id)
            except Exception as e:
                self.send_message(f"⚠️ Cash balance error: {e}", chat_id)

        elif base_cmd in ("/status", "status", "/ping", "ping"):
            try:
                portfolio = self.orchestrator.get_active_portfolio(user_id=user_id)
                msg = (
                    f"🟢 <b>FINANCIAL SENTINEL TERMINAL: OPERATIONAL</b>\n\n"
                    f"• <b>Account:</b> {user_name} ({user_role.upper()})\n"
                    f"• <b>Active Holdings:</b> {len(portfolio.holdings)} assets\n"
                    f"• <b>AI Core:</b> Gemini 3.8 Flash {'(Connected)' if user_gemini_key else '(API Key Required)'}\n"
                    f"• <b>Telegram Webhook:</b> Active\n\n"
                    f"<i>Send /help to view all intelligence commands.</i>"
                )
                self.send_message(msg, chat_id)
            except Exception as e:
                self.send_message(f"⚠️ Status check error: {e}", chat_id)

        elif base_cmd in ("/moonshots", "moonshots", "moonshot", "/moonshot"):
            self.send_message("🔭 <b>Scanning High-Asymmetry Moonshots...</b>", chat_id)
            try:
                portfolio = self.orchestrator.get_active_portfolio(user_id=user_id)
                moonshots = self.orchestrator.opportunity_agent.discover_moonshot_opportunities(
                    portfolio, count=3, api_key=user_gemini_key
                )

                if not moonshots:
                    self.send_message("No moonshots surfaced at this time.", chat_id)
                    return

                lines = ["🚀 <b>HIGH-ASYMMETRY MOONSHOT RADAR</b>\n"]
                for m in moonshots:
                    lines.append(f"• <b>{m.ticker}</b> ({m.name})")
                    lines.append(f"  Theme: {m.theme}")
                    lines.append(f"  🎯 Target: <b>+{m.estimated_upside_pct}%</b> | R/R {m.asymmetric_ratio}:1")
                    lines.append(f"  Thesis: {m.upside_thesis}")
                    if m.risk_factors:
                        risks_str = ', '.join(m.risk_factors[:2])
                        lines.append(f"  ⚠️ Risk: {risks_str}\n")
                self.send_message("\n".join(lines), chat_id)
            except Exception as e:
                self.send_message(f"⚠️ Moonshot error: {e}", chat_id)

        elif base_cmd in ("/add", "add", "/buy", "buy"):
            args = parse_add_args(clean_text)
            if not args or not args.get("ticker"):
                help_add = (
                    "➕ <b>ADD / BUY HOLDING USAGE:</b>\n\n"
                    "Syntax: <code>/add &lt;ticker&gt; &lt;shares&gt; @ &lt;price&gt;</code>\n\n"
                    "<b>Examples:</b>\n"
                    "• <code>/add NVDA 1 @ 123</code> (Adds 1 share of NVDA at $123)\n"
                    "• <code>/add AAPL 10 @ 225.50</code>\n"
                    "• <code>/add TSLA 5</code> (Uses current market quote)\n\n"
                    "<i>If you already own the ticker, it automatically averages your cost basis and increases your share count.</i>"
                )
                self.send_message(help_add, chat_id)
                return

            target_ticker = args["ticker"].upper().replace("$", "")
            shares_to_add = args["shares"] or 1.0
            purchase_price = args["price"]

            if shares_to_add <= 0:
                self.send_message("⚠️ Shares to add must be greater than 0.", chat_id)
                return

            self.send_message(f"⏳ <i>Adding {shares_to_add:g} share(s) of {target_ticker} to your active holdings...</i>", chat_id)
            try:
                res = self.portfolio_service.add_or_update_holding(
                    user_id=user_id,
                    ticker=target_ticker,
                    shares=shares_to_add,
                    price=purchase_price,
                    incremental=True,
                    deduct_cash=True
                )
                holding_ref = res["holding"]
                portfolio = self.portfolio_service.get_portfolio(user_id=user_id)
                tot_eq = portfolio.total_equity()
                pnl = (holding_ref.current_price - holding_ref.avg_price) * holding_ref.shares
                pnl_pct = ((holding_ref.current_price - holding_ref.avg_price) / holding_ref.avg_price * 100) if holding_ref.avg_price > 0 else 0
                pnl_sign = "+" if pnl >= 0 else ""

                if res["action"] == "updated":
                    action_desc = f"Added <b>+{shares_to_add:g}</b> share(s) @ <b>${purchase_price or holding_ref.avg_price:.2f}</b> to existing position"
                else:
                    action_desc = f"Opened new position with <b>{shares_to_add:g}</b> share(s) @ <b>${holding_ref.avg_price:.2f}</b>"

                msg = (
                    f"✅ <b>HOLDING UPDATED SUCCESSFULLY</b>\n\n"
                    f"• <b>Ticker:</b> <code>{holding_ref.ticker}</code> ({holding_ref.name})\n"
                    f"• <b>Sector:</b> {holding_ref.sector}\n"
                    f"• <b>Action:</b> {action_desc}\n"
                    f"• <b>Total Position:</b> <b>{holding_ref.shares:g} shares</b>\n"
                    f"• <b>Avg Cost Basis:</b> ${holding_ref.avg_price:.2f}\n"
                    f"• <b>Current Price:</b> ${holding_ref.current_price:.2f}\n"
                    f"• <b>Market Value:</b> ${holding_ref.market_value:,.2f} ({holding_ref.weight_pct:.1f}% of portfolio)\n"
                    f"• <b>Unrealized PnL:</b> {pnl_sign}${pnl:,.2f} ({pnl_sign}{pnl_pct:.1f}%)\n\n"
                    f"💼 <b>Updated Portfolio:</b> <b>${tot_eq:,.2f}</b> across {len(portfolio.holdings)} holdings | 💰 Cash: <b>${portfolio.cash:,.2f}</b>"
                )
                self.send_message(msg, chat_id)
            except Exception as e:
                self.send_message(f"⚠️ Failed to add {target_ticker}: {e}", chat_id)

        elif base_cmd in ("/rm", "rm", "/remove", "remove", "/sell", "sell", "/del", "del"):
            args = parse_rm_args(clean_text)
            if not args or not args.get("ticker"):
                help_rm = (
                    "🗑️ <b>REMOVE / SELL HOLDING USAGE:</b>\n\n"
                    "Syntax: <code>/rm &lt;ticker&gt; [shares] [@ price]</code>\n\n"
                    "<b>Examples:</b>\n"
                    "• <code>/rm NVDA</code> (Removes entire NVDA position)\n"
                    "• <code>/rm NVDA all</code> (Removes entire NVDA position)\n"
                    "• <code>/rm NVDA 1</code> (Trims 1 share of NVDA)\n"
                    "• <code>/rm AAPL 5 @ 230</code> (Trims 5 shares at $230)\n\n"
                    "<i>If you specify fewer shares than you own, your remaining shares stay active with the same cost basis.</i>"
                )
                self.send_message(help_rm, chat_id)
                return

            target_ticker = args["ticker"].upper().replace("$", "")
            shares_to_rm = args["shares"]
            exit_price = args["price"]

            self.send_message(f"⏳ <i>Processing removal for {target_ticker}...</i>", chat_id)
            try:
                res = self.portfolio_service.remove_or_trim_holding(
                    user_id=user_id,
                    ticker=target_ticker,
                    shares_to_remove=shares_to_rm,
                    exit_price=exit_price,
                    credit_cash=True
                )
                portfolio = self.portfolio_service.get_portfolio(user_id=user_id)
                tot_eq = portfolio.total_equity()

                if res["is_full_removal"]:
                    remaining_info = "<i>Position is no longer in active holdings.</i>"
                else:
                    existing_holding = next(h for h in portfolio.holdings if h.ticker.upper() == target_ticker)
                    remaining_info = (
                        f"• <b>Remaining Shares:</b> <b>{existing_holding.shares:g} shares</b>\n"
                        f"• <b>Avg Cost Basis:</b> ${existing_holding.avg_price:.2f}\n"
                        f"• <b>Market Value:</b> ${existing_holding.market_value:,.2f}"
                    )

                msg = (
                    f"🗑️ <b>HOLDING UPDATED / REMOVED</b>\n\n"
                    f"• <b>Ticker:</b> <code>{target_ticker}</code> ({res['company_name']})\n"
                    f"• <b>Action:</b> {res['action_desc']}\n"
                    f"{remaining_info}\n\n"
                    f"💼 <b>Updated Portfolio:</b> <b>${tot_eq:,.2f}</b> across {len(portfolio.holdings)} holdings | 💰 Cash: <b>${portfolio.cash:,.2f}</b>"
                )
                self.send_message(msg, chat_id)
            except ValueError as ve:
                portfolio = self.portfolio_service.get_portfolio(user_id=user_id)
                available = ', '.join([h.ticker for h in portfolio.holdings])
                self.send_message(f"❌ <b>{ve}</b>\nActive holdings: <code>{available}</code>", chat_id)
            except Exception as e:
                self.send_message(f"⚠️ Failed to remove {target_ticker}: {e}", chat_id)

        elif base_cmd.startswith("/"):
            unrecog = parts[0]
            self.send_message(
                f"⚠️ <b>Unrecognized Command:</b> <code>{unrecog}</code>\n\n"
                f"• <b>Stock Deep Dive:</b> Send <code>/&lt;ticker&gt;</code> (e.g. <code>/NVDA</code>, <code>/AAPL</code>, <code>/TSLA</code>)\n"
                f"• <b>Portfolio Summary:</b> <code>/portfolio</code>\n"
                f"• <b>Cash & Dry Powder:</b> <code>/cash</code>\n"
                f"• <b>Multi-Agent Scan:</b> <code>/scan</code>\n"
                f"• <b>Full Command Menu:</b> <code>/help</code>",
                chat_id
            )

        else:
            # AI Chat Question
            if not user_gemini_key:
                self.send_message(
                    "🔒 <b>Gemini API Key Required:</b>\n\nPlease add your personal Gemini API key in Dashboard Settings to chat with the AI Portfolio Analyst.\n\n<i>Your key is AES-256 encrypted at rest.</i>",
                    chat_id
                )
                return

            try:
                portfolio = self.orchestrator.get_active_portfolio(user_id=user_id)
                holdings_str = ', '.join([f"{h.ticker} ({h.weight_pct}%, ${h.current_price})" for h in portfolio.holdings])
                context = f"Portfolio Equity: ${portfolio.total_equity():,.2f}. Holdings: {holdings_str}"
                sys_inst = f"You are the Lead Portfolio Manager for this investor. Context: {context}. Be concise, institutional, and direct. Format with clean HTML/text."

                reply = self.orchestrator.notification_agent.query_llm_text(
                    prompt=f"Investor Question: {text}\nAnswer:",
                    system_instruction=sys_inst,
                    api_key=user_gemini_key
                )
                if not reply:
                    reply = f"I evaluated your inquiry regarding your ${portfolio.total_equity():,.2f} portfolio. What specific risk or hedge detail would you like to model?"
                self.send_message(f"🧠 <b>AI Portfolio Analyst:</b>\n\n{reply}", chat_id)
            except Exception as e:
                self.send_message(f"⚠️ Chat error: {e}", chat_id)
