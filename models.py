"""
Data models and schemas for the Financial Multi-Agent System.
"""
from datetime import datetime
from enum import Enum
from typing import List, Optional, Dict, Literal
from pydantic import BaseModel, Field


class DirectionalImpact(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"


class AlertPriority(str, Enum):
    P0_CRITICAL = "P0_CRITICAL"   # Direct holding hit, >3% expected impact, urgent push
    P1_NOTABLE = "P1_NOTABLE"     # Macro, sector, peer movement -> Daily digest
    P2_WATCHLIST = "P2_WATCHLIST" # Thematic, idea lab, low urgency -> Dashboard/Watchlist


class OpportunityHorizon(str, Enum):
    TACTICAL = "TACTICAL"         # 1-14 days (earnings, unscheduled 8-K catalyst, M&A)
    SWING = "SWING"               # 2-8 weeks (product cycles, sector rotation)
    SECULAR = "SECULAR"           # 3-12+ months (thematic, energy transition, AI compute)


class CriticVerdict(str, Enum):
    APPROVED = "APPROVED"
    APPROVED_WITH_CAVEATS = "APPROVED_WITH_CAVEATS"
    REJECTED_SPECULATIVE = "REJECTED_SPECULATIVE"
    REJECTED_LOW_CREDIBILITY = "REJECTED_LOW_CREDIBILITY"


from pydantic import computed_field


class PortfolioHolding(BaseModel):
    ticker: str
    name: str
    shares: float
    avg_price: float
    current_price: float
    daily_change_pct: float = Field(default=0.0, description="Intraday percentage change")
    weight_pct: float = Field(default=0.0, description="Percentage of total portfolio value")
    sector: str = "Unclassified"
    asset_class: str = "Equity"
    thematic_tags: List[str] = Field(default_factory=list)
    stop_loss: Optional[float] = None
    target_price: Optional[float] = None


    @computed_field
    @property
    def market_value(self) -> float:
        return round(self.shares * self.current_price, 2)

    @computed_field
    @property
    def total_cost(self) -> float:
        return round(self.shares * self.avg_price, 2)

    @computed_field
    @property
    def unrealized_pnl(self) -> float:
        return round((self.current_price - self.avg_price) * self.shares, 2)

    @computed_field
    @property
    def unrealized_pnl_pct(self) -> float:
        if self.avg_price <= 0:
            return 0.0
        return round(((self.current_price - self.avg_price) / self.avg_price) * 100.0, 2)


class Portfolio(BaseModel):
    name: str = "Primary Portfolio"
    cash: float = 0.0
    holdings: List[PortfolioHolding] = Field(default_factory=list)
    last_updated: datetime = Field(default_factory=datetime.utcnow)

    def total_equity(self) -> float:
        return round(self.cash + sum(h.market_value for h in self.holdings), 2)

    def total_cost(self) -> float:
        return round(sum(h.total_cost for h in self.holdings), 2)

    def total_unrealized_pnl(self) -> float:
        return round(sum(h.unrealized_pnl for h in self.holdings), 2)

    def total_unrealized_pnl_pct(self) -> float:
        cost = self.total_cost()
        if cost <= 0:
            return 0.0
        return round((self.total_unrealized_pnl() / cost) * 100.0, 2)

    def deduplicate_and_aggregate(self) -> None:
        """
        Merges duplicate holding entries for the same ticker into a single position
        using exact weighted-average cost basis calculation.
        """
        aggregated: Dict[str, PortfolioHolding] = {}
        for h in self.holdings:
            ticker_upper = h.ticker.strip().upper()
            if not ticker_upper:
                continue
            if ticker_upper in aggregated:
                existing = aggregated[ticker_upper]
                total_shares = existing.shares + h.shares
                if total_shares > 0:
                    total_cost = (existing.shares * existing.avg_price) + (h.shares * h.avg_price)
                    weighted_avg_price = total_cost / total_shares
                    existing.avg_price = round(weighted_avg_price, 4)
                    existing.shares = round(total_shares, 4)
                if h.current_price > 0:
                    existing.current_price = h.current_price
                if h.name and h.name != ticker_upper:
                    existing.name = h.name
                if h.sector and h.sector != "Technology":
                    existing.sector = h.sector
            else:
                aggregated[ticker_upper] = h
        self.holdings = list(aggregated.values())
        self.recalculate_weights()

    def recalculate_weights(self) -> None:
        total = self.total_equity()
        if total <= 0:
            return
        for h in self.holdings:
            h.weight_pct = round((h.market_value / total) * 100.0, 2)


class NewsCategory(str, Enum):
    MACRO = "MACRO"
    SEC_FILING = "SEC_FILING"
    EARNINGS = "EARNINGS"
    SECTOR = "SECTOR"
    GEOPOLITICAL = "GEOPOLITICAL"
    BREAKING = "BREAKING"
    ANALYST = "ANALYST"


class NewsItem(BaseModel):
    id: str = Field(description="Unique SHA256 or feed ID")
    title: str
    source: str
    url: str
    published_at: datetime = Field(default_factory=datetime.utcnow)
    summary: str
    full_text: Optional[str] = None
    category: NewsCategory = NewsCategory.BREAKING
    source_reliability_score: float = Field(default=0.8, ge=0.0, le=1.0)
    related_tickers: List[str] = Field(default_factory=list)
    related_sectors: List[str] = Field(default_factory=list)
    raw_hash: str = ""


class HoldingExposureAnalysis(BaseModel):
    holding_ticker: str
    holding_name: str
    impact: DirectionalImpact
    impact_magnitude_pct: float = Field(default=0.0, description="Estimated price volatility impact range in %")
    priority: AlertPriority
    direct_exposure: bool = Field(default=True, description="True if holding is directly named; False if 2nd order/supply chain")
    transmission_channel: str = Field(default="Direct Catalyst", description="e.g. Earnings, Supply Chain, Antitrust, Fed Rate")
    rationale: str
    key_risks: List[str] = Field(default_factory=list)
    recommended_action: str = "Hold and monitor"
    citations: List[str] = Field(default_factory=list)
    news_item_id: str


class OpportunityAnalysis(BaseModel):
    ticker: str
    name: str
    sector: str
    theme: str
    horizon: OpportunityHorizon
    catalyst_description: str
    why_now: str
    upside_thesis: str
    risk_factors: List[str] = Field(default_factory=list)
    asymmetric_ratio: float = Field(default=2.5, description="Estimated reward/risk ratio e.g. 3.0:1")
    estimated_upside_pct: float = 15.0
    suggested_stop_loss_pct: float = 5.0
    portfolio_synergy: str = Field(default="", description="How this diversifies or hedges existing portfolio")
    citations: List[str] = Field(default_factory=list)
    news_item_id: str
    priority: AlertPriority = AlertPriority.P2_WATCHLIST


class CriticReview(BaseModel):
    target_id: str = Field(description="ID of HoldingExposureAnalysis or OpportunityAnalysis reviewed")
    item_type: str = Field(description="'risk_analysis' or 'opportunity'")
    verdict: CriticVerdict
    bias_score: float = Field(default=0.1, ge=0.0, le=1.0, description="0 = Unbiased, 1 = Severe confirmation bias / hype")
    source_credibility_grade: str = Field(default="A", description="A, B, C, D, F")
    calibrated_confidence_pct: float = Field(default=85.0, ge=0.0, le=100.0)
    counter_thesis_questions: List[str] = Field(default_factory=list)
    identified_biases: List[str] = Field(default_factory=list)
    review_summary: str


class PortfolioStressMetric(BaseModel):
    sector_concentrations: Dict[str, float] = Field(default_factory=dict)
    top_3_concentration_pct: float = 0.0
    high_concentration_warning: bool = False
    sector_herfindahl_index: Optional[float] = None
    estimated_portfolio_beta: Optional[float] = 1.0
    annualized_volatility_pct: Optional[float] = None
    var_95_daily_pct: Optional[float] = None
    var_95_daily_usd: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    cash_allocation_pct: float = 0.0
    macro_shock_scenarios: Dict[str, float] = Field(
        default_factory=dict,
        description="Scenario name -> Estimated portfolio drawdown/gain %"
    )
    fields_unavailable: List[str] = Field(default_factory=list)
    provenance_note: Optional[str] = None



class BriefingReport(BaseModel):
    report_id: str
    user_id: Optional[str] = None
    slot: Optional[str] = "general"
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    executive_summary: str
    total_holdings_monitored: int
    portfolio_stress: Optional[PortfolioStressMetric] = None
    critical_risk_alerts: List[HoldingExposureAnalysis] = Field(default_factory=list)
    notable_risk_alerts: List[HoldingExposureAnalysis] = Field(default_factory=list)
    top_opportunities: List[OpportunityAnalysis] = Field(default_factory=list)
    critic_verdicts: List[CriticReview] = Field(default_factory=list)
    raw_news_count: int = 0
    dispatched_channels: List[str] = Field(default_factory=list)


class UserSession(BaseModel):
    user_id: str
    username: str
    email: str
    telegram_username: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    has_gemini_key: bool = False
    masked_gemini_key: Optional[str] = None
    role: str = "user"


class UserRegisterRequest(BaseModel):
    username: str
    email: str
    password: str
    telegram_username: Optional[str] = ""
    gemini_api_key: Optional[str] = ""


class UserLoginRequest(BaseModel):
    username_or_email: Optional[str] = ""
    password: str



class UserSettingsUpdateRequest(BaseModel):
    telegram_username: Optional[str] = None
    gemini_api_key: Optional[str] = None
    email: Optional[str] = None
    new_password: Optional[str] = None
    cash: Optional[float] = None


class SingleTickerAnalysis(BaseModel):
    ticker: str
    company_name: str
    verdict: Literal["BULLISH", "BEARISH", "NEUTRAL", "HOLD", "CAUTION"]
    conviction_score: float = Field(default=85.0, ge=0.0, le=100.0, description="Conviction score between 0.0 and 100.0")
    thesis: str = Field(default="", description="Summary thesis grounded in fundamentals, technicals, and sentiment")
    catalysts: List[str] = Field(default_factory=list, description="Key fundamental growth drivers and positive catalysts")
    risks: List[str] = Field(default_factory=list, description="Key operational, competitive, or valuation downside risks")
    target_price: Optional[float] = Field(None, description="Forward 6-12 month target price if applicable")
    stop_floor: Optional[float] = Field(None, description="Volatility-adjusted stop loss floor")
    suggested_allocation_usd: float = Field(0.0, description="Recommended dollar allocation from available cash")
    telegram_html: str = Field(default="", description="Formatted Telegram HTML output with <b>, <i>, and <code> tags")


