"""
services/analysis_service.py - Unified Single-Ticker & Multi-Agent Analysis Service.
Coordinates live quotes, technical momentum, retail sentiment, targeted news,
Gemini 3.8 structured thinking, and deep dive persistence.
Used identically across Web API, Telegram Bot, and CLI.
"""
import logging
from dataclasses import asdict
from typing import Optional, Dict, Any, Callable
import analytics.market_data as market_data
import analytics.technical_indicators as technical_indicators
import analytics.sentiment_stream as sentiment_stream
from orchestrator import FinancialSentinelOrchestrator

logger = logging.getLogger(__name__)


class AnalysisService:
    def __init__(self, orchestrator: FinancialSentinelOrchestrator):
        self.orchestrator = orchestrator

    def run_single_ticker_analysis(
        self,
        ticker: str,
        user_id: Optional[str] = None,
        api_key: Optional[str] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Executes an institutional single-ticker analysis with data provenance and multi-source context.
        Supports an optional progress callback: (percent: int, message: str) -> None.
        """
        resolved_key = api_key or self.orchestrator.resolve_user_api_key(user_id)
        if not resolved_key:
            raise ValueError(
                "Gemini API Key Required: Please configure GEMINI_API_KEY on the server or provide your BYOK key."
            )

        clean_sym = ticker.strip().upper().replace("$", "")
        if not clean_sym:
            raise ValueError("Ticker symbol cannot be empty.")

        # Step 1: Live Quote
        if progress_callback:
            progress_callback(20, f"Fetching real-time market quote and order flow for {clean_sym}...")
        try:
            quote = market_data.fetch_live_quote(clean_sym)
        except Exception as e:
            logger.warning(f"Error fetching live quote for {clean_sym}: {e}")
            quote = {}

        current_price = float(quote.get("current_price") or quote.get("price") or 0.0)
        portfolio = self.orchestrator.get_active_portfolio(user_id=user_id)
        is_portfolio_holding = any(h.ticker.upper() == clean_sym for h in portfolio.holdings)

        if current_price <= 0.0 and not is_portfolio_holding:
            raise ValueError(
                f"Ticker '{clean_sym}' not recognized: Unable to verify live trade data."
            )

        # Step 2: Technical Momentum
        if progress_callback:
            progress_callback(40, f"Computing verified technical momentum (RSI, MACD, Bollinger Bands) for {clean_sym}...")
        tech_snap = technical_indicators.compute_technical_snapshot(clean_sym)
        if current_price <= 0.0 and tech_snap and tech_snap.current_price:
            current_price = float(tech_snap.current_price or 0.0)

        # Step 3: Retail Sentiment & RVOL
        if progress_callback:
            progress_callback(60, f"Ingesting StockTwits & Reddit retail sentiment velocity for {clean_sym}...")
        rvol_val = tech_snap.rvol if (tech_snap and tech_snap.is_live) else None
        sent_snap = sentiment_stream.fetch_social_sentiment_snapshot(clean_sym, rvol=rvol_val)

        # Step 4: News Feeds
        news_items = self.orchestrator.news_agent.ingest_all_feeds(
            live=True, portfolio_tickers=[clean_sym], api_key=resolved_key
        )

        # Step 5: Gemini Structured Thinking
        if progress_callback:
            progress_callback(80, f"Gemini 3.8 deep thinking: evaluating fundamental moats & catalysts for {clean_sym}...")

        analysis_obj = self.orchestrator.analysis_agent.analyze_single_ticker_structured(
            ticker=clean_sym,
            portfolio=portfolio,
            news_items=news_items,
            quote_data=quote,
            technical_snapshot=tech_snap,
            sentiment_snapshot=sent_snap,
            api_key=resolved_key
        )

        company_name = quote.get("name") or clean_sym
        verdict = analysis_obj.verdict
        conviction_score = analysis_obj.conviction_score
        analysis_text = analysis_obj.telegram_html

        # Enforce deterministic data quality ceiling on conviction score (R-4)
        from analytics.quant_risk import QuantRiskEngine, apply_data_quality_ceiling, compute_deterministic_position_size
        stress_metrics = QuantRiskEngine().analyze_portfolio(portfolio) if portfolio else None
        capped_conviction, ceiling_reasons = apply_data_quality_ceiling(
            conviction_pct=conviction_score,
            tech_snapshot=tech_snap,
            stress_metrics=stress_metrics
        )
        if capped_conviction != conviction_score:
            logger.info(f"Data quality ceiling applied to {clean_sym}: {conviction_score}% -> {capped_conviction}% ({ceiling_reasons})")
            conviction_score = capped_conviction

        # Compute institutional volatility- and liquidity-adjusted deterministic position size (§2B)
        atr_14_val = getattr(tech_snap, "atr_14", None) if tech_snap else None
        median_adv_val = float(quote.get("volume", 0.0) or 0.0)
        deterministic_sizing = compute_deterministic_position_size(
            portfolio_equity=portfolio.total_equity(),
            portfolio_cash=portfolio.cash,
            current_price=current_price,
            atr_14=atr_14_val,
            conviction_pct=conviction_score,
            median_adv_shares_30d=median_adv_val,
        )

        deepdive_payload = {
            "status": "success",
            "ticker": clean_sym,
            "company_name": company_name,
            "current_price": current_price,
            "quote": quote,
            "technicals": asdict(tech_snap) if tech_snap else None,
            "sentiment": asdict(sent_snap) if sent_snap else None,
            "analysis": analysis_text,
            "verdict": verdict,
            "conviction_score": conviction_score,
            "thesis": analysis_obj.thesis,
            "catalysts": analysis_obj.catalysts,
            "risks": analysis_obj.risks,
            "target_price": analysis_obj.target_price,
            "stop_floor": analysis_obj.stop_floor,
            "suggested_allocation_usd": analysis_obj.suggested_allocation_usd,
            "data_quality_ceiling_applied": capped_conviction != analysis_obj.conviction_score,
            "data_quality_reasons": ceiling_reasons,
            "deterministic_sizing": deterministic_sizing,
        }

        # Step 6: State persistence in user's Deep Dive archive
        deepdive_id = None
        try:
            deepdive_id = self.orchestrator.state_store.save_deepdive(
                user_id=user_id,
                ticker=clean_sym,
                company_name=company_name,
                current_price=current_price,
                verdict=verdict,
                conviction_score=conviction_score,
                technicals=asdict(tech_snap) if tech_snap else None,
                sentiment=asdict(sent_snap) if sent_snap else None,
                analysis_text=analysis_text,
                payload_json=deepdive_payload
            )
            deepdive_payload["deepdive_id"] = deepdive_id
        except Exception as e:
            logger.error(f"Failed to persist deepdive analysis for {clean_sym}: {e}")

        # Step 7: Record thesis outcome into ground truth ledger for forward return tracking (§4A)
        if deepdive_id:
            try:
                self.orchestrator.state_store.record_thesis_outcome(
                    thesis_id=str(deepdive_id),
                    user_id=user_id or "default_user",
                    ticker=clean_sym,
                    stance=verdict,
                    conviction_pct=conviction_score,
                    entry_price=current_price,
                    critic_verdict=None,
                    critic_conf_pct=None,
                )
            except Exception as e:
                logger.error(f"Failed to record thesis outcome for {clean_sym}: {e}")

        if progress_callback:
            progress_callback(100, f"Analysis complete for {clean_sym}.")

        deepdive_payload["structured"] = analysis_obj
        return deepdive_payload
