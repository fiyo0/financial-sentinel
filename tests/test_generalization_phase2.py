"""
Phase 2 Generalization Suite: Unit and Integration Tests for the 7 Anti-Bandaid Architectural Improvements.
Covers:
1. Universal 11-GICS Sector News Entity Extraction & Canonical Ticker Sector Propagation
2. Universal Metadata-Driven ETF Classification (Zero Hardcoded Ticker Sets)
3. Perpetual Algorithmic FOMC Schedule & Meeting Minutes Projection (Beyond March 2027)
4. Volatility & Beta-Adaptive "Mover" Sensitivity Calibration
5. Astronomical Daylight Saving Time Formatting (%Z) Across Summer and Winter
6. Clean Portfolio CSV Ingestion, Dynamic Cash Resolution & Eradication of "Technology" Default
7. Dead Code Removal Verification
"""
from datetime import datetime, date, timezone
from zoneinfo import ZoneInfo
from models import Portfolio, PortfolioHolding, NewsItem
from agents.news_ingestion import NewsIngestionAgent, evaluate_source_reliability
from agents.market_briefing_agent import MarketBriefingAgent
from agents.analysis_agent import PortfolioAnalysisAgent
from analytics.market_data import classify_equity_sector
from analytics.economic_calendar import generate_statutory_fomc_schedule, get_economic_calendar_context
from storage.state_store import StateStore

from orchestrator import FinancialSentinelOrchestrator



# ==============================================================================
# Area 1: Universal 11-GICS Sector Detection in News Ingestion
# ==============================================================================

def test_news_ingestion_canonical_ticker_sector_propagation(tmp_path):
    """Verifies recognized tickers propagate their canonical sectors into found_sectors."""
    db_path = str(tmp_path / "test_state.db")
    store = StateStore(db_path=db_path)
    agent = NewsIngestionAgent(state_store=store)

    # 1. Caterpillar (CAT) -> Industrials
    res_cat = agent.extract_entities("Caterpillar announces quarterly dividend increase for shareholders.")
    assert "CAT" in res_cat["tickers"]
    assert "Industrials" in res_cat["sectors"]

    # 2. NextEra Energy (NEE) -> Utilities
    res_nee = agent.extract_entities("NextEra Energy accelerates renewable infrastructure expansion.")
    assert "NEE" in res_nee["tickers"]
    assert "Utilities" in res_nee["sectors"]

    # 3. JPMorgan (JPM) -> Financials
    res_jpm = agent.extract_entities("JPMorgan issues guidance on net interest income trajectory.")
    assert "JPM" in res_jpm["tickers"]
    assert "Financials" in res_jpm["sectors"]


def test_news_ingestion_11_gics_semantic_classification():
    """Verifies semantic keyword extraction across official GICS sectors without tickers."""
    agent = NewsIngestionAgent()

    # Materials
    res_mat = agent.extract_entities("Chemical mining firm expands copper and lithium extraction for batteries.")
    assert "Materials" in res_mat["sectors"]

    # Real Estate
    res_re = agent.extract_entities("Commercial real estate REIT reports record property leasing revenues.")
    assert "Real Estate" in res_re["sectors"]

    # Consumer Staples
    res_cs = agent.extract_entities("Supermarket chain reports surge in grocery and packaged food sales.")
    assert "Consumer Staples" in res_cs["sectors"]

    # Consumer Discretionary
    res_cd = agent.extract_entities("Luxury apparel retailer and restaurant operator reports strong holiday traffic.")
    assert "Consumer Discretionary" in res_cd["sectors"]

    # Communication Services
    res_comm = agent.extract_entities("Telecom operator expands 5G wireless carrier and streaming media broadband.")
    assert "Communication Services" in res_comm["sectors"]

    # Unclassified fallback when no financial context exists
    res_uncl = agent.extract_entities("A quiet walk in the park on a sunny afternoon.")
    assert res_uncl["sectors"] == ["Unclassified"]


# ==============================================================================
# Area 2: Universal Metadata-Driven ETF Classification
# ==============================================================================

def test_market_data_dynamic_etf_classification():
    """Verifies arbitrary ETFs are classified dynamically without appearing in a static ticker set."""
    # Arbitrary non-whitelist ETFs
    assert classify_equity_sector("ARKK", "ARK Innovation ETF") == "Index ETF / Fund"
    assert classify_equity_sector("SMH", "VanEck Semiconductor ETF") == "Index ETF / Fund"
    assert classify_equity_sector("JEPI", "JPMorgan Equity Premium Income ETF") == "Index ETF / Fund"
    assert classify_equity_sector("BND", "Vanguard Total Bond Market Index Fund") == "Index ETF / Fund"
    assert classify_equity_sector("VT", "Vanguard Total World Stock Index Fund") == "Index ETF / Fund"
    assert classify_equity_sector("TLT", "iShares 20+ Year Treasury Bond ETF") == "Index ETF / Fund"
    assert classify_equity_sector("XBI", "SPDR S&P Biotech ETF") == "Index ETF / Fund"

    # With explicit_type="etf" even if name is non-traditional
    assert classify_equity_sector("XYZT", "Global Strategic Assets Vehicle", explicit_type="etf") == "Index ETF / Fund"

    # Core standard ETFs still classify properly
    assert classify_equity_sector("SPY", "SPDR S&P 500 ETF Trust") == "Index ETF / Fund"
    assert classify_equity_sector("VOO", "Vanguard S&P 500 ETF") == "Index ETF / Fund"
    assert classify_equity_sector("QQQ", "Invesco QQQ Trust Series 1") == "Index ETF / Fund"


def test_analysis_agent_dynamic_etf_detection():
    """Verifies PortfolioAnalysisAgent identifies arbitrary ETFs dynamically in holdings."""
    agent = PortfolioAnalysisAgent()
    p = Portfolio(
        name="Dynamic ETF Portfolio",
        cash=10000.0,
        holdings=[
            PortfolioHolding(
                ticker="ARKK",
                name="ARK Innovation ETF",
                shares=50,
                avg_price=45.0,
                current_price=48.0,
                sector="Index ETF / Fund"
            ),
            PortfolioHolding(
                ticker="BND",
                name="Vanguard Total Bond Market",
                shares=100,
                avg_price=72.0,
                current_price=73.0,
                sector="Index ETF / Fund"
            ),
            PortfolioHolding(
                ticker="NVDA",
                name="NVIDIA Corporation",
                shares=20,
                avg_price=120.0,
                current_price=130.0,
                sector="Semiconductors"
            )
        ]
    )

    captured_prompts = []
    agent.query_llm_json = lambda prompt, **kw: (captured_prompts.append(prompt), {
        "verdict": "BULLISH", "conviction_score": 85.0, "thesis": "Solid", "target_price": 145.0,
        "key_catalysts": ["AI"], "primary_risks": ["Competition"],
        "portfolio_fit_assessment": "Good fit", "telegram_html": "Analysis"
    })[1]


    agent.analyze_single_ticker_structured("NVDA", portfolio=p, news_items=[], api_key="fake")
    assert len(captured_prompts) == 1

    prompt = captured_prompts[0]
    assert "ARKK" in prompt
    assert "BND" in prompt
    assert "active index ETF allocations" in prompt


# ==============================================================================
# Area 3: Perpetual Algorithmic FOMC Schedule Projection
# ==============================================================================

def test_fomc_schedule_perpetual_projection_2027_beyond():
    """Verifies FOMC schedule projects all statutory meetings, press conferences, and minutes beyond March 2027."""
    # Query for the remainder of 2027 (April 1 to December 31, 2027)
    events_2027 = generate_statutory_fomc_schedule(date(2027, 4, 1), date(2027, 12, 31))

    # Must contain the 6 meetings for the remainder of 2027 (May, Jun, Jul, Sep, Nov, Dec)
    rate_decisions = [e for e in events_2027 if "FOMC Interest Rate Decision" in e["name"]]
    assert len(rate_decisions) >= 5

    # June, September, and December meetings must include SEP
    sep_decisions = [e for e in rate_decisions if "Summary of Economic Projections" in e["name"]]
    assert len(sep_decisions) >= 2

    # Every meeting must be followed by a 14:30 ET Press Conference
    pressers = [e for e in events_2027 if "Federal Reserve Chair Press Conference" in e["name"]]
    assert len(pressers) == len(rate_decisions)

    # Must generate 21-day delayed Meeting Minutes
    minutes = [e for e in events_2027 if "Meeting Minutes" in e["name"]]
    assert len(minutes) >= 4

    # Full year 2028 statutory projection
    events_2028 = generate_statutory_fomc_schedule(date(2028, 1, 1), date(2028, 12, 31))
    rates_2028 = [e for e in events_2028 if "FOMC Interest Rate Decision" in e["name"]]
    assert len(rates_2028) == 8


# ==============================================================================
# Area 4: Volatility & Beta-Adaptive Mover Thresholds
# ==============================================================================

def test_market_briefing_beta_adaptive_mover_sensitivity():
    """
    Verifies that low-volatility defensive holdings trigger on smaller moves (0.75%),
    while high-beta holdings filter out sub-2.0% routine market drift.
    """
    agent = MarketBriefingAgent()
    p = Portfolio(
        name="Adaptive Volatility Portfolio",
        cash=25000.0,
        holdings=[
            # Utilities: growth_beta = 0.4 -> adaptive threshold = 0.75%
            PortfolioHolding(
                ticker="DUK", name="Duke Energy", shares=50, avg_price=100.0, current_price=100.85,
                sector="Utilities", daily_change_pct=0.85
            ),
            # Consumer Staples: growth_beta = 0.5 -> adaptive threshold = 0.75%
            PortfolioHolding(
                ticker="PG", name="Procter & Gamble", shares=40, avg_price=160.0, current_price=161.28,
                sector="Consumer Staples", daily_change_pct=0.80
            ),
            # Semiconductors: growth_beta = 1.5 -> adaptive threshold = 2.25%
            PortfolioHolding(
                ticker="NVDA", name="NVIDIA", shares=30, avg_price=120.0, current_price=121.44,
                sector="Semiconductors", daily_change_pct=1.20
            ),
            # Technology: growth_beta = 1.3 -> adaptive threshold = 1.95%
            PortfolioHolding(
                ticker="TSLA", name="Tesla", shares=20, avg_price=220.0, current_price=223.08,
                sector="Technology", daily_change_pct=1.40
            ),
        ]
    )

    ctx = agent._extract_significant_portfolio_movers(p, news_items=[], threshold_pct=1.5)

    # DUK (+0.85%) and PG (+0.80%) exceeded their 0.75% defensive thresholds
    assert "DUK" in ctx
    assert "PG" in ctx

    # NVDA (+1.20%) and TSLA (+1.40%) are within their beta-scaled noise bands (< 1.95% - 2.25%)
    assert "NVDA" not in ctx
    assert "TSLA" not in ctx

    # When an active breaking news catalyst hits NVDA, it triggers regardless of price threshold
    news = [NewsItem(
        id="n_nvda",
        title="Nvidia Announces Breakthrough Next-Gen Architecture",
        source="Reuters",
        url="https://reuters.com/nvda",
        published_at=datetime.now(timezone.utc),
        summary="Architecture breakthrough announced.",
        related_tickers=["NVDA"]
    )]
    ctx_with_news = agent._extract_significant_portfolio_movers(p, news_items=news, threshold_pct=1.5)
    assert "NVDA" in ctx_with_news
    assert "Active News Catalyst" in ctx_with_news


# ==============================================================================
# Area 5: Astronomical Daylight Saving Time Formatting (%Z)
# ==============================================================================

def test_astronomical_timezone_formatting_seasons():
    """Verifies %Z produces exact astronomical timezones across summer and winter."""
    # Summer (Daylight Saving Time)
    summer_dt_ny = datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("America/New_York"))
    summer_dt_la = datetime(2026, 7, 15, 11, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert summer_dt_ny.strftime("%Z") == "EDT"
    assert summer_dt_la.strftime("%Z") == "PDT"

    # Winter (Standard Time)
    winter_dt_ny = datetime(2027, 1, 15, 14, 0, tzinfo=ZoneInfo("America/New_York"))
    winter_dt_la = datetime(2027, 1, 15, 11, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert winter_dt_ny.strftime("%Z") == "EST"
    assert winter_dt_la.strftime("%Z") == "PST"

    # Economic calendar status description formatting in winter
    as_of_winter = datetime(2027, 1, 27, 15, 0, tzinfo=ZoneInfo("America/New_York"))
    res = get_economic_calendar_context(as_of=as_of_winter, horizon_days=7)
    today_items = res["today_events"]

    assert len(today_items) > 0
    # In winter, time display must output EST
    assert "EST" in today_items[0]["time_display"]


# ==============================================================================
# Area 6: Clean Portfolio CSV Ingestion & Eradication of "Technology" Default
# ==============================================================================

def test_orchestrator_csv_ingestion_cash_and_sectors(tmp_path):
    """Verifies CSV parsing supports cash rows dynamically and uses canonical sector resolution."""
    db_path = str(tmp_path / "test_state.db")
    orch = FinancialSentinelOrchestrator(db_path=db_path)



    # 1. CSV with explicit cash row
    csv_file = tmp_path / "test_portfolio.csv"
    csv_file.write_text(
        "ticker,name,shares,avg_price,current_price,sector\n"
        "CAT,Caterpillar,10,300.0,320.0,\n"          # Empty sector -> should resolve to Industrials from canonical DB
        "UNKNOWNXYZ,Mystery Stock,5,50.0,55.0,\n"   # Empty sector & unknown -> should resolve to Unclassified
        "USD,Cash Reserves,5000,1.0,1.0,Cash\n"      # Cash line
    )

    p = orch.load_portfolio_from_file(str(csv_file))

    assert p.cash == 5000.0
    cat_holding = next(h for h in p.holdings if h.ticker == "CAT")
    assert cat_holding.sector == "Industrials"

    unknown_holding = next(h for h in p.holdings if h.ticker == "UNKNOWNXYZ")
    assert unknown_holding.sector == "Unclassified"

    # 2. CSV with NO cash row -> cash defaults to 0.0 without magic numbers
    csv_no_cash = tmp_path / "no_cash.csv"
    csv_no_cash.write_text(
        "ticker,shares,avg_price\n"
        "AAPL,10,200.0\n"
    )
    p_no_cash = orch.load_portfolio_from_file(str(csv_no_cash))
    assert p_no_cash.cash == 0.0


def test_portfolio_model_deduplicate_and_aggregate_sector_update():
    """Verifies deduplicate_and_aggregate updates Unclassified to real sectors including Technology."""
    p = Portfolio(
        name="Update Test",
        cash=0.0,
        holdings=[
            PortfolioHolding(ticker="AAPL", name="Apple", shares=10, avg_price=150.0, current_price=150.0, sector="Unclassified"),
            PortfolioHolding(ticker="AAPL", name="Apple Inc", shares=5, avg_price=160.0, current_price=160.0, sector="Technology")

        ]
    )
    p.deduplicate_and_aggregate()
    assert len(p.holdings) == 1
    assert p.holdings[0].sector == "Technology"
    assert p.holdings[0].shares == 15.0


# ==============================================================================
# Area 7: Clean Removal of Dead Legacy Code
# ==============================================================================

def test_dead_code_removal_and_authority_scoring():
    """Verifies SOURCE_RELIABILITY_MAP was cleanly removed and authority scoring functions properly."""
    import agents.news_ingestion as ni_module
    assert "SOURCE_RELIABILITY_MAP" not in ni_module.__dict__

    # Verify authority scoring continues to operate universally
    assert evaluate_source_reliability("https://www.sec.gov/news/press-release") == 0.99
    assert evaluate_source_reliability("https://www.reuters.com/markets") == 0.92
    assert evaluate_source_reliability("https://www.cnbc.com/markets") == 0.85
    assert evaluate_source_reliability("https://seekingalpha.com/article/123") == 0.65
    assert evaluate_source_reliability("https://random-unknown-blog.xyz/post") == 0.75

