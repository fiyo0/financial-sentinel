"""
services/briefing_service.py - Unified Market Briefings & Scheduling Service.
Coordinates premarket, midmarket, postmarket, weekend, and earnings briefings,
multi-tenant isolation, Telegram dispatch, and archive pruning.
"""
import logging
from typing import Optional, Dict, Any, List
from orchestrator import FinancialSentinelOrchestrator
from channels.telegram import TelegramChannel

logger = logging.getLogger(__name__)


class BriefingService:
    def __init__(
        self,
        orchestrator: FinancialSentinelOrchestrator,
        scheduler: Optional[Any] = None
    ):
        self.orchestrator = orchestrator
        self._scheduler = scheduler
        self.telegram = TelegramChannel()

    @property
    def scheduler(self):
        if self._scheduler is None:
            from scheduler import DailyMarketScheduler
            self._scheduler = DailyMarketScheduler(self.orchestrator)
        return self._scheduler

    def generate_briefing(
        self,
        slot: str,
        user_id: Optional[str] = None,
        target_chat_id: Optional[str] = None,
        auto_dispatch: bool = True,
        force: bool = False
    ) -> str:
        """Generates a scheduled or on-demand market briefing for a specific slot."""
        return self.scheduler.execute_briefing(
            slot,
            target_chat_id,
            user_id,
            auto_dispatch,
            force
        )

    def list_briefings(
        self,
        user_id: Optional[str] = None,
        slot: Optional[str] = None,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Retrieves market briefings respecting tenant isolation."""
        return self.orchestrator.state_store.get_market_briefings(
            slot=slot,
            user_id=user_id,
            limit=limit
        )

    def get_briefing(self, report_id: str) -> Optional[Dict[str, Any]]:
        """Fetches a specific briefing report by ID."""
        return self.orchestrator.state_store.get_market_briefing_by_id(report_id)

    def dispatch_briefing(self, report_id: str, chat_id: Optional[str] = None) -> bool:
        """Dispatches an archived briefing report to Telegram."""
        briefing = self.get_briefing(report_id)
        if not briefing:
            raise ValueError(f"Briefing report {report_id} not found.")

        message = briefing.get("message_html") or briefing.get("executive_summary") or briefing.get("message")
        if not message:
            raise ValueError(f"Briefing report {report_id} contains no dispatchable content.")

        target = chat_id or self.telegram.get_effective_chat_id()
        if not target:
            raise ValueError("No recipient Telegram chat ID configured.")

        self.telegram.send_message(message, chat_id=target)
        return True

    def prune_briefings(self, retention_days: int = 30) -> int:
        """Prunes historical briefings beyond the retention horizon."""
        return self.orchestrator.state_store.prune_briefings(retention_days=retention_days)
