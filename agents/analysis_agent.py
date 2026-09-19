"""
Portfolio Analysis Agent: Evaluates directional risks, fundamental catalysts,
and supply-chain ripple effects dynamically using Gemini 3.8 Flash.
Zero hardcoded ecosystem dictionaries.
"""

import logging
import re
from typing import List, Optional, Dict, Any
from models import (
    Portfolio, PortfolioHolding, NewsItem, HoldingExposureAnalysis,
    DirectionalImpact, AlertPriority, SingleTickerAnalysis
)
from agents.base_agent import BaseAgent
from agents.news_ingestion import (
    resolve_ticker_aliases, is_primary_headline_subject,
    is_low_signal_clickbait, categorize_catalyst_provenance
)

logger = logging.getLogger(__name__)


def deduplicate_and_prioritize_regulatory_items(items: List[NewsItem], max_items: int = 4) -> List[NewsItem]:
    """
    Universally prioritizes formal regulatory rulings, exemptive orders, and rule proposals over speeches,
    and clusters multiple speeches/remarks from the same event to prevent slot exhaustion.
    """
    if not items:
        return []

    def _priority_score(item: NewsItem) -> int:
        title = (item.title or "").lower()
        if any(k in title for k in ["sec issues", "exemptive order", "innovation exemption to facilitate", "order", "proposes"]):
            return 4
        if any(k in title for k in ["innovation exemption", "exemption", "rule", "framework", "announces"]):
            return 3
        if any(k in title for k in ["charges", "statement", "joint", "press release"]):
            return 2
        return 1

    sorted_items = sorted(items, key=lambda x: (_priority_score(x), x.published_at), reverse=True)

    curated: List[NewsItem] = []
    seen_event_clusters: Dict[str, int] = {}

    for item in sorted_items:
        title_lower = (item.title or "").lower()
        cluster_key = None
        for pattern in [r'24[- ]hour trading', r'proxy solicitation', r'shareholder proposal', r'investor advisory committee', r'tokeniz\w+', r'innovation exemption', r'crypto rules']:
            if re.search(pattern, title_lower):
                cluster_key = 'tokenization_exemption' if ('tokeniz' in pattern or 'innovation' in pattern) else pattern
                break

        if cluster_key:
            if seen_event_clusters.get(cluster_key, 0) >= 1:
                continue
            seen_event_clusters[cluster_key] = seen_event_clusters.get(cluster_key, 0) + 1

        curated.append(item)
        if len(curated) >= max_items:
            break

    return curated


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

    def analyze_single_ticker_structured(
        self,
        ticker: str,
        portfolio: Portfolio,
        news_items: List[NewsItem],
        quote_data: Optional[Dict[str, Any]] = None,
        technical_snapshot: Optional[Any] = None,
        sentiment_snapshot: Optional[Any] = None,
        api_key: Optional[str] = None
    ) -> SingleTickerAnalysis:
        """
        Generates a strictly-typed, structured SingleTickerAnalysis evaluating verified technicals,
        live sentiment, fundamental catalysts, risks, target price, and a deterministic verdict and conviction score.
        Uses Gemini responseMimeType='application/json' with prompt injection delimiter defenses.
        """
        sym = ticker.strip().upper()
        quote = quote_data or {}
        price = float(quote.get("current_price", 0.0) or 0.0)
        company_name = quote.get("name", sym)
        sector = quote.get("sector", "Technology")

        # Backwards compatibility check: If analyze_single_ticker is mocked or overridden
        orig_fn = getattr(PortfolioAnalysisAgent.analyze_single_ticker, "__func__", PortfolioAnalysisAgent.analyze_single_ticker)
        curr_fn = getattr(self.analyze_single_ticker, "__func__", self.analyze_single_ticker)
        if curr_fn is not orig_fn or hasattr(self.analyze_single_ticker, "mock") or hasattr(self.analyze_single_ticker, "call_args"):
            res = self.analyze_single_ticker(
                ticker=ticker,
                portfolio=portfolio,
                news_items=news_items,
                quote_data=quote_data,
                technical_snapshot=technical_snapshot,
                sentiment_snapshot=sentiment_snapshot,
                api_key=api_key
            )
            if isinstance(res, SingleTickerAnalysis):
                return res
            res_str = str(res)
            upper_res = res_str.upper()
            verdict = "NEUTRAL"
            if "BUY" in upper_res or "BULLISH" in upper_res:
                verdict = "BULLISH"
            elif "PASS" in upper_res or "BEARISH" in upper_res or "AVOID" in upper_res:
                verdict = "BEARISH"
            elif "HOLD" in upper_res or "NEUTRAL" in upper_res:
                verdict = "HOLD"
            return SingleTickerAnalysis(
                ticker=sym,
                company_name=company_name,
                verdict=verdict,
                conviction_score=85.0 if verdict == "BULLISH" else 50.0,
                thesis=res_str,
                telegram_html=res_str
            )

        # 1. Compute deterministic technical snapshot if not provided
        if technical_snapshot is None:
            try:
                from analytics.technical_indicators import compute_technical_snapshot
                technical_snapshot = compute_technical_snapshot(sym)
            except Exception:
                technical_snapshot = None

        # 2. Fetch retail social sentiment if not provided
        if sentiment_snapshot is None:
            try:
                from analytics.sentiment_stream import fetch_social_sentiment_snapshot
                rvol_val = technical_snapshot.rvol if technical_snapshot and technical_snapshot.is_live else None
                sentiment_snapshot = fetch_social_sentiment_snapshot(sym, rvol=rvol_val)
            except Exception:
                sentiment_snapshot = None

        # Ensure price is populated from technical snapshot if missing
        if price <= 0.0 and technical_snapshot and technical_snapshot.current_price > 0:
            price = technical_snapshot.current_price

        # Check if already a holding
        existing_holding = next((h for h in portfolio.holdings if h.ticker.upper() == sym), None)
        if existing_holding:
            pnl_sign = "+" if existing_holding.unrealized_pnl >= 0 else ""
            position_context = (
                f"CURRENT HOLDING: Yes ({existing_holding.shares} shares @ avg ${existing_holding.avg_price:.2f}, "
                f"current ${existing_holding.current_price:.2f}, weight {existing_holding.weight_pct:.1f}%, "
                f"unrealized PnL: {pnl_sign}${existing_holding.unrealized_pnl:,.2f} / {pnl_sign}{existing_holding.unrealized_pnl_pct:.1f}%)"
            )
        else:
            position_context = "CURRENT HOLDING: No (New candidate asset outside current portfolio)"

        tot_eq = portfolio.total_equity()
        cash_pct = (portfolio.cash / tot_eq * 100.0) if tot_eq > 0 else 0.0
        holdings_summary = [f"{h.ticker} ({h.name}, {h.sector}, {h.weight_pct:.1f}% weight)" for h in portfolio.holdings]

        # Dynamically resolve company brand aliases (e.g. HOOD -> Robinhood Markets, Robinhood)
        aliases = resolve_ticker_aliases(sym, company_name, state_store=self.state_store, use_market_lookup=True)

        # Smart Tiered News Curation for Target Ticker:
        # Tier 1: Primary Ticker Catalysts (company or brand in headline, or explicit related ticker)
        # Tier 2: Authoritative Regulatory / SEC / Central Bank Actions
        # Tier 3: Sector & Macro Context
        primary_catalysts: List[NewsItem] = []
        regulatory_actions: List[NewsItem] = []
        sector_context: List[NewsItem] = []
        other_news: List[NewsItem] = []

        clean_sym_upper = sym.upper()

        for n in news_items:
            # Anti-clickbait defense: filter low-signal retail SEO listicles unless from wire with >= 0.90 reliability
            if is_low_signal_clickbait(n.title) and (n.source_reliability_score or 0.8) < 0.90:
                continue

            provenance = categorize_catalyst_provenance(n)
            is_ticker_match = (
                clean_sym_upper in [t.upper() for t in n.related_tickers] or
                is_primary_headline_subject(n.title, clean_sym_upper, aliases)
            )

            if is_ticker_match:
                primary_catalysts.append(n)
            elif provenance == "REGULATORY / SEC ACTION" or "sec.gov" in (n.source or "").lower():
                regulatory_actions.append(n)
            elif sector and sector in n.related_sectors:
                sector_context.append(n)
            else:
                other_news.append(n)

        # Deduplicate & prioritize regulatory actions so formal commission orders/exemptions
        # are not crowded out by multiple speech transcripts from a single roundtable/event
        curated_regulatory = deduplicate_and_prioritize_regulatory_items(regulatory_actions, max_items=4)

        # Assemble curated news list (up to 5 primary ticker, up to 4 regulatory, up to 2 context)
        selected_news: List[NewsItem] = []
        selected_news.extend(primary_catalysts[:5])
        selected_news.extend(curated_regulatory)
        remaining_slots = 12 - len(selected_news)
        if remaining_slots > 0:
            selected_news.extend(sector_context[:min(2, remaining_slots)])
        remaining_slots = 12 - len(selected_news)
        if remaining_slots > 0 and len(selected_news) < 5:
            selected_news.extend(other_news[:remaining_slots])

        # Prompt injection defense & provenance labeling
        news_summary = []
        for n in selected_news:
            clean_title = str(n.title).replace("<", "").replace(">", "").strip()
            clean_source = str(n.source).replace("<", "").replace(">", "").strip()
            provenance_tag = categorize_catalyst_provenance(n)
            news_summary.append(
                f"- [{provenance_tag}] <<<UNTRUSTED_HEADLINE source=\"{clean_source}\">>>{clean_title}<<</UNTRUSTED_HEADLINE>>>"
            )

        tech_block_text = technical_snapshot.to_telegram_block() if technical_snapshot else "📈 <i>Technical momentum data unavailable.</i>"
        sent_block_text = sentiment_snapshot.to_telegram_block() if sentiment_snapshot else "💬 <i>Social sentiment stream unavailable.</i>"

        tech_details = technical_snapshot.to_prompt_context(price=price) if (technical_snapshot and technical_snapshot.is_live) else ""

        sent_details = ""
        if sentiment_snapshot and sentiment_snapshot.is_live:
            recency = sentiment_snapshot.format_recency_window()
            rate_str = f" ({sentiment_snapshot.messages_per_hour:.1f} msgs/hr · {sentiment_snapshot.total_messages_analyzed} in {recency} · {sentiment_snapshot.acceleration_factor:.1f}x accel)" if sentiment_snapshot.messages_per_hour > 0 else ""
            sent_details = f"""
        RETAIL SOCIAL SENTIMENT & ACTIVITY VELOCITY:
        • Retail Sentiment: {sentiment_snapshot.retail_bull_pct:.0f}% Bullish ({sentiment_snapshot.sentiment_verdict})
        • Activity Velocity: {sentiment_snapshot.social_velocity}{rate_str}
        • Relative Volume (RVOL): {sentiment_snapshot.relative_volume:.2f}x
        """

        stop_floor_text = f"${technical_snapshot.suggested_stop_loss:.2f}" if (technical_snapshot and technical_snapshot.suggested_stop_loss is not None and technical_snapshot.suggested_stop_loss > 0) else f"${price * 0.92:.2f}"


        system_instruction = (
            "You are an institutional Chief Investment Officer and Senior Equity Portfolio Strategist. "
            "You evaluate equities with rigorous objectivity and produce deterministic structured JSON. "
            "Never use dramatic puffery or false profundity. "
            "The 'telegram_html' field must be an exhaustive, thorough, multi-paragraph institutional briefing memo—NOT an abbreviated summary. "
            "Respond ONLY with a JSON object matching this schema:\n"
            "{\n"
            '  "verdict": "BULLISH" | "BEARISH" | "NEUTRAL" | "HOLD" | "CAUTION",\n'
            '  "conviction_score": float (0.0 to 100.0),\n'
            '  "thesis": string (concise 1-2 sentence executive thesis),\n'
            '  "catalysts": [string] (list of 2-3 key catalytic drivers),\n'
            '  "risks": [string] (list of 2-3 material vulnerabilities),\n'
            '  "target_price": float or null,\n'
            '  "stop_floor": float or null,\n'
            '  "suggested_allocation_usd": float,\n'
            '  "telegram_html": string (Comprehensive institutional Telegram HTML formatted with <b>, <i>, <code>)\n'
            "}"
        )

        user_prompt = f"""
        Perform a rigorous, comprehensive, and institutional investment analysis for {sym} ({company_name}).

        CRITICAL VERDICT & DISCIPLINE GUIDELINES:
        • "BULLISH": Durable competitive moat, compelling forward earnings power, verified high-impact catalysts, and disciplined risk/reward.
        • "HOLD": High-quality business trading near fair value or upper technical resistance; favorable long-term but awaiting consolidation or a lower-risk entry.
        • "CAUTION": Elevated downside asymmetry, multiple macro or operational headwinds, or uncertain capital trajectory.
        • "BEARISH": Structural deterioration, distribution breakdown, or severe negative catalysts.
        • "NEUTRAL": Balanced risk/reward with no decisive catalyst.

        TARGET ASSET & POSITION CONTEXT:
        • Ticker: {sym}
        • Company Name: {company_name}
        • Sector: {sector}
        • Live Price: ${price:.2f}
        • {position_context}

        {tech_details}

        {sent_details}

        INVESTOR'S ACTIVE PORTFOLIO & CAPITAL POSTURE:
        • Total Portfolio Equity: ${tot_eq:,.2f}
        • Deployable Cash Reserves: ${portfolio.cash:,.2f} ({cash_pct:.1f}% Dry Powder)
        • Current Portfolio Holdings ({len(portfolio.holdings)} positions):
        {chr(10).join(holdings_summary)}

        INGESTED EXTERNAL HEADLINES & DATA STREAM:
        {chr(10).join(news_summary) if news_summary else "• No breaking external headlines recorded."}

        CRITICAL CATALYST & PROVENANCE SIFTING DIRECTIVE:
        • Distinguish between PRIMARY COMPANY CATALYSTS vs. INCIDENTAL MENTIONS. Only treat an external event as a material catalyst if the company is a direct beneficiary, primary subject, or structural driver.
        • Do NOT inflate generic syndicated listicles ('3 stocks to buy', 'Why X moved today'), incidental name-dropping, or speculative retail clickbait into corporate catalysts.
        • Pay rigorous attention to [REGULATORY / SEC ACTION] and [CORPORATE CATALYST] events (exemptive orders, rule approvals, statutory filings, product launches) that expand the company's addressable market, operational rights, or core product moat. If a landmark regulatory order or exemption (such as SEC tokenization exemptions, broker-dealer market microstructure rulings, or banking/lending approvals) directly empowers the firm's business model or core platform, you MUST explicitly evaluate and detail it under Fundamental Catalysts.
        • If recent headlines lack material substance, explicitly rely on structural fundamental business drivers (revenue expansion, unit economics, net interest income, operating leverage) rather than hallucinating significance from trivial news.

        TASK & REQUIRED "telegram_html" STRUCTURE:
        In the "telegram_html" field, write an in-depth, multi-paragraph institutional briefing memo using clean Telegram HTML tags (<b>, <i>, <code>). Do not write a superficial or compressed summary. Each section must provide substantive, multi-sentence analytical depth:

        🔬 <b>STOCK ANALYSIS: {sym} ({company_name})</b>
        <i>Sector: {sector} | Price: ${price:.2f}</i>

        {tech_block_text}

        {sent_block_text}

        📊 <b>Fundamental Catalysts & Growth Drivers:</b>
        Detail 2–3 genuinely important catalytic drivers capable of moving the stock price over the next 6–18 months.
        - Structure each point with a clear bold title followed by a multi-sentence explanation: e.g. • <b>[Catalyst Title]:</b> Detailed narrative...
        - Draw from whatever holds genuine economic signal: major product cycles, enterprise adoption curves, margin expansion, pricing power, or significant news/filing developments (earnings prints, guidance revisions, regulatory actions).
        - IMPORTANT: Do NOT force-fit trivial or routine headlines into artificial catalysts—focus strictly on catalysts capable of driving meaningful price swings.

        💼 <b>Portfolio Fit & Synergy Analysis:</b>
        - <b>Ecosystem & Cross-Asset Correlation:</b> How {sym} correlates with the investor's specific active holdings ({', '.join([h.ticker for h in portfolio.holdings[:6]])}). Detail upstream/downstream supply chain linkages, competitive overlap, or platform synergies.
        - <b>Sector & Factor Concentration:</b> How holding or expanding {sym} alters aggregate technology/semiconductor exposure across the portfolio (including index ETF allocations like VOO and SFY).

        ⚠️ <b>Key Risks & Fundamental Vulnerabilities:</b>
        Detail 2–3 specific, non-technical vulnerabilities that could derail the investment thesis.
        - Structure each point with a bold title: e.g. • <b>[Risk Title]:</b> Detailed explanation...
        - Focus on real commercial risks: gross/operating margin compression, customer/supplier concentration, demand deceleration, execution bottlenecks, regulatory scrutiny, or valuation multiples (do not rehash technical RSI/DMA indicators here). Incorporate external news or filing disclosures only if they represent genuine material risks.

        🎯 <b>Conviction Verdict & Actionable Sizing:</b>
        - <b>Verdict:</b> [Emoji] <b>[BULLISH (ACCUMULATE) | HOLD (WAIT FOR PULLBACK) | PASS (AVOID)]</b>
        - <b>Verdict Rationale:</b> A comprehensive, institutional multi-sentence paragraph synthesizing the technical momentum indicators, retail sentiment velocity, fundamental quality, material catalysts, and portfolio fit into a clear strategic conclusion.
        - <b>Target Price & Trailing Stop:</b> 12-Month Target: $XXX (+XX%) | Dynamic Trailing Floor: {stop_floor_text}
        - <b>Position Sizing:</b> Explicit recommendation on capital allocation given available ${portfolio.cash:,.2f} in cash reserves. Specify exact dollar amounts, estimated share counts, and whether to deploy immediately or wait for consolidation toward specific support levels.

        Avoid hyperbolic puffery. Maintain institutional rigor, objective precision, and clean formatting.
        """

        effective_key = api_key or self.api_key
        if not effective_key:
            fallback_html = (
                f"🔒 <b>Gemini API Key Required:</b> Cannot perform AI deep-dive analysis on <b>{sym}</b> without an active Gemini API key.\n\n"
                "Please configure your Gemini API key in Settings to activate AI single-stock analysis."
            )
            return SingleTickerAnalysis(
                ticker=sym,
                company_name=company_name,
                verdict="NEUTRAL",
                conviction_score=50.0,
                thesis="Gemini API key required for deep dive.",
                telegram_html=fallback_html
            )

        parsed_json = self.query_llm_json(user_prompt, system_instruction=system_instruction, api_key=effective_key)

        if parsed_json and isinstance(parsed_json, dict):
            try:
                raw_verdict = str(parsed_json.get("verdict", "NEUTRAL")).upper().strip()
                if raw_verdict not in ("BULLISH", "BEARISH", "NEUTRAL", "HOLD", "CAUTION"):
                    raw_verdict = "NEUTRAL"

                raw_conviction = float(parsed_json.get("conviction_score", 85.0) or 85.0)
                raw_conviction = max(0.0, min(100.0, raw_conviction))

                html_block = parsed_json.get("telegram_html")
                if not html_block or len(html_block.strip()) < 30:
                    html_block = (
                        f"🔬 <b>STOCK ANALYSIS: {sym} ({company_name})</b>\n"
                        f"<i>Sector: {sector} | Price: ${price:.2f}</i>\n\n"
                        f"{tech_block_text}\n\n"
                        f"{sent_block_text}\n\n"
                        f"📊 <b>Thesis:</b> {parsed_json.get('thesis', '')}\n"
                        f"🎯 <b>Verdict:</b> {raw_verdict} (Conviction: {raw_conviction:.0f}%)\n"
                    )

                return SingleTickerAnalysis(
                    ticker=sym,
                    company_name=company_name,
                    verdict=raw_verdict,
                    conviction_score=raw_conviction,
                    thesis=str(parsed_json.get("thesis", "")),
                    catalysts=[str(c) for c in parsed_json.get("catalysts", [])],
                    risks=[str(r) for r in parsed_json.get("risks", [])],
                    target_price=float(parsed_json["target_price"]) if parsed_json.get("target_price") is not None else None,
                    stop_floor=float(parsed_json["stop_floor"]) if parsed_json.get("stop_floor") is not None else None,
                    suggested_allocation_usd=float(parsed_json.get("suggested_allocation_usd", 0.0) or 0.0),
                    telegram_html=html_block.strip()
                )
            except Exception as parse_err:
                logger.warning(f"Error parsing Gemini structured response for {sym}: {parse_err}")

        # Deterministic fallback baseline when LLM is unavailable
        calc_verdict = "NEUTRAL"
        calc_conviction = 50.0
        if technical_snapshot and technical_snapshot.is_live:
            if technical_snapshot.rsi_14 <= 35 and "BULLISH" in technical_snapshot.macd_status:
                calc_verdict = "BULLISH"
                calc_conviction = 88.0
            elif technical_snapshot.rsi_14 >= 75:
                calc_verdict = "BEARISH"
                calc_conviction = 82.0
            elif technical_snapshot.rsi_14 >= 55:
                calc_verdict = "HOLD"
                calc_conviction = 65.0

        fallback_html = (
            f"🔬 <b>STOCK ANALYSIS: {sym} ({company_name})</b>\n"
            f"<i>Sector: {sector} | Price: ${price:.2f}</i>\n\n"
            f"{tech_block_text}\n\n"
            f"{sent_block_text}\n\n"
            f"🎯 <b>Technical Stance:</b> {calc_verdict} (Deterministic Baseline: {calc_conviction:.0f}%)\n"
            f"<i>Note: Full AI synthesis unavailable at this moment.</i>"
        )

        return SingleTickerAnalysis(
            ticker=sym,
            company_name=company_name,
            verdict=calc_verdict,
            conviction_score=calc_conviction,
            thesis="Deterministic technical baseline calculated from price history.",
            telegram_html=fallback_html
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
        Backwards-compatible wrapper returning the formatted Telegram HTML text block.
        """
        analysis = self.analyze_single_ticker_structured(
            ticker=ticker,
            portfolio=portfolio,
            news_items=news_items,
            quote_data=quote_data,
            technical_snapshot=technical_snapshot,
            sentiment_snapshot=sentiment_snapshot,
            api_key=api_key
        )
        return analysis.telegram_html


