"""
Opportunity Discovery Agent: Dynamically scans broader market news and macro trends using Gemini 3.8
to surface high-conviction asymmetric alpha plays outside current portfolio holdings.
Zero static catalogs.
"""

from typing import List, Optional, Any
from models import (
    Portfolio, NewsItem, OpportunityAnalysis, OpportunityHorizon, AlertPriority
)
from agents.base_agent import BaseAgent


class OpportunityDiscoveryAgent(BaseAgent):
    def __init__(self, state_store: Optional[Any] = None):
        super().__init__(
            name="Opportunity Discovery Agent",
            role_description="Discovers asymmetric risk/reward market opportunities and secular thematic trends outside current holdings.",
            state_store=state_store
        )

    def scan_opportunities(
        self,
        news_items: List[NewsItem],
        portfolio: Portfolio,
        api_key: Optional[str] = None
    ) -> List[OpportunityAnalysis]:
        effective_key = api_key or self.api_key
        if not effective_key:
            raise ValueError("Gemini API key is required to discover AI market opportunities. Please add your key in Settings.")

        llm_opps = self._llm_batch_discover_opportunities(portfolio, news_items, api_key=effective_key)
        if llm_opps is None:
            raise RuntimeError("Gemini AI was unable to discover opportunities at this time. Please verify API key status.")

        return llm_opps


    def _llm_batch_discover_opportunities(
        self,
        portfolio: Portfolio,
        news_items: List[NewsItem],
        api_key: Optional[str] = None
    ) -> Optional[List[OpportunityAnalysis]]:
        tot_eq = portfolio.total_equity()
        cash_pct = (portfolio.cash / tot_eq * 100.0) if tot_eq > 0 else 0.0
        existing_holdings = [f"{h.ticker} ({h.name}, {h.sector}, {h.weight_pct}%)" for h in portfolio.holdings]
        news_headlines = [f"- {n.source}: {n.title} ({n.summary[:120]})" for n in news_items[:10]]

        prompt = f"""
        You are an elite institutional hedge fund alpha strategist.
        Analyze the investor's current portfolio allocations, deployable cash reserves, and recent global market news:

        CURRENT PORTFOLIO ALLOCATIONS:
        {existing_holdings}

        DEPLOYABLE CASH & DRY POWDER:
        • Available Cash to Deploy: ${portfolio.cash:,.2f} ({cash_pct:.1f}% of total portfolio equity)

        RECENT NEWS FEEDS & MACRO THEMES:
        {chr(10).join(news_headlines)}

        TASK:
        Identify the 2 to 4 absolute best, highest-conviction asymmetric risk/reward investment opportunities OUTSIDE the investor's current portfolio.
        
        CRITICAL SELECTION & OBJECTIVITY RULES:
        1. Avoid random, low-liquidity, or speculative tickers. Prioritize established businesses with proven competitive moats, durable growth runways, or recent credible positive changes in institutional analyst ratings.
        2. Select purely based on what best complements, hedges, and maximizes risk-adjusted alpha for this specific portfolio.
        3. Avoid unwarranted puffery or hyperbole in theses. State growth drivers, margins, and risks objectively.
        4. Consider the investor's available cash reserves when sizing the strategic upside thesis.


        Return JSON matching this exact schema:
        {{
            "opportunities": [
                {{
                    "ticker": "EXAMPLE",
                    "name": "Example Corp.",
                    "sector": "Utilities / Healthcare / Industrials / Financials",
                    "theme": "Thematic Catalyst Name",
                    "horizon": "SECULAR" | "TACTICAL" | "EVENT_DRIVEN",
                    "catalyst_description": "Clear growth or market catalyst.",
                    "why_now": "Immediate macro or earnings inflection.",
                    "upside_thesis": "Why this offers outsized asymmetric upside.",
                    "risk_factors": ["Risk factor 1", "Risk factor 2"],
                    "asymmetric_ratio": 3.2,
                    "estimated_upside_pct": 25.0,
                    "suggested_stop_loss_pct": 7.0,
                    "portfolio_synergy": "Specific portfolio diversification benefit."
                }}
            ]
        }}
        """
        effective_key = api_key or self.api_key
        res = self.query_llm_json(prompt, api_key=effective_key)
        if not res or "opportunities" not in res:
            return None

        opportunities: List[OpportunityAnalysis] = []
        for item in res.get("opportunities", []):
            try:
                hor_str = str(item.get("horizon", "SECULAR")).upper()
                if hor_str not in [e.value for e in OpportunityHorizon]:
                    hor_str = "SECULAR"

                t = str(item.get("ticker", "")).upper().strip()
                if not t or t == "EXAMPLE":
                    continue

                opportunities.append(OpportunityAnalysis(
                    ticker=t,
                    name=item.get("name", t),
                    sector=item.get("sector", "Technology"),
                    theme=item.get("theme", "Secular Growth"),
                    horizon=OpportunityHorizon(hor_str),
                    catalyst_description=item.get("catalyst_description", ""),
                    why_now=item.get("why_now", ""),
                    upside_thesis=item.get("upside_thesis", ""),
                    risk_factors=item.get("risk_factors", []),
                    asymmetric_ratio=float(item.get("asymmetric_ratio", 3.0)),
                    estimated_upside_pct=float(item.get("estimated_upside_pct", 20.0)),
                    suggested_stop_loss_pct=float(item.get("suggested_stop_loss_pct", 7.0)),
                    portfolio_synergy=item.get("portfolio_synergy", "Diversification"),
                    citations=[f"Gemini 3.8 Dynamic Alpha Discovery: {t}"],
                    news_item_id=f"alpha_{t}",

                    priority=AlertPriority.P2_WATCHLIST
                ))
            except Exception:
                continue

        return opportunities


    def discover_more_opportunities(
        self,
        portfolio: Portfolio,
        theme: Optional[str] = None,
        count: int = 4,
        api_key: Optional[str] = None
    ) -> List[OpportunityAnalysis]:
        """
        Executes a targeted, on-demand alpha discovery scan based on an optional user theme or sector filter.
        """
        existing_holdings = [f"{h.ticker} ({h.name}, {h.sector}, {h.weight_pct}%)" for h in portfolio.holdings]
        theme_focus = f"FOCUS THEME / SECTOR: {theme}" if theme else "FOCUS: Diversified high-conviction secular growth & uncorrelated alpha across under-allocated sectors."

        prompt = f"""
        You are an elite quantitative hedge fund alpha strategist.
        The investor is requesting targeted, fresh investment ideas outside their active portfolio.

        CURRENT PORTFOLIO ALLOCATIONS:
        {existing_holdings}

        REQUESTED CRITERIA:
        {theme_focus}

        TASK:
        Generate {count} unique, high-conviction asymmetric investment opportunities that are NOT in the investor's current portfolio.
        
        CRITICAL SELECTION RULES:
        1. Avoid random, speculative, or low-liquidity tickers. Select companies with durable business models, secular growth drivers, or recent credible institutional analyst upgrades.
        2. Ensure genuine asymmetry: upside potential must be at least 2.5x to 3x the suggested stop-loss risk.
        3. Avoid unwarranted puffery or hyperbole; describe theses objectively and with concrete rationale.


        Return JSON matching this exact schema:
        {{
            "opportunities": [
                {{
                    "ticker": "CEG",
                    "name": "Constellation Energy Corp.",
                    "sector": "Utilities",
                    "theme": "AI Datacenter Baseload Energy",
                    "horizon": "SECULAR" | "TACTICAL" | "EVENT_DRIVEN",
                    "catalyst_description": "Hyperscaler 20-year off-take agreements for dedicated nuclear capacity.",
                    "why_now": "Datacenter power bottleneck driving long-term contracted power purchase premiums.",
                    "upside_thesis": "Accelerated EBITDA growth from long-term fixed power pricing.",
                    "risk_factors": ["NRC relicensing timelines", "Regional grid interconnect queues"],
                    "asymmetric_ratio": 3.4,
                    "estimated_upside_pct": 26.0,
                    "suggested_stop_loss_pct": 7.5,
                    "portfolio_synergy": "Provides essential non-correlated infrastructure exposure to balance tech weight."
                }}
            ]
        }}
        """
        effective_key = api_key or self.api_key
        res = self.query_llm_json(prompt, api_key=effective_key)
        if not res or "opportunities" not in res:
            return []


        opportunities: List[OpportunityAnalysis] = []
        for item in res.get("opportunities", []):
            try:
                hor_str = str(item.get("horizon", "SECULAR")).upper()
                if hor_str not in [e.value for e in OpportunityHorizon]:
                    hor_str = "SECULAR"

                opportunities.append(OpportunityAnalysis(
                    ticker=item.get("ticker", "").upper(),
                    name=item.get("name", item.get("ticker", "")),
                    sector=item.get("sector", "Technology"),
                    theme=item.get("theme", theme or "Secular Growth"),
                    horizon=OpportunityHorizon(hor_str),
                    catalyst_description=item.get("catalyst_description", ""),
                    why_now=item.get("why_now", ""),
                    upside_thesis=item.get("upside_thesis", ""),
                    risk_factors=item.get("risk_factors", []),
                    asymmetric_ratio=float(item.get("asymmetric_ratio", 3.0)),
                    estimated_upside_pct=float(item.get("estimated_upside_pct", 20.0)),
                    suggested_stop_loss_pct=float(item.get("suggested_stop_loss_pct", 7.0)),
                    portfolio_synergy=item.get("portfolio_synergy", "Diversification"),
                    citations=[f"Gemini 3.8 On-Demand Alpha Engine: {item.get('ticker')}"],
                    news_item_id=f"alpha_ondemand_{item.get('ticker')}",

                    priority=AlertPriority.P2_WATCHLIST
                ))
            except Exception:
                continue

        return opportunities if len(opportunities) > 0 else []

    def discover_moonshot_opportunities(
        self,
        portfolio: Portfolio,
        count: int = 4,
        api_key: Optional[str] = None
    ) -> List[OpportunityAnalysis]:
        """
        Specialized engine that scans for high-beta, high-asymmetry 'Moonshot' opportunities
        (e.g., Quantum Computing, Small Modular Nuclear Reactors, Phase 3 Biotech readouts,
        Space/Defense payloads, Optical Photonics, and Deep Tech).
        """
        existing_holdings = [f"{h.ticker} ({h.name}, {h.sector})" for h in portfolio.holdings]

        prompt = f"""
        You are an elite deep-tech venture-equity analyst and high-asymmetry quantitative strategist.
        Your task is to surface {count} high-risk, high-reward "MOONSHOT" investment opportunities outside the investor's active portfolio.

        CURRENT PORTFOLIO:
        {existing_holdings}

        MOONSHOT CRITERIA:
        1. Asymmetric Upside: Potential upside of +50.0% to +250.0% backed by a credible structural or binary catalyst.
        2. High Risk / High Volatility: Acknowledge high beta, binary risk, capital expenditure intensity, or regulatory trial risk.
        3. Asymmetric Reward-to-Risk Ratio: >= 3.5 : 1 (e.g. 4.0:1 up to 8.0:1).
        4. Focus Themes: Quantum Computing, Small Modular Reactors (SMRs), Space Commercialization / Defense Satellites, Clinical Phase 3 / Obesity Biotech, Optical Photonics, Autonomous Robotics / AI Edge Silicon.
        5. Outside Active Holdings: Must not be already in the investor's current portfolio.

        Return JSON matching this exact schema:
        {{
            "moonshots": [
                {{
                    "ticker": "OKLO",
                    "name": "Oklo Inc.",
                    "sector": "Energy / Clean Tech",
                    "theme": "🚀 Small Modular Nuclear Fission (SMRs)",
                    "horizon": "SECULAR",
                    "catalyst_description": "Commercial deployment of liquid metal fast reactors to power hyperscaler AI datacenters off-grid.",
                    "why_now": "Hyperscaler data center buildouts facing 4-7 year grid interconnect queues, making on-site SMRs mission-critical.",
                    "upside_thesis": "Market re-rating as first commercial reactor breaks ground with hyperscaler off-take agreement.",
                    "risk_factors": ["NRC regulatory design approval delays", "High cash burn rate before commercial deployment"],
                    "asymmetric_ratio": 5.2,
                    "estimated_upside_pct": 110.0,
                    "suggested_stop_loss_pct": 18.0,
                    "portfolio_synergy": "Asymmetric deep-tech power hedge with venture-like upside profile."
                }}
            ]
        }}
        """
        effective_key = api_key or self.api_key
        res = self.query_llm_json(prompt, api_key=effective_key)
        raw_items = []
        if res and ("moonshots" in res or "opportunities" in res):
            raw_items = res.get("moonshots") or res.get("opportunities") or []

        if not raw_items:
            return []

        opportunities: List[OpportunityAnalysis] = []
        for item in raw_items:
            try:
                hor_str = str(item.get("horizon", "SECULAR")).upper()
                if hor_str not in [e.value for e in OpportunityHorizon]:
                    hor_str = "SECULAR"

                theme = item.get("theme", "🚀 Moonshot Asymmetry")
                if not theme.startswith("🚀"):
                    theme = f"🚀 {theme}"

                opportunities.append(OpportunityAnalysis(
                    ticker=item.get("ticker", "").upper(),
                    name=item.get("name", item.get("ticker", "")),
                    sector=item.get("sector", "Deep Tech"),
                    theme=theme,
                    horizon=OpportunityHorizon(hor_str),
                    catalyst_description=item.get("catalyst_description", ""),
                    why_now=item.get("why_now", ""),
                    upside_thesis=item.get("upside_thesis", ""),
                    risk_factors=item.get("risk_factors", []),
                    asymmetric_ratio=float(item.get("asymmetric_ratio", 4.5)),
                    estimated_upside_pct=float(item.get("estimated_upside_pct", 75.0)),
                    suggested_stop_loss_pct=float(item.get("suggested_stop_loss_pct", 15.0)),
                    portfolio_synergy=item.get("portfolio_synergy", "High-Beta Asymmetric Upside"),
                    citations=[f"Gemini 3.8 Moonshot Radar: {item.get('ticker')}"],
                    news_item_id=f"moonshot_{item.get('ticker')}",

                    priority=AlertPriority.P2_WATCHLIST
                ))
            except Exception:
                continue

        return opportunities if len(opportunities) > 0 else []
