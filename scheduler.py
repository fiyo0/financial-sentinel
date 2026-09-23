"""
Scheduler & Daemon Runner for continuous financial portfolio monitoring
and scheduled daily market intelligence briefings.
"""
import time
import signal
import sys
import threading
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional, Callable, Set, List, Dict, Any

from orchestrator import FinancialSentinelOrchestrator
from models import Portfolio, NewsItem
from agents.market_briefing_agent import MarketBriefingAgent
from analytics.market_data import fetch_market_overview, fetch_market_movers, update_portfolio_live_prices
from analytics.market_calendar import is_market_holiday
from channels.telegram import TelegramChannel
from config import config

logger = logging.getLogger("Scheduler")


class DailyMarketScheduler:
    """
    Automated Eastern Timezone-Aware Daily Market & Opportunity Briefing Scheduler.
    - Weekdays (Mon-Fri): 09:30 EST (Pre-Market), 13:00 EST (Mid-Market), 16:30 EST (Post-Market Hot Movers)
    - Weekends (Sat-Sun): 21:00 EST (Weekend Macro & Week-Ahead Preview)
    Anchored explicitly to America/New_York to eliminate Daylight Saving Time drift relative to the NYSE bell.
    """

    def __init__(
        self,
        orchestrator: Optional[FinancialSentinelOrchestrator] = None,
        portfolio_loader: Optional[Callable[[], Portfolio]] = None,
        timezone_str: str = "America/New_York"
    ):
        self.orchestrator = orchestrator or FinancialSentinelOrchestrator(config.db_path)
        self.portfolio_loader = portfolio_loader
        self.tz = ZoneInfo(timezone_str)
        self.briefing_agent = MarketBriefingAgent()
        self.telegram = TelegramChannel()
        self.is_running = False
        self._thread: Optional[threading.Thread] = None
        saved_slots = self.orchestrator.state_store.get_kv("executed_schedule_slots") or []
        self._executed_slots: Set[str] = set(saved_slots)

    def get_portfolio(self) -> Portfolio:
        if self.portfolio_loader:
            return self.portfolio_loader()
        return self.orchestrator.get_active_portfolio()

    def start(self):
        """Starts the daily briefing scheduler loop in a background daemon thread."""
        if self.is_running:
            return
        self.is_running = True
        self._thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self._thread.start()
        logger.info(f"DailyMarketScheduler daemon started with timezone {self.tz}.")

    def stop(self):
        self.is_running = False

    def _mark_slot_executed(self, slot_key: str):
        self._executed_slots.add(slot_key)
        try:
            self.orchestrator.state_store.set_kv("executed_schedule_slots", list(self._executed_slots))
        except Exception as e:
            logger.warning("Failed persisting executed schedule slots: %s", e)

    def _prepare_portfolio_news(self, portfolio: Optional[Portfolio], base_news: List[NewsItem]) -> List[NewsItem]:
        """
        Retrieves news specifically mentioning or tagged with the portfolio's holdings,
        and merges it with the base news stream so holding-specific catalysts are prioritized.
        """
        if not portfolio or not getattr(portfolio, "holdings", None):
            return base_news

        try:
            holding_news = self.orchestrator.state_store.get_recent_news_for_portfolio(portfolio, hours=48, limit=30)
        except Exception as e:
            logger.warning("Failed fetching holding-affinity news: %s", e)
            holding_news = []

        seen_hashes = set()
        merged = []
        for n in holding_news:
            h = getattr(n, "raw_hash", None) or n.id
            if h not in seen_hashes:
                seen_hashes.add(h)
                merged.append(n)
        for n in base_news:
            h = getattr(n, "raw_hash", None) or n.id
            if h not in seen_hashes:
                seen_hashes.add(h)
                merged.append(n)
        return merged

    def _get_prior_briefing_context(
        self,
        slot: str,
        user_id: Optional[str] = None,
        as_of: Optional[datetime] = None
    ) -> Optional[str]:
        """
        Retrieves executive summaries from today's earlier briefings to maintain cross-briefing narrative continuity.
        For midmarket: retrieves today's premarket briefing.
        For postmarket: retrieves today's premarket and midmarket briefings.
        """
        if slot not in ("midmarket", "postmarket"):
            return None

        ref_dt = as_of or datetime.now(self.tz)
        today_prefix = ref_dt.strftime("%Y-%m-%d")

        briefings = []
        try:
            if user_id:
                briefings = self.orchestrator.state_store.get_market_briefings(user_id=user_id, limit=20)
            if not briefings:
                briefings = self.orchestrator.state_store.get_market_briefings(user_id=None, limit=20)
        except Exception as e:
            logger.warning("Failed retrieving market briefings for prior context: %s", e)
            return None

        if not briefings:
            return None

        today_briefings = []
        for b in briefings:
            gen_str = b.get("generated_at") or ""
            if gen_str.startswith(today_prefix):
                today_briefings.append(b)
                continue
            try:
                raw_val = gen_str.strip()
                if raw_val.endswith("Z"):
                    raw_val = raw_val[:-1] + "+00:00"
                dt = datetime.fromisoformat(raw_val)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
                if 0 <= age_h <= 18:
                    today_briefings.append(b)
            except Exception:
                pass

        if not today_briefings:
            return None

        premarket_b = next((b for b in today_briefings if b.get("slot") == "premarket"), None)
        midmarket_b = next((b for b in today_briefings if b.get("slot") == "midmarket"), None)

        def _clean_summary(b: Dict[str, Any]) -> str:
            msg = b.get("executive_summary") or b.get("message_html") or ""
            return msg.strip()[:400]

        if slot == "midmarket" and premarket_b:
            summary = _clean_summary(premarket_b)
            if summary:
                return f"TODAY'S PRE-MARKET GAMEPLAN & CATALYST OUTLOOK:\n{summary}"

        if slot == "postmarket":
            parts = []
            if premarket_b:
                pm_sum = _clean_summary(premarket_b)
                if pm_sum:
                    parts.append(f"• Pre-Market Gameplan: {pm_sum}")
            if midmarket_b:
                mm_sum = _clean_summary(midmarket_b)
                if mm_sum:
                    parts.append(f"• Mid-Market Momentum & Catalyst Digestion: {mm_sum}")
            if parts:
                return "\n".join(parts)

        return None

    def execute_briefing(
        self,
        slot: str,
        target_chat_id: Optional[str] = None,
        user_id: Optional[str] = None,
        auto_dispatch: bool = True,
        force: bool = False
    ) -> str:
        """
        Executes a specific briefing slot dynamically (premarket, midmarket, postmarket, weekend, earnings).
        Supports multi-user personalized briefings with BYOK Gemini key resolution.
        """
        now_pst = datetime.now(self.tz)
        date_str = now_pst.strftime("%Y-%m-%d")
        today_date = now_pst.date()
        slot_key = f"{date_str}_{slot}"

        # Suppress intraday market briefings on official market holidays unless explicitly forced
        if slot in ("premarket", "midmarket", "postmarket") and not force and is_market_holiday(today_date):
            logger.info(f"Skipping {slot} briefing on market holiday {date_str} (NYSE closed).")
            return f"Skipped {slot} briefing on market holiday {date_str} (NYSE closed)."

        # Deduplicate automated scheduled cron triggers to avoid double-pushing
        if not user_id and not force:
            saved_slots = self.orchestrator.state_store.get_kv("executed_schedule_slots") or []
            if slot_key in saved_slots or slot_key in self._executed_slots:
                logger.info(f"Slot {slot_key} already executed today. Skipping duplicate dispatch.")
                return f"Slot {slot_key} already dispatched today."
            self._mark_slot_executed(slot_key)

        market_overview = fetch_market_overview()
        try:
            from analytics.market_data import fetch_live_quote
            cross_assets = {}
            for sym in ("TLT", "UUP", "GLD", "USO"):
                q = fetch_live_quote(sym)
                if q and q.get("current_price") is not None:
                    cross_assets[sym] = q
            market_overview["cross_assets"] = cross_assets

            sector_spdrs = {}
            for sym in ("XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLRE", "XLC", "XLB"):
                q = fetch_live_quote(sym)
                if q and q.get("change_pct") is not None:
                    sector_spdrs[sym] = q
            market_overview["sector_spdrs"] = sector_spdrs
        except Exception as e:
            logger.debug("Failed enriching market overview: %s", e)

        # Compute deterministic technical snapshots for SPY and QQQ
        technical_snapshots = {}
        try:
            from analytics.technical_indicators import compute_technical_snapshot
            for sym in ("SPY", "QQQ"):
                snap = compute_technical_snapshot(sym)
                if snap:
                    technical_snapshots[sym] = snap
        except Exception as e:
            logger.debug("Failed computing technical snapshot: %s", e)

        # Fetch today's earnings schedule for premarket (BMO) and postmarket (AMC)
        earnings_calendar = []
        try:
            from analytics.earnings_calendar import fetch_earnings_for_date
            all_today_earnings = fetch_earnings_for_date(date_str)
            if slot == "premarket":
                earnings_calendar = [e for e in all_today_earnings if "BMO" in e.get("timing", "")]
            elif slot == "postmarket":
                earnings_calendar = [e for e in all_today_earnings if "AMC" in e.get("timing", "")]
            else:
                earnings_calendar = all_today_earnings
        except Exception as e:
            logger.warning("Failed fetching earnings for date %s: %s", date_str, e)
            earnings_calendar = []

        fresh_news = self.orchestrator.news_agent.ingest_all_feeds(force_fresh=True)
        stored_news = self.orchestrator.state_store.get_recent_news(hours=24)
        seen_hashes = set()
        combined_news = []
        for n in (fresh_news + stored_news):
            h = getattr(n, "raw_hash", None) or n.id
            if h not in seen_hashes:
                seen_hashes.add(h)
                combined_news.append(n)
        def _safe_pub_time(it: NewsItem) -> datetime:
            dt = getattr(it, "published_at", None)
            if not dt:
                return datetime.min.replace(tzinfo=timezone.utc)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt

        combined_news.sort(key=_safe_pub_time, reverse=True)
        news_items = combined_news

        movers = fetch_market_movers() if slot == "postmarket" else []

        # Single user on-demand generation
        if user_id:
            portfolio = self.portfolio_loader() if self.portfolio_loader else self.orchestrator.get_active_portfolio(user_id=user_id)
            portfolio, _ = update_portfolio_live_prices(portfolio)
            api_key = self.orchestrator.resolve_user_api_key(user_id)
            user_news = self._prepare_portfolio_news(portfolio, news_items)
            prior_context = self._get_prior_briefing_context(slot, user_id=user_id, as_of=now_pst)

            if slot == "premarket":
                message = self.briefing_agent.generate_premarket_briefing(
                    portfolio, market_overview, user_news, api_key=api_key, as_of=now_pst,
                    technical_snapshots=technical_snapshots, earnings_calendar=earnings_calendar,
                    prior_briefing_context=prior_context
                )
            elif slot == "midmarket":
                message = self.briefing_agent.generate_midmarket_briefing(
                    portfolio, market_overview, user_news, api_key=api_key, as_of=now_pst,
                    technical_snapshots=technical_snapshots, prior_briefing_context=prior_context
                )
            elif slot == "postmarket":
                message = self.briefing_agent.generate_postmarket_briefing(
                    portfolio, market_overview, user_news, movers, api_key=api_key, as_of=now_pst,
                    technical_snapshots=technical_snapshots, earnings_calendar=earnings_calendar,
                    prior_briefing_context=prior_context
                )
            elif slot == "weekend":
                message = self.briefing_agent.generate_weekend_eod_briefing(portfolio, market_overview, user_news, api_key=api_key, as_of=now_pst)
            elif slot == "earnings":
                message = self.briefing_agent.generate_weekly_earnings_briefing(portfolio, user_news, api_key=api_key, as_of=now_pst)
            else:
                message = self.briefing_agent.generate_premarket_briefing(
                    portfolio, market_overview, user_news, api_key=api_key, as_of=now_pst,
                    technical_snapshots=technical_snapshots, earnings_calendar=earnings_calendar,
                    prior_briefing_context=prior_context
                )

            briefing_id = f"briefing_{slot}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
            dispatched = []
            if auto_dispatch and target_chat_id:
                try:
                    self.telegram.send_message(message, chat_id=target_chat_id)
                    dispatched.append("telegram")
                except Exception as e:
                    logger.error(f"Failed to dispatch to {target_chat_id}: {e}")

            try:
                self.orchestrator.state_store.record_market_briefing(
                    briefing_id=briefing_id,
                    slot=slot,
                    message=message,
                    user_id=user_id,
                    dispatched_channels=dispatched,
                    extra_payload={"total_holdings": len(portfolio.holdings)}
                )
            except Exception as e:
                logger.error(f"Failed to record on-demand briefing in state_store: {e}")

            return message

        # Multi-user automated cron dispatch (Google Cloud Scheduler)
        active_users = self.orchestrator.state_store.get_all_active_telegram_users()
        if not active_users:
            admin = self.orchestrator.state_store.get_or_create_default_admin()
            active_users = [admin] if admin else []

        last_generated_msg = ""
        effective_fallback_chat = self.telegram.get_effective_chat_id() or config.telegram_chat_id
        dispatched_chats = set()

        for u in active_users:
            u_id = u["id"]
            u_chat = u.get("telegram_chat_id") or effective_fallback_chat
            if not u_chat or u_chat in dispatched_chats:
                continue

            u_role = u.get("role", "user")
            u_key = self.orchestrator.resolve_user_api_key(u_id)

            # Skip non-admin users without a Gemini key for AI briefings
            if u_role != "admin" and not u_key:
                logger.info(f"Skipping automated AI briefing for user {u['username']} (No BYOK Gemini key)")
                continue

            try:
                u_portfolio = self.portfolio_loader() if self.portfolio_loader else self.orchestrator.get_active_portfolio(user_id=u_id)
                u_portfolio, _ = update_portfolio_live_prices(u_portfolio)
                u_news = self._prepare_portfolio_news(u_portfolio, news_items)
                u_prior_context = self._get_prior_briefing_context(slot, user_id=u_id, as_of=now_pst)

                if slot == "premarket":
                    u_msg = self.briefing_agent.generate_premarket_briefing(
                        u_portfolio, market_overview, u_news, api_key=u_key, as_of=now_pst,
                        technical_snapshots=technical_snapshots, earnings_calendar=earnings_calendar,
                        prior_briefing_context=u_prior_context
                    )
                elif slot == "midmarket":
                    u_msg = self.briefing_agent.generate_midmarket_briefing(
                        u_portfolio, market_overview, u_news, api_key=u_key, as_of=now_pst,
                        technical_snapshots=technical_snapshots, prior_briefing_context=u_prior_context
                    )
                elif slot == "postmarket":
                    u_msg = self.briefing_agent.generate_postmarket_briefing(
                        u_portfolio, market_overview, u_news, movers, api_key=u_key, as_of=now_pst,
                        technical_snapshots=technical_snapshots, earnings_calendar=earnings_calendar,
                        prior_briefing_context=u_prior_context
                    )
                elif slot == "weekend":
                    u_msg = self.briefing_agent.generate_weekend_eod_briefing(u_portfolio, market_overview, u_news, api_key=u_key, as_of=now_pst)
                elif slot == "earnings":
                    u_msg = self.briefing_agent.generate_weekly_earnings_briefing(u_portfolio, u_news, api_key=u_key, as_of=now_pst)
                else:
                    u_msg = self.briefing_agent.generate_premarket_briefing(
                        u_portfolio, market_overview, u_news, api_key=u_key, as_of=now_pst,
                        technical_snapshots=technical_snapshots, earnings_calendar=earnings_calendar,
                        prior_briefing_context=u_prior_context
                    )


                last_generated_msg = u_msg
                dispatched_tg = False
                if auto_dispatch and u_chat:
                    logger.info(f"Dispatching {slot} briefing to Telegram chat {u_chat} for user {u.get('username')}")
                    self.telegram.send_message(u_msg, chat_id=u_chat)
                    dispatched_chats.add(u_chat)
                    dispatched_tg = True
                else:
                    logger.warning(f"Briefing generated for {u.get('username')}, but no Telegram chat ID was available.")

                # Persist personalized market briefing in state store for this user
                try:
                    self.orchestrator.state_store.record_market_briefing(
                        briefing_id=f"daily_{slot}_{u_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
                        slot=slot,
                        message=u_msg,
                        user_id=u_id,
                        dispatched_channels=["telegram"] if dispatched_tg else [],
                        extra_payload={"total_holdings": len(u_portfolio.holdings) if u_portfolio else 0}
                    )
                except Exception as e:
                    logger.error(f"Failed to record automated briefing for user {u.get('username')}: {e}")

            except Exception as e:
                logger.error(f"Error generating briefing for user {u.get('username')}: {e}")

        # Persist fallback / global market briefing in state store ONLY if no active users were processed
        if last_generated_msg and not active_users:
            try:
                self.orchestrator.state_store.record_market_briefing(
                    briefing_id=f"daily_{slot}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
                    slot=slot,
                    message=last_generated_msg,
                    user_id=None,
                    dispatched_channels=["telegram"] if dispatched_chats else [],
                    extra_payload={"active_users_count": 0}
                )
            except Exception as e:
                logger.warning(f"Failed to record daily fallback briefing to state store: {e}")

        # Auto-prune duplicate or expired briefings (older than 30 days) to keep archive clean
        try:
            self.orchestrator.state_store.prune_briefings(retention_days=30)
        except Exception as e:
            logger.debug(f"Briefing retention prune note: {e}")

        return last_generated_msg


    def _scheduler_loop(self):
        while self.is_running:
            try:
                now_ny = datetime.now(ZoneInfo("America/New_York"))
                hour = now_ny.hour
                minute = now_ny.minute
                weekday = now_ny.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun
                date_str = now_ny.strftime("%Y-%m-%d")

                today_date = now_ny.date()
                is_holiday = is_market_holiday(today_date)
                is_weekend = weekday >= 5

                # Active Trading Day Schedule (Mon-Fri and NOT an official market holiday)
                if not is_weekend and not is_holiday:
                    # 1. 09:30 AM EST/EDT: Pre-Market Bell (45-min grace window: 09:30 - 10:15)
                    if (hour == 9 and minute >= 30) or (hour == 10 and minute <= 15):
                        slot_key = f"{date_str}_premarket"
                        if slot_key not in self._executed_slots:
                            logger.info("Triggering 9:30 AM EST Pre-Market Briefing...")
                            self.execute_briefing("premarket")
                            self._mark_slot_executed(slot_key)

                    # 2. 01:00 PM EST/EDT (13:00): Mid-Market (45-min grace window: 13:00 - 13:45)
                    elif hour == 13 and minute <= 45:
                        slot_key = f"{date_str}_midmarket"
                        if slot_key not in self._executed_slots:
                            logger.info("Triggering 1:00 PM EST Mid-Market Briefing...")
                            self.execute_briefing("midmarket")
                            self._mark_slot_executed(slot_key)

                    # 3. 04:30 PM EST/EDT (16:30): Post-Market Wrap (45-min grace window: 16:30 - 17:15)
                    elif (hour == 16 and minute >= 30) or (hour == 17 and minute <= 15):
                        slot_key = f"{date_str}_postmarket"
                        if slot_key not in self._executed_slots:
                            logger.info("Triggering 4:30 PM EST Post-Market Briefing...")
                            self.execute_briefing("postmarket")
                            self._mark_slot_executed(slot_key)

                # Non-Trading Day Schedule (Weekend OR Market Holiday)
                else:
                    # 4. 09:00 PM EST/EDT (21:00): Weekend / Holiday EOD (45-min grace window: 21:00 - 21:45)
                    if hour == 21 and minute <= 45:
                        slot_key = f"{date_str}_weekend"
                        if slot_key not in self._executed_slots:
                            slot_desc = "Holiday" if is_holiday else "Weekend"
                            logger.info(f"Triggering 9:00 PM EST {slot_desc} Briefing...")
                            self.execute_briefing("weekend")
                            self._mark_slot_executed(slot_key)

                # Clear old slots periodically (keep last 14 days)
                if len(self._executed_slots) > 60:
                    self._executed_slots = {s for s in self._executed_slots if date_str in s}
                    try:
                        self.orchestrator.state_store.set_kv("executed_schedule_slots", list(self._executed_slots))
                    except Exception as e:
                        logger.warning("Failed updating pruned schedule slots: %s", e)

            except Exception as e:
                logger.error(f"Error in DailyMarketScheduler loop: {e}")

            time.sleep(20)  # Check every 20 seconds


class MonitoringScheduler:
    def __init__(
        self,
        orchestrator: FinancialSentinelOrchestrator,
        portfolio: Portfolio,
        interval_seconds: int = 900  # Default 15 minutes
    ):
        self.orchestrator = orchestrator
        self.portfolio = portfolio
        self.interval_seconds = interval_seconds
        self.running = False

    def start(self):
        self.running = True
        print(f"🚀 Financial Sentinel Scheduler started. Interval: {self.interval_seconds}s")
        print(f"💼 Monitored Portfolio: {self.portfolio.name} ({len(self.portfolio.holdings)} holdings)")

        def handle_stop(signum, frame):
            print("\n🛑 Stopping Financial Sentinel Scheduler...")
            self.running = False
            sys.exit(0)

        signal.signal(signal.SIGINT, handle_stop)
        signal.signal(signal.SIGTERM, handle_stop)

        cycle_count = 0
        while self.running:
            cycle_count += 1
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            print(f"\n[{now_str}] 🔄 Executing Monitoring Cycle #{cycle_count}...")

            try:
                briefing = self.orchestrator.run_monitoring_cycle(self.portfolio, live=True)
                print(f"✅ Cycle #{cycle_count} Completed.")
                print(f"   Processed {briefing.raw_news_count} news items.")
                print(f"   P0 Critical Alerts: {len(briefing.critical_risk_alerts)}")
                print(f"   P1 Notable Alerts: {len(briefing.notable_risk_alerts)}")
                print(f"   Top Opportunities: {len(briefing.top_opportunities)}")
                if briefing.dispatched_channels:
                    print(f"   Dispatched via: {', '.join(briefing.dispatched_channels)}")
            except Exception as e:
                print(f"⚠️ Error during monitoring cycle #{cycle_count}: {e}")

            time.sleep(self.interval_seconds)

