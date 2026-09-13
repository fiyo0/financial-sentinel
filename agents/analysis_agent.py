"""
Portfolio Analysis Agent: Evaluates directional risks, fundamental catalysts,
and supply-chain ripple effects dynamically using Gemini 3.8 Flash.
Zero hardcoded ecosystem dictionaries.
"""

from typing import List, Optional, Dict, Any
from models import (
    Portfolio, PortfolioHolding, NewsItem, NewsCategory, HoldingExposureAnalysis,
    DirectionalImpact, AlertPriority
)
from agents.base_agent import BaseAgent
from config import config


class PortfolioAnalysisAgent(BaseAgent):
    def __init__(self, state_store: Optional[Any] = None):
        super().__init__(
            name="Portfolio Analysis Agent",
            role_description="Evaluates directional risks, fundamental catalysts, and ecosystem exposures for all portfolio holdings.",
            state_store=state_store
        )

    def analyze_news_against_portfolio(
        self,
        news_items: List[NewsItem],
        portfolio: Portfolio,
        api_key: Optional[str] = None
    ) -> List[HoldingExposureAnalysis]:
        portfolio.recalculate_weights()
        effective_key = api_key or self.api_key
        if not effective_key:
            raise ValueError("Gemini API key is required to analyze portfolio exposure and risk. Please add your key in Settings.")

        batch_results = self._llm_batch_analyze_portfolio(portfolio, news_items, api_key=effective_key)
        if batch_results is None:
            raise RuntimeError("Gemini Portfolio Risk Analysis failed. Please verify API key status.")

        return batch_results


    def _llm_batch_analyze_portfolio(
        self,
        portfolio: Portfolio,
        news_items: List[NewsItem],
        api_key: Optional[str] = None
    ) -> Optional[List[HoldingExposureAnalysis]]:
        holdings_summary = []
        for h in portfolio.holdings:
            holdings_summary.append({
                "ticker": h.ticker,
                "name": h.name,
                "sector": h.sector,
                "shares": h.shares,
                "avg_price": h.avg_price,
                "current_price": h.current_price,
                "unrealized_pnl_pct": f"{h.unrealized_pnl_pct:.1f}%",
                "weight_pct": f"{h.weight_pct:.1f}%"
            })

        news_summary = []
        for n in news_items[:12]:
            news_summary.append({
                "title": n.title,
                "source": n.source,
                "summary": n.summary[:200],
                "tickers": n.related_tickers
            })

        prompt = f"""
        You are an elite quantitative & fundamental equity portfolio risk analyst.
        Analyze this active portfolio holdings list and recent market news feeds:

        PORTFOLIO HOLDINGS:
        {holdings_summary}

        RECENT MARKET NEWS & FILINGS:
        {news_summary}

        TASK:
        For EACH holding in the portfolio:
        1. Dynamically evaluate direct news catalysts, macro pressures, and 1st/2nd order ecosystem supply-chain ripple effects.
        2. Assign directional impact (BULLISH, BEARISH, HIGH_VOLATILITY, NEUTRAL) and estimated price volatility magnitude %.
        3. Formulate actionable portfolio advice (e.g. trailing stop levels, profit targets, or position sizing).

        Return JSON matching this exact schema:
        {{
            "analyses": [
                {{
                    "holding_ticker": "NVDA",
                    "holding_name": "NVIDIA Corporation",
                    "impact": "BULLISH" | "BEARISH" | "HIGH_VOLATILITY" | "NEUTRAL",
                    "impact_magnitude_pct": 3.5,
                    "priority": "P0_CRITICAL" | "P1_NOTABLE" | "P2_WATCHLIST",
                    "transmission_channel": "Direct News" | "Macro Multiple" | "Supply Chain Ripple",
                    "rationale": "Detailed fundamental assessment citing catalysts and market dynamics",
                    "key_risks": ["Risk factor 1", "Risk factor 2"],
                    "recommended_action": "Clear actionable recommendation with price thresholds"
                }}
            ]
        }}
        """
        effective_key = api_key or self.api_key
        res = self.query_llm_json(prompt, api_key=effective_key)
        if not res or "analyses" not in res:
            return None

        analyses: List[HoldingExposureAnalysis] = []
        for item in res.get("analyses", []):
            try:
                impact_str = str(item.get("impact", "BULLISH")).upper()
                if impact_str not in [e.value for e in DirectionalImpact]:
                    impact_str = "BULLISH"
                priority_str = str(item.get("priority", "P1_NOTABLE")).upper()
                if priority_str not in [e.value for e in AlertPriority]:
                    priority_str = "P1_NOTABLE"

                ticker_sym = item.get("holding_ticker") or item.get("ticker") or ""
                name_val = item.get("holding_name") or item.get("name") or ticker_sym

                analyses.append(HoldingExposureAnalysis(
                    holding_ticker=ticker_sym,
                    holding_name=name_val,
                    impact=DirectionalImpact(impact_str),
                    impact_magnitude_pct=float(item.get("impact_magnitude_pct", 2.5)),
                    priority=AlertPriority(priority_str),
                    direct_exposure=True,
                    transmission_channel=item.get("transmission_channel", "Gemini 3.8 Flash Dynamic Synthesis"),
                    rationale=item.get("rationale", ""),

                    key_risks=item.get("key_risks", []),
                    recommended_action=item.get("recommended_action", "Hold"),
                    citations=[f"Gemini AI Dynamic Synthesis: {ticker_sym}"],
                    news_item_id=f"llm_{ticker_sym}"
                ))
            except Exception:
                continue

        return analyses


    def _synthesize_dynamic_holding_health(self, holding: PortfolioHolding, portfolio: Portfolio) -> HoldingExposureAnalysis:
        cur_price = holding.current_price if holding.current_price > 0 else holding.avg_price
        pnl_pct = holding.unrealized_pnl_pct
        weight_pct = holding.weight_pct
        stop_price = round(cur_price * 0.92, 2)
        target_price = round(cur_price * 1.15, 2)

        if pnl_pct >= 15.0:
            impact = DirectionalImpact.BULLISH
            magnitude = 3.5
            rationale = f"Solid unrealized gain (+{pnl_pct:.1f}%). {holding.name} maintains positive technical momentum."
            action = f"Protect gains: Set trailing stop at ${stop_price:.2f}. Upside target: ${target_price:.2f}."
        elif pnl_pct <= -10.0:
            impact = DirectionalImpact.BEARISH
            magnitude = 3.8
            rationale = f"Drawdown ({pnl_pct:.1f}%). Position under downward multiple pressure."
            action = f"Risk management: Maintain structural stop-loss at ${stop_price:.2f}."
        else:
            impact = DirectionalImpact.BULLISH
            magnitude = 2.4
            rationale = f"Tracking near cost basis ({pnl_pct:+.1f}%). Balanced posture."
            action = f"Maintain core allocation ({weight_pct:.1f}% weight). Objective: ${target_price:.2f}."

        key_risks = [
            f"Key support floor: ${stop_price:.2f} (-8.0% buffer)",
            f"Sector volatility in {holding.sector}"
        ]
        if weight_pct >= 25.0:
            key_risks.append(f"High concentration ({weight_pct:.1f}% of total portfolio)")

        priority = AlertPriority.P0_CRITICAL if (pnl_pct <= -15.0) else (
            AlertPriority.P1_NOTABLE if (weight_pct >= 15.0 or magnitude >= 3.0) else AlertPriority.P2_WATCHLIST
        )

        return HoldingExposureAnalysis(
            holding_ticker=holding.ticker,
            holding_name=holding.name or holding.ticker,
            impact=impact,
            impact_magnitude_pct=magnitude,
            priority=priority,
            direct_exposure=True,
            transmission_channel=f"Dynamic Quantitative Engine ({holding.sector})",
            rationale=rationale,
            key_risks=key_risks,
            recommended_action=action,
            citations=[f"Real-Time Quantitative Positioning: {holding.ticker}"],
            news_item_id=f"quant_{holding.ticker}"
        )

    def analyze_single_ticker(
        self,
        ticker: str,
        portfolio: Portfolio,
        news_items: List[NewsItem],
        quote_data: Optional[Dict[str, Any]] = None,
        technical_snapshot: Optional[Any] = None,
        sentiment_snapshot: Optional[Any] = None,
        api_key: Optional[str] = None
    ) -> str:
        """
        Generates an institutional-grade, comprehensive deep dive analysis of a specific ticker,
        evaluating verified technical momentum indicators, live retail social sentiment,
        fundamental catalysts, portfolio synergy/fit against current holdings,
        downside risks, and a clear Buy/Hold/Pass verdict with cash deployment sizing.
        """
        sym = ticker.strip().upper()
        quote = quote_data or {}
        price = quote.get("current_price", 0.0)
        company_name = quote.get("name", sym)
        sector = quote.get("sector", "Technology")

        # 1. Compute deterministic technical snapshot if not provided
        if technical_snapshot is None:
            try:
                from analytics.technical_indicators import compute_technical_snapshot
                technical_snapshot = compute_technical_snapshot(sym)
            except Exception as e:
                technical_snapshot = None

        # 2. Fetch retail social sentiment if not provided
        if sentiment_snapshot is None:
            try:
                from analytics.sentiment_stream import fetch_social_sentiment_snapshot
                rvol_val = technical_snapshot.rvol if technical_snapshot and technical_snapshot.is_live else None
                sentiment_snapshot = fetch_social_sentiment_snapshot(sym, rvol=rvol_val)
            except Exception as e:
                sentiment_snapshot = None

        # Ensure price is populated from technical snapshot if missing
        if price <= 0.0 and technical_snapshot and technical_snapshot.current_price > 0:
            price = technical_snapshot.current_price

        # Check if already a holding
        existing_holding = next((h for h in portfolio.holdings if h.ticker.upper() == sym), None)
        position_context = ""
        if existing_holding:
            pnl_sign = "+" if existing_holding.unrealized_pnl >= 0 else ""
            position_context = (
                f"CURRENT HOLDING: Yes ({existing_holding.shares} shares @ avg ${existing_holding.avg_price:.2f}, "
                f"current ${existing_holding.current_price:.2f}, weight {existing_holding.weight_pct:.1f}%, "
                f"unrealized PnL: {pnl_sign}${existing_holding.unrealized_pnl:,.2f} / {pnl_sign}{existing_holding.unrealized_pnl_pct:.1f}%)"
            )
        else:
            position_context = f"CURRENT HOLDING: No (New candidate asset outside current portfolio)"

        tot_eq = portfolio.total_equity()
        cash_pct = (portfolio.cash / tot_eq * 100.0) if tot_eq > 0 else 0.0
        holdings_summary = [f"{h.ticker} ({h.name}, {h.sector}, {h.weight_pct:.1f}% weight)" for h in portfolio.holdings]
        news_summary = [f"- {n.source}: {n.title}" for n in news_items[:8]]

        tech_block_text = technical_snapshot.to_telegram_block() if technical_snapshot else "📈 <i>Technical momentum data unavailable.</i>"
        sent_block_text = sentiment_snapshot.to_telegram_block() if sentiment_snapshot else "💬 <i>Social sentiment stream unavailable.</i>"

        tech_details = ""
        if technical_snapshot and technical_snapshot.is_live:
            tech_details = f"""
        VERIFIED TECHNICAL MOMENTUM DATA (CALCULATED FROM 250 DAILY BARS):
        • Live Price: ${price:.2f}
        • RSI (14-Day): {technical_snapshot.rsi_14:.1f} ({technical_snapshot.rsi_status})
        • MACD (12, 26, 9): Line {technical_snapshot.macd_line:+.2f} | Signal {technical_snapshot.macd_signal:+.2f} | Hist {technical_snapshot.macd_hist:+.2f} ({technical_snapshot.macd_status})
        • Moving Averages: 50 DMA ${technical_snapshot.sma_50:.2f} ({technical_snapshot.dist_from_50_dma_pct:+.1f}%) | 200 DMA ${technical_snapshot.sma_200:.2f} ({technical_snapshot.dist_from_200_dma_pct:+.1f}%)
        • Bollinger Bands: ${technical_snapshot.bollinger_lower:.2f} to ${technical_snapshot.bollinger_upper:.2f} (%B: {technical_snapshot.bollinger_pct_b:.2f}, Bandwidth: {technical_snapshot.bollinger_bandwidth:.1f}%, Status: {technical_snapshot.bollinger_status})
        • Volatility ATR (14-Day): ${technical_snapshot.atr_14:.2f} | Suggested Trailing Stop Floor: ${technical_snapshot.suggested_stop_loss:.2f}
        """

        sent_details = ""
        if sentiment_snapshot and sentiment_snapshot.is_live:
            recency = sentiment_snapshot.format_recency_window()
            rate_str = f" ({sentiment_snapshot.messages_per_hour:.1f} msgs/hr · {sentiment_snapshot.total_messages_analyzed} in {recency} · {sentiment_snapshot.acceleration_factor:.1f}x accel)" if sentiment_snapshot.messages_per_hour > 0 else ""
            threads_str = f", {sentiment_snapshot.reddit_post_count} Reddit threads" if sentiment_snapshot.reddit_post_count > 0 else ""
            sent_details = f"""
        RETAIL SOCIAL SENTIMENT & ACTIVITY VELOCITY:
        • Retail Sentiment: {sentiment_snapshot.retail_bull_pct:.0f}% Bullish ({sentiment_snapshot.sentiment_verdict})
        • Activity Velocity: {sentiment_snapshot.social_velocity}{rate_str}
        • Relative Volume (RVOL): {sentiment_snapshot.relative_volume:.2f}x
        """

        stop_floor_text = f"${technical_snapshot.suggested_stop_loss:.2f}" if (technical_snapshot and technical_snapshot.suggested_stop_loss > 0) else f"${price * 0.92:.2f}"
        suggested_atr_text = f"${technical_snapshot.suggested_stop_loss:.2f}" if (technical_snapshot and technical_snapshot.suggested_stop_loss > 0) else "8-10% below entry"

        prompt = f"""
        You are an institutional Chief Investment Officer and Equity Portfolio Strategist.
        Perform a rigorous, objective, and critical investment analysis for {sym} ({company_name}).

        COMMUNICATION STYLE & CRITICAL OBJECTIVITY RULES:
        1. Avoid unwarranted puffery, hyperbole, and false profundity:
           - Do not use dramatic, breathless, or hyperbolic phrasing for routine company developments.
           - Present financial realities, operating margins, competitive moats, valuation multiples, and risk factors objectively.
        2. Exercise rigorous critical judgment on the verdict:
           - Do not casually recommend "BUY (ACCUMULATE)" for unvetted or low-moat tickers.
           - 🟢 BUY (ACCUMULATE / ADD TO WINNERS) is warranted when there is a durable competitive moat, compelling forward growth potential (even if at a premium valuation, provided future earnings growth justifies it), positive catalysts (including recent credible analyst upgrades, earnings revisions, and reporting), and favorable risk-adjusted upside. Note: The investor is explicitly open to buying more of existing winning positions to pyramid into strong fundamental/technical/sentiment opportunities, as well as initiating new positions.
           - 🟡 HOLD (WAIT FOR PULLBACK / MONITOR) applies when near-term risk/reward is temporarily balanced, upside is currently priced in, or when awaiting technical consolidation after an extended move.
           - 🔴 PASS (AVOID / UNFAVORABLE) applies to speculative, unvetted, or deteriorating businesses where downside risk outweighs prospective upside.
        3. Do not invent or rehash technical numbers:
           - Technical momentum indicators and social sentiment metrics are already rendered in their dedicated header blocks above.
           - In the written analysis, focus your narrative on fundamental drivers, portfolio fit, non-technical business risks, and actionable sizing, using the ATR stop-loss floor ({stop_floor_text}) for execution reference.

        TARGET ASSET:
        • Ticker: {sym}
        • Company Name: {company_name}
        • Sector / Asset Class: {sector}
        • Live Price: ${price:.2f}
        • {position_context}

        {tech_details}

        {sent_details}

        INVESTOR'S ACTIVE PORTFOLIO & CAPITAL POSTURE:
        • Total Portfolio Equity: ${tot_eq:,.2f}
        • Deployable Cash Reserves: ${portfolio.cash:,.2f} ({cash_pct:.1f}% Dry Powder)
        • Current Holdings ({len(portfolio.holdings)} positions):
        {chr(10).join(holdings_summary)}

        RECENT MARKET & ASSET HEADLINES:
        {chr(10).join(news_summary)}

        TASK:
        Generate a structured, evidence-grounded analysis in clean Telegram HTML format (use <b>, <i>, <code>).

        Structure the response with these exact sections:
        🔬 <b>STOCK ANALYSIS: {sym} ({company_name})</b>
        <i>Sector: {sector} | Price: ${price:.2f}</i>

        {tech_block_text}

        {sent_block_text}

        📊 <b>Fundamental Catalysts & Growth Drivers:</b>
        - 2-3 key catalysts driving revenue growth, competitive moats, product cycles, and any recent credible positive changes in analyst ratings.

        💼 <b>Portfolio Fit & Synergy Analysis:</b>
        - How {sym} interacts with the investor's current holdings ({', '.join([h.ticker for h in portfolio.holdings[:6]])}).
        - Sector concentration impact (does it increase existing sector weighting or provide diversification?).
        - Ecosystem / supply chain cross-correlations.

        ⚠️ <b>Key Risks & Fundamental Vulnerabilities:</b>
        - 2-3 specific non-technical risks: operating margin compression, customer/supplier concentration, demand deceleration, competitive threats, valuation multiples, product execution bottlenecks, or regulatory/macro headwinds (do not rehash technical RSI/DMA indicators here as they are already presented in the header above).

        🎯 <b>Conviction Verdict & Actionable Sizing:</b>
        - <b>Verdict:</b> 🟢 <b>BUY (ACCUMULATE / ADD)</b> | 🟡 <b>HOLD (WAIT FOR PULLBACK)</b> | 🔴 <b>PASS (AVOID)</b>
        - <b>Verdict Rationale:</b> A clear, sober explanation integrating the technical momentum and sentiment with the fundamental moat.
        - <b>Target Price & Trailing Stop:</b> e.g. Target: $XXX (+XX%) | Trailing Stop Floor: {stop_floor_text}
        - <b>Position Sizing:</b> Explicit recommendation on how much capital to allocate given the investor's available ${portfolio.cash:,.2f} cash reserves (note whether this initiates a new position or scales up an existing winning holding).

        Keep it institutional, balanced, objective, and beautifully styled with HTML tags. Avoid unwarranted puffery or hyperbole. Do not use Markdown backticks.
        """



        effective_key = api_key or self.api_key
        if not effective_key:
            return (
                f"🔒 <b>Gemini API Key Required:</b> Cannot perform AI deep-dive analysis on <b>{sym}</b> without an active Gemini API key.\n\n"
                "Please configure your Gemini API key in Settings to activate AI single-stock analysis."
            )

        res = self.query_llm_text(prompt, api_key=effective_key)
        if res and len(res.strip()) > 50:
            return res.strip()

        return (
            f"⚠️ <b>AI Analysis Unavailable:</b> Gemini was unable to generate an analysis for <b>{sym}</b> at this time.\n\n"
            "Please check network connectivity or your Gemini API quota."
        )

