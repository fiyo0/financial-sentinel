"""
Phase 3 Verification: Canonical Registry-First Resolution & Externalized Dynamic Sector Taxonomy.
Verifies:
1. Zero corporate brand names in generic sector taxonomy.
2. Canonical Registry-First resolution across news and market data.
3. Macro thematic news classification via externalized taxonomy.
4. LLM semantic zero-shot fallback on novel/unseen macro headlines.
"""
import os
import json

from storage.state_store import StateStore
from agents.news_ingestion import NewsIngestionAgent
from analytics.market_data import classify_equity_sector, update_portfolio_live_prices
from models import Portfolio, PortfolioHolding


def test_taxonomy_contains_zero_corporate_names():
    """
    Verifies that storage/sector_taxonomy.json contains strictly thematic/industry
    terminology and ZERO corporate brand names or ticker symbols.
    """
    tax_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage", "sector_taxonomy.json")
    ref_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage", "reference_equities.json")

    assert os.path.exists(tax_path), "storage/sector_taxonomy.json must exist"
    assert os.path.exists(ref_path), "storage/reference_equities.json must exist"

    with open(tax_path, "r", encoding="utf-8") as f:
        taxonomy = json.load(f)

    with open(ref_path, "r", encoding="utf-8") as f:
        reference_equities = json.load(f)

    # Collect known corporate brand names that are distinct from generic English dictionary words
    corporate_brands = set()
    for eq in reference_equities:
        for alias in eq.get("aliases", []):
            clean = alias.strip().lower()
            # Check non-generic brand names
            if clean in ("caterpillar", "microsoft", "google", "alphabet", "netflix", "boeing",
                         "pfizer", "cisco", "intel", "nvidia", "broadcom", "qualcomm",
                         "salesforce", "oracle", "uber", "disney", "palantir", "crowdstrike",
                         "robinhood", "coinbase", "microstrategy", "booking"):
                corporate_brands.add(clean)

    # Assert that none of these corporate brand names exist anywhere in sector taxonomy
    for sector_name, data in taxonomy.items():
        macro_kws = [k.lower() for k in data.get("macro_keywords", [])]
        for brand in corporate_brands:
            assert brand not in macro_kws, (
                f"Corporate brand name '{brand}' found in generic sector taxonomy for '{sector_name}'! "
                f"Companies must be resolved via the Canonical Registry, not generic taxonomy."
            )


def test_caterpillar_resolves_strictly_via_canonical_registry(tmp_path):
    """
    Verifies that 'Caterpillar' resolves to CAT and 'Industrials' strictly via
    Canonical Registry entity alias matching and sector propagation, without 'caterpillar'
    being present in the generic keyword list.
    """
    db_path = str(tmp_path / "reg_test.db")
    store = StateStore(db_path=db_path)
    agent = NewsIngestionAgent(state_store=store)

    tax = store.get_sector_taxonomy()
    industrials_kws = [k.lower() for k in tax.get("Industrials", {}).get("macro_keywords", [])]
    assert "caterpillar" not in industrials_kws, "Caterpillar must not be in Industrials keywords"

    # Ingestion test
    res = agent.extract_entities("Caterpillar announces quarterly dividend increase for shareholders.")
    assert "CAT" in res["tickers"]
    assert "Industrials" in res["sectors"]


def test_classify_equity_sector_registry_first():
    """
    Verifies that classify_equity_sector uses Canonical Registry-First resolution.
    Even when the company name contains ZERO sector keywords, the canonical ticker
    determines the authoritative GICS sector.
    """
    # CAT has "Industrials" in registry, even with completely generic/misleading name
    assert classify_equity_sector("CAT", "Global Heavy Holdings Vehicle Inc.") == "Industrials"

    # NEE has "Utilities" in registry
    assert classify_equity_sector("NEE", "Capital Reserve Corporation") == "Utilities"

    # NVDA has "Semiconductors" in registry
    assert classify_equity_sector("NVDA", "Advanced Silicon Architecture LLC") == "Semiconductors"

    # JPM has "Financials" in registry
    assert classify_equity_sector("JPM", "Strategic Liquidity Group") == "Financials"

    # SPY has "Index ETF / Fund" in registry
    assert classify_equity_sector("SPY", "Universal Market Basket") == "Index ETF / Fund"


def test_macro_thematic_news_extraction_via_external_taxonomy():
    """
    Verifies that macro/thematic news headlines containing no tickers or company names
    are accurately mapped to the appropriate GICS sector via storage/sector_taxonomy.json.
    """
    agent = NewsIngestionAgent()

    # 1. Industrials
    res_ind = agent.extract_entities("Global freight railroad and cargo shipping logistics experience container delays.")
    assert "Industrials" in res_ind["sectors"]

    # 2. Materials
    res_mat = agent.extract_entities("Lithium and copper mining operations face environmental compliance reviews.")
    assert "Materials" in res_mat["sectors"]

    # 3. Utilities
    res_utl = agent.extract_entities("Electric utility operators upgrade regional power grid to handle summer heatwave.")
    assert "Utilities" in res_utl["sectors"]

    # 4. Real Estate
    res_re = agent.extract_entities("Commercial real estate REIT reports 95% occupancy rate across property leases.")
    assert "Real Estate" in res_re["sectors"]

    # 5. Consumer Staples
    res_cs = agent.extract_entities("Supermarket and grocery chains stock up on packaged food and beverages.")
    assert "Consumer Staples" in res_cs["sectors"]

    # 6. Consumer Discretionary
    res_cd = agent.extract_entities("Luxury apparel retailer and restaurant operator reports strong holiday traffic.")
    assert "Consumer Discretionary" in res_cd["sectors"]


def test_llm_zero_shot_fallback_for_unseen_macro_news(monkeypatch):
    """
    Verifies that novel or nuanced macro headlines that contain NO tickers and NO exact keywords
    fall back to the LLM semantic zero-shot classifier.
    """
    agent = NewsIngestionAgent()
    agent.use_llm = True

    # Obscure headline without any keyword matches from taxonomy
    obscure_headline = "Sovereign liquidity backstop mechanism implemented for bilateral swap arrangements."

    def fake_llm_call(prompt, **kwargs):
        assert "Sovereign liquidity backstop" in prompt
        return {"sector": "Financials", "confidence": 0.94}

    monkeypatch.setattr(agent, "query_llm_json", fake_llm_call)
    monkeypatch.setattr("config.config.gemini_api_key", "test_gemini_key_active")

    res = agent.extract_entities(obscure_headline)
    assert "Financials" in res["sectors"]


def test_t3_5_netflix_and_northern_trust_not_funds():
    """T3.5: Netflix and Northern Trust are not falsely classified as Index ETF / Fund."""
    sec_nflx = classify_equity_sector("NFLX", "Netflix, Inc.")
    assert sec_nflx != "Index ETF / Fund"

    sec_ntrs = classify_equity_sector("NTRS", "Northern Trust Corp")
    assert sec_ntrs != "Index ETF / Fund"

    sec_spy = classify_equity_sector("SPY", "SPDR S&P 500 ETF Trust")
    assert sec_spy == "Index ETF / Fund"


def test_t3_5_user_sector_preserved_on_quote_refresh(monkeypatch):
    """T3.5: User-assigned sector is not overwritten when live quote returns Unclassified or another sector."""
    portfolio = Portfolio(
        name="Custom Sector Portfolio",
        cash=1000.0,
        holdings=[
            PortfolioHolding(
                ticker="CUSTOM",
                name="Custom Company",
                shares=10,
                avg_price=50.0,
                current_price=50.0,
                sector="Custom Healthcare"
            )
        ]
    )

    def mock_fetch_quote(ticker):
        return {
            "ticker": ticker,
            "name": "Custom Company",
            "current_price": 55.0,
            "change_pct": 10.0,
            "sector": "Financials",  # Attempt to overwrite
            "is_live": True
        }

    monkeypatch.setattr("analytics.market_data.fetch_live_quote", mock_fetch_quote)
    updated_p, failed = update_portfolio_live_prices(portfolio)

    assert len(failed) == 0
    # Custom sector must be preserved because holding.sector was already set and not "Unclassified"
    assert updated_p.holdings[0].sector == "Custom Healthcare"
    assert updated_p.holdings[0].current_price == 55.0

