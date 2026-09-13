"""
Orchestrator: Coordinates the full lifecycle across all 5 agents, quantitative analytics, state persistence, and dispatch.
"""
import os
import json
import csv
import logging
from typing import Optional, List, Dict, Any
from models import Portfolio, PortfolioHolding, BriefingReport
from storage.state_store import StateStore
from analytics.quant_risk import QuantRiskEngine
from analytics.token_budget import TokenBudgetManager
from analytics.market_data import update_portfolio_live_prices
from agents.news_ingestion import NewsIngestionAgent
from agents.analysis_agent import PortfolioAnalysisAgent
from agents.opportunity_agent import OpportunityDiscoveryAgent
from agents.critic_agent import RiskCriticAgent
from agents.notification_agent import NotificationAgent
from config import config

logger = logging.getLogger(__name__)


class FinancialSentinelOrchestrator:
    def __init__(self, db_path: Optional[str] = None):
        self.state_store = StateStore(db_path or config.db_path)
        self.token_manager = TokenBudgetManager(
            self.state_store,
            daily_token_limit=config.daily_token_limit,
            max_tokens_per_cycle=config.max_tokens_per_cycle
        )
        self.quant_engine = QuantRiskEngine(config.max_sector_concentration_pct)
        self.news_agent = NewsIngestionAgent(self.state_store)
        self.analysis_agent = PortfolioAnalysisAgent(state_store=self.state_store)
        self.opportunity_agent = OpportunityDiscoveryAgent(state_store=self.state_store)
        self.critic_agent = RiskCriticAgent(state_store=self.state_store)
        self.notification_agent = NotificationAgent(state_store=self.state_store)

    def load_portfolio_from_file(self, file_path: str) -> Portfolio:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Portfolio file not found: {file_path}")

        if file_path.endswith(".json"):
            with open(file_path, "r") as f:
                data = json.load(f)
                return Portfolio(**data)
        elif file_path.endswith(".csv"):
            holdings = []
            with open(file_path, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    holdings.append(PortfolioHolding(
                        ticker=row["ticker"].strip().upper(),
                        name=row.get("name", row["ticker"]).strip(),
                        shares=float(row.get("shares", 0)),
                        avg_price=float(row.get("avg_price", 0)),
                        current_price=float(row.get("current_price", row.get("avg_price", 0))),
                        sector=row.get("sector", "Technology").strip(),
                        thematic_tags=[t.strip() for t in row.get("thematic_tags", "").split(",") if t.strip()]
                    ))
            p = Portfolio(name="Imported CSV Portfolio", cash=0.0, holdings=holdings)
            p.recalculate_weights()
            return p
        else:
            raise ValueError("Supported portfolio formats: .json, .csv")

    def get_active_portfolio(self, user_id: Optional[str] = None) -> Portfolio:
        """
        Multi-tenant portfolio loader:
        1. If user_id provided, loads user-specific portfolio from user_portfolios table.
        2. If user_id is None, loads primary Admin portfolio.
        3. Fallback to sample_portfolio.json only for unauthenticated/initial global state.
        """
        if user_id:
            user_p = self.state_store.get_user_portfolio(user_id)
            if user_p and isinstance(user_p, dict):
                try:
                    p = Portfolio.model_validate(user_p)
                    p.deduplicate_and_aggregate()
                    return p
                except Exception:
                    pass
            # Fresh empty user portfolio (starts off with nothing)
            p = Portfolio(name="User Portfolio", cash=0.0, holdings=[])
            return p


        # Default admin flow
        admin = self.state_store.get_or_create_default_admin()
        if admin:
            user_p = self.state_store.get_user_portfolio(admin["id"])
            if user_p and isinstance(user_p, dict) and user_p.get("holdings"):
                try:
                    p = Portfolio.model_validate(user_p)
                    p.deduplicate_and_aggregate()
                    return p
                except Exception:
                    pass

        saved = self.state_store.get_kv("active_portfolio")
        if saved and isinstance(saved, dict) and saved.get("holdings"):
            try:
                p = Portfolio.model_validate(saved)
                p.deduplicate_and_aggregate()
                return p
            except Exception:
                pass

        data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        sample_json = os.path.join(data_dir, "sample_portfolio.json")
        if os.path.exists(sample_json):
            try:
                p = self.load_portfolio_from_file(sample_json)
                p.deduplicate_and_aggregate()
                return p
            except Exception:
                pass

        p = Portfolio(name="Default Portfolio", cash=10000.0, holdings=[])
        p.deduplicate_and_aggregate()
        return p


    def persist_active_portfolio(self, portfolio: Portfolio, user_id: Optional[str] = None) -> Dict[str, Any]:
        portfolio.deduplicate_and_aggregate()
        dumped = portfolio.model_dump(mode="json")

        admin = self.state_store.get_or_create_default_admin()
        admin_id = admin["id"] if admin else None

        target_uid = user_id or admin_id

        if target_uid:
            self.state_store.save_user_portfolio(target_uid, dumped, cash=portfolio.cash)

        # Global sync ONLY for admin / default user for cold start backups
        if target_uid == admin_id or not user_id:
            self.state_store.set_kv("active_portfolio", dumped)
            self.state_store.set_kv("portfolio_cash", portfolio.cash)

            data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
            active_json = os.path.join(data_dir, "active_portfolio.json")
            os.makedirs(data_dir, exist_ok=True)
            try:
                with open(active_json, "w") as f:
                    json.dump(dumped, f, indent=2)
            except Exception:
                pass
        return dumped


    def resolve_user_api_key(self, user_id: Optional[str] = None) -> str:
        """
        Resolves the Gemini API key for a specific user.
        Strict isolation: Regular users ONLY use their own decrypted key.
        Admins (by role, username, or allowed Telegram identity) fall back to the system config.gemini_api_key.
        """
        from auth.crypto import decrypt_api_key

        if not user_id:
            admin = self.state_store.get_or_create_default_admin()
            if admin and admin.get("encrypted_gemini_key"):
                return decrypt_api_key(admin["encrypted_gemini_key"])
            return config.gemini_api_key

        user = self.state_store.get_user_by_id(user_id)
        if not user:
            return ""

        if user.get("encrypted_gemini_key"):
            dec = decrypt_api_key(user["encrypted_gemini_key"])
            if dec:
                return dec


        # Check if user is admin or matches allowed admin telegram identifiers
        allowed_tg = [u.lower().replace("@", "") for u in config.telegram_allowed_usernames if u]
        user_tg = (user.get("telegram_username") or "").lower().replace("@", "")
        user_chat = str(user.get("telegram_chat_id") or "").strip()
        user_name = (user.get("username") or "").lower().strip()
        is_allowed_admin = (
            user.get("role") == "admin"
            or (user_tg and user_tg in allowed_tg)
            or (user_chat and user_chat in config.telegram_allowed_chat_ids)
            or (user_name and user_name in allowed_tg)
        )

        if is_allowed_admin:
            return config.gemini_api_key

        # Non-admin user with no key: Return empty string (never fallback to admin key!)
        return ""



    def run_monitoring_cycle(
        self,
        portfolio: Portfolio,
        live: bool = True,
        mock_news: Optional[List[Dict[str, Any]]] = None,
        force_fresh: bool = False,
        user_id: Optional[str] = None,
        api_key: Optional[str] = None,
        auto_dispatch: bool = True,
        async_dispatch: bool = True,
        on_progress: Optional[Any] = None
    ) -> BriefingReport:
        def _emit_progress(stage: str, percent: int, msg: str):
            if on_progress and callable(on_progress):
                try:
                    import inspect
                    sig = inspect.signature(on_progress)
                    param_count = len(sig.parameters)
                    if param_count >= 3:
                        on_progress(stage, percent, msg)
                    elif param_count == 2:
                        on_progress(msg, percent)
                    else:
                        on_progress(msg)
                except Exception:
                    pass

        # Resolve active BYOK Gemini key
        active_key = api_key if api_key is not None else self.resolve_user_api_key(user_id)

        # Step 1: Configure all agents with active user BYOK key
        if active_key:
            self.news_agent.api_key = active_key
            self.news_agent.use_llm = True
            self.analysis_agent.api_key = active_key
            self.analysis_agent.use_llm = True
            self.opportunity_agent.api_key = active_key
            self.opportunity_agent.use_llm = True
            self.critic_agent.api_key = active_key
            self.critic_agent.use_llm = True
            self.notification_agent.api_key = active_key
            self.notification_agent.use_llm = True
        else:
            self.news_agent.use_llm = False
            self.analysis_agent.use_llm = False
            self.opportunity_agent.use_llm = False
            self.critic_agent.use_llm = False
            self.notification_agent.use_llm = False

        # Step 2: Concurrently refresh live market prices and ingest real-time news/filings
        _emit_progress(
            "market_data",
            15,
            f"Concurrently updating trade quotes for {len(portfolio.holdings)} holdings and ingesting market feeds/SEC filings..."
        )
        portfolio_tickers = [h.ticker for h in portfolio.holdings]
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=2) as pre_executor:
            fut_quotes = pre_executor.submit(update_portfolio_live_prices, portfolio) if live else None
            fut_news = pre_executor.submit(
                self.news_agent.ingest_all_feeds,
                live=live,
                custom_items=mock_news,
                force_fresh=force_fresh,
                portfolio_tickers=portfolio_tickers,
                api_key=active_key
            )
            if fut_quotes:
                fut_quotes.result()
            news_items = fut_news.result()

        portfolio.recalculate_weights()
        stress_metrics = self.quant_engine.analyze_portfolio(portfolio)
        _emit_progress("news_ingestion", 35, f"Market data refreshed & {len(news_items)} real-time news feeds ingested.")

        _emit_progress(
            "analysis_opportunity",
            45,
            f"Gemini 3.8 deep thinking: evaluating direct catalysts & supply chain ripple effects across {len(news_items)} news items..."
        )
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as executor:
            fut_analysis = executor.submit(self.analysis_agent.analyze_news_against_portfolio, news_items or [], portfolio, api_key=active_key)
            fut_opportunity = executor.submit(self.opportunity_agent.scan_opportunities, news_items or [], portfolio, api_key=active_key)
            risk_analyses = fut_analysis.result()
            opportunities = fut_opportunity.result()

        # Step 5: Risk & Critic Agent
        _emit_progress(
            "critic_audit",
            75,
            f"Adversarial Risk Critic stress-testing {len(risk_analyses)} risk theses & {len(opportunities)} alpha proposals..."
        )
        critic_reviews = self.critic_agent.audit_all_batch(risk_analyses, opportunities, api_key=active_key)

        # Step 6: Notification Agent (Synthesis)
        _emit_progress(
            "briefing_synthesis",
            90,
            "Synthesizing Chief Investment Officer executive briefing & final portfolio risk posture..."
        )
        briefing = self.notification_agent.generate_briefing(
            risk_analyses=risk_analyses,
            opportunities=opportunities,
            critic_reviews=critic_reviews,
            portfolio_stress=stress_metrics,
            total_holdings_monitored=len(portfolio.holdings),
            raw_news_count=len(news_items),
            api_key=active_key
        )

        # Step 7: Persist Briefing in State Store immediately (guarantees persistence before external calls)
        self.state_store.save_briefing(briefing)
        if user_id:
            self.state_store.record_user_scan(user_id, briefing.report_id, briefing.model_dump(mode="json"))

        _emit_progress("complete", 100, "Analysis complete! Finalizing briefing report...")

        # Step 8: Dispatch across active channels (non-blocking in background by default)
        if auto_dispatch:
            if async_dispatch:
                import threading
                def _bg_dispatch():
                    try:
                        self.notification_agent.dispatch_briefing(briefing)
                    except Exception as e:
                        logger.error(f"Error during async notification dispatch: {e}")
                threading.Thread(target=_bg_dispatch, daemon=True).start()
            else:
                self.notification_agent.dispatch_briefing(briefing)

        return briefing

