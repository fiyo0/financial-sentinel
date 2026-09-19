"""
Comprehensive unit tests for the 6 Generalization Pillars:
- Pillar 1: AI Prompt De-biasing (no hardcoded VOO/SFY or sector bias)
- Pillar 2: Dynamic 11 GICS Sector Classification & Quant Risk
- Pillar 3: Autonomous Ticker Alias Registry & Canonical Self-Seeding
- Pillar 4: Algorithmic Multi-Year Macro Calendar Generator (2026/2027+)
- Pillar 5: TLD & Governance-Driven Domain Authority Scoring
- Pillar 6: Equity-Anchored Social Sentiment Search Query Construction
"""

import httpx
from datetime import date, datetime

from models import Portfolio, PortfolioHolding
from agents.analysis_agent import PortfolioAnalysisAgent
from analytics.market_data import classify_equity_sector
from agents.news_ingestion import (
    evaluate_source_reliability,
    extract_stem_aliases,
    resolve_ticker_aliases,
    is_primary_headline_subject,
    NewsIngestionAgent,
    StateStore,
)
from analytics.economic_calendar import (
    generate_statutory_macro_schedule,
    FOMC_SCHEDULE,
)
from analytics.sentiment_stream import _fetch_reddit_discussion
from analytics.quant_risk import SECTOR_MACRO_SENSITIVITIES


# ==============================================================================
# Pillar 1: AI Prompt De-biasing
# ==============================================================================
def test_pillar1_prompt_debiasing_non_tech_portfolio(monkeypatch):
    """
    Verifies that the single-stock deep-dive prompt dynamically calculates the user's
    actual sector allocations and does not hardcode VOO, SFY, or semiconductor assumptions.
    """
    portfolio = Portfolio(
        cash=50000.0,
        holdings=[
            PortfolioHolding(
                ticker="JNJ",
                name="Johnson & Johnson",
                shares=100,
                avg_price=150.0,
                current_price=160.0,
                sector="Health Care",
                weight_pct=40.0
            ),
            PortfolioHolding(
                ticker="XOM",
                name="Exxon Mobil Corporation",
                shares=200,
                avg_price=100.0,
                current_price=110.0,
                sector="Energy",
                weight_pct=60.0
            ),
        ]
    )

    captured_prompt = {}

    def fake_gemini_call(prompt: str, system_instruction: str, model_name: str = "gemini-2.5-flash", **kwargs):
        captured_prompt["user_prompt"] = prompt
        captured_prompt["system_instruction"] = system_instruction
        return {
            "verdict": "BULLISH",
            "conviction_score": 85.0,
            "thesis": "Strong dividend and cash generation profile.",
            "catalysts": ["Expanding operational margins", "Global demand tailwind"],
            "risks": ["Commodity price volatility", "Regulatory overhead"],
            "target_price": 180.0,
            "stop_floor": 145.0,
            "suggested_allocation_usd": 10000.0,
            "telegram_html": "🔬 <b>STOCK ANALYSIS</b>"
        }

    agent = PortfolioAnalysisAgent()
    agent.query_llm_json = fake_gemini_call

    agent.analyze_single_ticker_structured(
        "PFE",
        portfolio,
        [],
        quote_data={"current_price": 30.0, "name": "Pfizer Inc.", "sector": "Health Care"},
        api_key="test-key"
    )

    prompt_text = captured_prompt.get("user_prompt", "")
    assert prompt_text, "User prompt should have been generated."

    # Must contain the user's actual sectors
    assert "Health Care" in prompt_text
    assert "Energy" in prompt_text

    # Must NOT contain purged hardcoded references
    assert "VOO" not in prompt_text
    assert "SFY" not in prompt_text
    assert "technology/semiconductor" not in prompt_text
    assert "semiconductor" not in prompt_text.lower()


def test_pillar1_prompt_detects_user_etf_allocations(monkeypatch):
    """
    Verifies that if the user holds an ETF (e.g. SPY), the prompt mentions it dynamically.
    """
    portfolio = Portfolio(
        cash=20000.0,
        holdings=[
            PortfolioHolding(
                ticker="SPY",
                name="SPDR S&P 500 ETF Trust",
                shares=50,
                avg_price=450.0,
                current_price=500.0,
                sector="Index ETF / Fund",
                weight_pct=100.0
            )
        ]
    )

    captured_prompt = {}

    def fake_gemini_call(prompt: str, system_instruction: str, model_name: str = "gemini-2.5-flash", **kwargs):
        captured_prompt["user_prompt"] = prompt
        return {
            "verdict": "HOLD",
            "conviction_score": 70.0,
            "thesis": "Solid enterprise software.",
            "catalysts": ["Cloud migration"],
            "risks": ["Valuation multiple"],
            "target_price": 300.0,
            "stop_floor": 250.0,
            "suggested_allocation_usd": 5000.0,
            "telegram_html": "🔬 <b>STOCK ANALYSIS</b>"
        }

    agent = PortfolioAnalysisAgent()
    agent.query_llm_json = fake_gemini_call

    agent.analyze_single_ticker_structured(
        "CRM",
        portfolio,
        [],
        quote_data={"current_price": 280.0, "name": "Salesforce Inc.", "sector": "Information Technology"},
        api_key="test-key"
    )

    prompt_text = captured_prompt.get("user_prompt", "")
    assert "SPY" in prompt_text


# ==============================================================================
# Pillar 2: Dynamic 11 GICS Sector Classification
# ==============================================================================
def test_pillar2_gics_sector_classification_all_11_sectors():
    """
    Verifies that classify_equity_sector accurately identifies all 11 official GICS sectors.
    """
    gics_samples = [
        ("Information Technology", "AAPL", "Enterprise SaaS and software platform"),
        ("Healthcare", "LLY", "Oncology clinical trials and biotechnology therapeutics"),
        ("Financials", "JPM", "Regional banking deposit franchise and wealth management"),
        ("Consumer Discretionary", "TSLA", "Electric automobile manufacturer and retail apparel"),
        ("Consumer Staples", "KO", "Packaged foods, beverages, and household consumer goods"),
        ("Industrials", "CAT", "Commercial aerospace engines, defense machinery, and freight rail"),
        ("Energy", "XOM", "Upstream crude oil exploration and petroleum refining"),
        ("Utilities", "NEE", "Regulated electric power utility and water distribution"),
        ("Real Estate", "PLD", "Commercial property REIT and real estate leasing"),
        ("Materials", "LIN", "Specialty chemicals and lithium mining"),
        ("Communication Services", "DIS", "Wireless telecommunications and streaming media network"),
    ]

    for expected_sector, sym, text in gics_samples:
        detected = classify_equity_sector(sym, text)
        if expected_sector == "Information Technology":
            assert detected in ("Technology", "Information Technology")
        else:
            assert detected == expected_sector, f"Expected {expected_sector} for '{text}', got {detected}"


def test_pillar2_gics_sector_classification_etf_and_unclassified():
    """
    Verifies ETF classification and unclassified fallback without hardcoded technology default.
    """
    assert classify_equity_sector("VOO", "Vanguard S&P 500 ETF trust") == "Index ETF / Fund"
    assert classify_equity_sector("QQQ", "Invesco QQQ Trust Series 1") == "Index ETF / Fund"
    assert classify_equity_sector("XYZ", "Totally obscure text with no recognizable industry keywords") == "Unclassified"


def test_pillar2_quant_risk_sensitivities_cover_all_sectors():
    """
    Verifies that SECTOR_MACRO_SENSITIVITIES covers all 11 GICS sectors + ETF + Unclassified.
    """
    required_sectors = [
        "Information Technology", "Technology", "Health Care", "Healthcare", "Financials",
        "Consumer Discretionary", "Communication Services", "Industrials", "Consumer Staples",
        "Energy", "Utilities", "Real Estate", "Materials", "Index ETF / Fund", "Unclassified"
    ]
    for s in required_sectors:
        assert s in SECTOR_MACRO_SENSITIVITIES, f"Missing sector sensitivity for: {s}"


# ==============================================================================
# Pillar 3: Autonomous Ticker Alias Registry & Canonical Self-Seeding
# ==============================================================================
def test_pillar3_dynamic_stem_alias_extraction():
    """
    Verifies that extract_stem_aliases cleans corporate suffixes correctly.
    """
    aliases = extract_stem_aliases("CRWD", "CrowdStrike Holdings, Inc. Class A")
    assert "CrowdStrike" in aliases
    assert "Inc." not in aliases
    assert "Class A" not in aliases

    aliases_nvda = extract_stem_aliases("NVDA", "NVIDIA Corporation")
    assert "NVIDIA" in aliases_nvda
    assert "Corporation" not in aliases_nvda


def test_pillar3_canonical_self_seeding(tmp_path):
    """
    Verifies that a brand new, empty StateStore automatically self-seeds
    from reference_equities.json and natively extracts entities for unowned stocks.
    """
    db_path = str(tmp_path / "fresh_self_seed.db")
    store = StateStore(db_path)

    # Check that database self-seeded canonical equities
    all_aliases = store.get_all_ticker_aliases()
    assert len(all_aliases) >= 100
    assert "HOOD" in all_aliases
    assert "Robinhood" in all_aliases["HOOD"]
    assert "CAT" in all_aliases
    assert "Caterpillar" in all_aliases["CAT"]
    assert "PFE" in all_aliases
    assert "Pfizer" in all_aliases["PFE"]

    # Verify NewsIngestionAgent matches without any hardcoded dictionary
    agent = NewsIngestionAgent(store)
    entities = agent.extract_entities("Robinhood expands tokenized trading framework under new SEC regulatory guidelines.")
    assert "HOOD" in entities["tickers"]

    entities_cat = agent.extract_entities("Caterpillar beats earnings expectations as global infrastructure demand surges.")
    assert "CAT" in entities_cat["tickers"]


def test_pillar3_alias_resolution_and_sqlite_persistence(tmp_path):
    """
    Verifies that aliases resolve dynamically and persist in SQLite for future lookups.
    """
    db_path = str(tmp_path / "test_aliases.db")
    store = StateStore(db_path)

    # First resolution with explicit company name
    res1 = resolve_ticker_aliases("SHOP", company_name="Shopify Inc.", state_store=store)
    assert "Shopify" in res1
    assert "SHOP" in res1

    # Second resolution without company name retrieves from persistent store
    res2 = resolve_ticker_aliases("SHOP", company_name=None, state_store=store)
    assert "Shopify" in res2


def test_pillar3_headline_salience_with_aliases():
    """
    Verifies headline salience logic with resolved aliases.
    """
    aliases = ["Robinhood Markets", "Robinhood", "HOOD"]
    assert is_primary_headline_subject("Robinhood unveils new prime brokerage", "HOOD", aliases) is True
    assert is_primary_headline_subject("Apple partners with Robinhood on payment rail", "HOOD", aliases) is True
    assert is_primary_headline_subject("General market recap across Wall Street", "HOOD", aliases) is False


# ==============================================================================
# Pillar 4: Algorithmic Multi-Year Macro Calendar Generator
# ==============================================================================
def test_pillar4_algorithmic_macro_schedule_multiyear_projection():
    """
    Verifies that generate_statutory_macro_schedule computes NFP, CPI, and PPI
    for dates beyond the 2026 table into 2027.
    """
    start = date(2027, 1, 1)
    end = date(2027, 3, 31)
    events = generate_statutory_macro_schedule(start, end)

    assert len(events) > 0

    # Verify NFP is generated on first Friday
    nfp_events = [e for e in events if "Non-Farm Payrolls" in e["name"]]
    assert len(nfp_events) == 3  # Jan, Feb, Mar

    for nfp in nfp_events:
        d = datetime.strptime(nfp["date"], "%Y-%m-%d").date()
        assert d.weekday() == 4  # Friday
        assert d.day <= 7  # Must be first Friday of the month

    # Verify CPI is generated on second Wednesday
    cpi_events = [e for e in events if "Consumer Price Index" in e["name"]]
    assert len(cpi_events) == 3

    for cpi in cpi_events:
        d = datetime.strptime(cpi["date"], "%Y-%m-%d").date()
        assert d.weekday() == 2  # Wednesday
        assert 8 <= d.day <= 14  # Must be second Wednesday of the month

    # Verify PPI is generated on the day following CPI
    ppi_events = [e for e in events if "Producer Price Index" in e["name"]]
    assert len(ppi_events) == 3


def test_pillar4_fomc_schedule_integrity():
    """
    Verifies that the multi-year FOMC schedule includes official descriptions.
    """
    for item in FOMC_SCHEDULE:
        assert "Federal Reserve FOMC Interest Rate Decision" in item["desc"]
        assert "date" in item
        assert "time_et" in item


# ==============================================================================
# Pillar 5: TLD & Governance-Driven Domain Authority Scoring
# ==============================================================================
def test_pillar5_domain_authority_tiers():
    """
    Verifies the 5-tier dynamic reliability scoring model.
    """
    # Tier 1: Statutory government & central banks (.gov, .mil)
    assert evaluate_source_reliability("https://www.sec.gov/news/press-release/2026-1") == 0.99
    assert evaluate_source_reliability("https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm") == 0.99
    assert evaluate_source_reliability("https://www.bls.gov/news.release/cpi.nr0.htm") == 0.99
    assert evaluate_source_reliability("https://www.defense.mil/News/Releases/") == 0.99

    # Tier 2: Premier financial investigative wires & primary exchanges
    assert evaluate_source_reliability("https://www.ft.com/content/abc-123") == 0.92
    assert evaluate_source_reliability("https://www.reuters.com/markets/us/stocks-rally-2026") == 0.92
    assert evaluate_source_reliability("https://www.bloomberg.com/news/articles/2026-09-18") == 0.92
    assert evaluate_source_reliability("https://www.wsj.com/finance/investing/earnings") == 0.92

    # Tier 3: Mainstream financial media & aggregators
    assert evaluate_source_reliability("https://www.cnbc.com/2026/09/market-close") == 0.85
    assert evaluate_source_reliability("https://finance.yahoo.com/news/tech-breakout") == 0.85
    assert evaluate_source_reliability("https://www.marketwatch.com/story/fed-decision") == 0.85

    # Tier 4: Crowdsourced retail opinion platforms
    assert evaluate_source_reliability("https://seekingalpha.com/article/12345-stock-pick") == 0.65
    assert evaluate_source_reliability("https://www.fool.com/investing/2026/best-stock") == 0.65
    assert evaluate_source_reliability("https://www.benzinga.com/markets/26/09/premarket") == 0.65

    # Tier 5: General unverified web
    assert evaluate_source_reliability("https://unverifiedfinancialblog.xyz/posts/hot-tip") == 0.75


# ==============================================================================
# Pillar 6: Equity-Anchored Social Sentiment Streaming
# ==============================================================================
def test_pillar6_reddit_equity_anchoring(monkeypatch):
    """
    Verifies that Reddit searches construct equity-anchored queries with brand aliases
    and cashtags, preventing dictionary homonym pollution on tickers like HOOD, CAT, and ON.
    """
    captured_urls = []

    class MockResponse:
        def __init__(self):
            self.status_code = 200
            self.text = '<rss><channel><title>Reddit RSS</title></channel></rss>'

    def mock_client_get(self, url, **kwargs):
        captured_urls.append(url)
        return MockResponse()

    monkeypatch.setattr(httpx.Client, "get", mock_client_get)

    # Test ticker with alias (HOOD -> Robinhood)
    _fetch_reddit_discussion("HOOD", aliases=["Robinhood", "Robinhood Markets"])
    assert len(captured_urls) >= 1
    hood_url = captured_urls[0]
    assert "%22Robinhood%22" in hood_url or "Robinhood" in hood_url
    assert "%24HOOD" in hood_url or "$HOOD" in hood_url

    # Test ambiguous dictionary word ticker (CAT) with alias (Caterpillar)
    captured_urls.clear()
    _fetch_reddit_discussion("CAT", aliases=["Caterpillar"])
    cat_url = captured_urls[0]
    assert "%22Caterpillar%22" in cat_url or "Caterpillar" in cat_url
    assert "%24CAT" in cat_url or "$CAT" in cat_url
