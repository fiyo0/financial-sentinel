"""
Scheduler & Daemon Runner for continuous financial portfolio monitoring
and scheduled daily market intelligence briefings.
"""
import time
import signal
import sys
import threading
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Callable, Set

from orchestrator import FinancialSentinelOrchestrator
from models import Portfolio
from agents.market_briefing_agent import MarketBriefingAgent
from analytics.market_data import fetch_market_overview, fetch_market_movers, update_portfolio_live_prices
from channels.telegram import TelegramChannel
from config import config

logger = logging.getLogger("Scheduler")


class DailyMarketScheduler:
    """
    Automated PST Timezone-Aware Daily Market & Opportunity Briefing Scheduler.
    - Weekdays (Mon-Fri): 06:30 PST (Pre-Market), 10:00 PST (Mid-Market), 15:00 PST (Post-Market Hot Movers)
    - Weekends (Sat-Sun): 21:00 PST (Weekend Macro & Week-Ahead Preview)
    100% dynamic holdings resolution on every execution.
    """

    def __init__(
        self,
        orchestrator: Optional[FinancialSentinelOrchestrator] = None,
        portfolio_loader: Optional[Callable[[], Portfolio]] = None,
        timezone_str: str = "America/Los_Angeles"
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
        slot_key = f"{date_str}_{slot}"

        # Deduplicate automated scheduled cron triggers to avoid double-pushing
        if not user_id and not force:
            saved_slots = self.orchestrator.state_store.get_kv("executed_schedule_slots") or []
            if slot_key in saved_slots or slot_key in self._executed_slots:
                logger.info(f"Slot {slot_key} already executed today. Skipping duplicate dispatch.")
                return f"Slot {slot_key} already dispatched today."
            self._mark_slot_executed(slot_key)

        market_overview = fetch_market_overview()
        fresh_news = self.orchestrator.news_agent.ingest_all_feeds(force_fresh=True)
        stored_news = self.orchestrator.state_store.get_recent_news(hours=24)
        seen_hashes = set()
        combined_news = []
        for n in (fresh_news + stored_news):
            h = getattr(n, "raw_hash", None) or n.id
            if h not in seen_hashes:
                seen_hashes.add(h)
                combined_news.append(n)
        combined_news.sort(key=lambda x: x.published_at if x.published_at else datetime.min, reverse=True)
        news_items = combined_news

        movers = fetch_market_movers() if slot == "postmarket" else []

        # Single user on-demand generation
        if user_id:
            portfolio = self.orchestrator.get_active_portfolio(user_id=user_id)
            portfolio, _ = update_portfolio_live_prices(portfolio)
            api_key = self.orchestrator.resolve_user_api_key(user_id)

            if slot == "premarket":
                message = self.briefing_agent.generate_premarket_briefing(portfolio, market_overview, news_items, api_key=api_key, as_of=now_pst)
            elif slot == "midmarket":
                message = self.briefing_agent.generate_midmarket_briefing(portfolio, market_overview, news_items, api_key=api_key, as_of=now_pst)
            elif slot == "postmarket":
                message = self.briefing_agent.generate_postmarket_briefing(portfolio, market_overview, news_items, movers, api_key=api_key, as_of=now_pst)
            elif slot == "weekend":
                message = self.briefing_agent.generate_weekend_eod_briefing(portfolio, market_overview, news_items, api_key=api_key, as_of=now_pst)
            elif slot == "earnings":
                message = self.briefing_agent.generate_weekly_earnings_briefing(portfolio, news_items, api_key=api_key, as_of=now_pst)
            else:
                message = self.briefing_agent.generate_premarket_briefing(portfolio, market_overview, news_items, api_key=api_key, as_of=now_pst)

            briefing_id = f"briefing_{slot}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
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
                u_portfolio = self.orchestrator.get_active_portfolio(user_id=u_id)
                u_portfolio, _ = update_portfolio_live_prices(u_portfolio)

                if slot == "premarket":
                    u_msg = self.briefing_agent.generate_premarket_briefing(u_portfolio, market_overview, news_items, api_key=u_key, as_of=now_pst)
                elif slot == "midmarket":
                    u_msg = self.briefing_agent.generate_midmarket_briefing(u_portfolio, market_overview, news_items, api_key=u_key, as_of=now_pst)
                elif slot == "postmarket":
                    u_msg = self.briefing_agent.generate_postmarket_briefing(u_portfolio, market_overview, news_items, movers, api_key=u_key, as_of=now_pst)
                elif slot == "weekend":
                    u_msg = self.briefing_agent.generate_weekend_eod_briefing(u_portfolio, market_overview, news_items, api_key=u_key, as_of=now_pst)
                elif slot == "earnings":
                    u_msg = self.briefing_agent.generate_weekly_earnings_briefing(u_portfolio, news_items, api_key=u_key, as_of=now_pst)
                else:
                    u_msg = self.briefing_agent.generate_premarket_briefing(u_portfolio, market_overview, news_items, api_key=u_key, as_of=now_pst)


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
                        briefing_id=f"daily_{slot}_{u_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
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
                    briefing_id=f"daily_{slot}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
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
                now_pst = datetime.now(self.tz)
                hour = now_pst.hour
                minute = now_pst.minute
                weekday = now_pst.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun
                date_str = now_pst.strftime("%Y-%m-%d")

                # Weekday Schedule (Mon-Fri)
                if weekday < 5:
                    # 1. 06:30 AM PST: Pre-Market (30-min grace window: 06:30 - 07:15)
                    if (hour == 6 and minute >= 30) or (hour == 7 and minute <= 15):
                        slot_key = f"{date_str}_premarket"
                        if slot_key not in self._executed_slots:
                            logger.info("Triggering 6:30 AM PST Pre-Market Briefing...")
                            self.execute_briefing("premarket")
                            self._mark_slot_executed(slot_key)

                    # 2. 10:00 AM PST: Mid-Market (45-min grace window: 10:00 - 10:45)
                    elif hour == 10 and minute <= 45:
                        slot_key = f"{date_str}_midmarket"
                        if slot_key not in self._executed_slots:
                            logger.info("Triggering 10:00 AM PST Mid-Market Briefing...")
                            self.execute_briefing("midmarket")
                            self._mark_slot_executed(slot_key)

                    # 3. 03:00 PM PST (15:00): Post-Market (45-min grace window: 15:00 - 15:45)
                    elif hour == 15 and minute <= 45:
                        slot_key = f"{date_str}_postmarket"
                        if slot_key not in self._executed_slots:
                            logger.info("Triggering 3:00 PM PST Post-Market Briefing...")
                            self.execute_briefing("postmarket")
                            self._mark_slot_executed(slot_key)

                # Weekend Schedule (Sat & Sun)
                else:
                    # 4. 09:00 PM PST (21:00): Weekend EOD (45-min grace window: 21:00 - 21:45)
                    if hour == 21 and minute <= 45:
                        slot_key = f"{date_str}_weekend"
                        if slot_key not in self._executed_slots:
                            logger.info("Triggering 9:00 PM PST Weekend Briefing...")
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
            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
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

