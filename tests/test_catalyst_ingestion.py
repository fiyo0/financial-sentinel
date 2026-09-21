"""
Unit tests for SEC catalyst ingestion, dynamic ticker alias resolution,
headline salience scoring, anti-inflation clickbait filtering, and catalyst routing.
"""
from datetime import datetime
from unittest.mock import patch, MagicMock
from models import NewsItem, NewsCategory, Portfolio
from storage.state_store import StateStore
from agents.news_ingestion import (
    NewsIngestionAgent, resolve_ticker_aliases, extract_stem_aliases,
    is_primary_headline_subject, is_low_signal_clickbait,
    categorize_catalyst_provenance
)
from agents.analysis_agent import PortfolioAnalysisAgent


def test_algorithmic_stem_extraction():
    """Test universal corporate legal suffix stripping and brand root extraction."""
    # Test arbitrary US companies without hardcoded rules
    assert "Robinhood" in extract_stem_aliases("HOOD", "Robinhood Markets, Inc.")
    assert "Robinhood Markets" in extract_stem_aliases("HOOD", "Robinhood Markets, Inc.")
    assert "Palantir" in extract_stem_aliases("PLTR", "Palantir Technologies Inc.")
    assert "CrowdStrike" in extract_stem_aliases("CRWD", "CrowdStrike Holdings, Inc.")
    assert "Snowflake" in extract_stem_aliases("SNOW", "Snowflake Inc.")
    assert "Coinbase" in extract_stem_aliases("COIN", "Coinbase Global, Inc.")
    assert "Shopify" in extract_stem_aliases("SHOP", "Shopify Inc.")
    assert "Duolingo" in extract_stem_aliases("DUOL", "Duolingo, Inc.")
    assert "Airbnb" in extract_stem_aliases("ABNB", "Airbnb, Inc.")


def test_dynamic_alias_resolution_with_sqlite(tmp_path):
    """Test dynamic alias resolution and persistence in SQLite StateStore."""
    store = StateStore(str(tmp_path / "test_aliases.db"))

    # Resolve new company not in hardcoded dict
    aliases = resolve_ticker_aliases("CRWD", "CrowdStrike Holdings, Inc.", state_store=store)
    assert "CrowdStrike" in aliases
    assert "CRWD" in aliases

    # Verify cached in SQLite
    cached_db = store.get_ticker_aliases("CRWD")
    assert cached_db is not None
    assert "CrowdStrike" in cached_db

    # Subsequent call without company_name uses SQLite cache
    resolved_cached = resolve_ticker_aliases("CRWD", state_store=store)
    assert "CrowdStrike" in resolved_cached


def test_brand_name_entity_matching(tmp_path):
    """Test that headlines with company brand name but NO ticker symbol are matched."""
    store = StateStore(str(tmp_path / "test_brand.db"))
    agent = NewsIngestionAgent(store)

    # Article without $HOOD or HOOD
    text = "Robinhood expands tokenized trading framework under new SEC regulatory guidelines."
    entities = agent.extract_entities(text)
    assert "HOOD" in entities["tickers"]

    # Article with dynamically added ticker via SQLite
    store.save_ticker_aliases("SNOW", "Snowflake Inc.", ["Snowflake", "SNOW"])
    text2 = "Snowflake unveils new enterprise generative AI compute engine."
    entities2 = agent.extract_entities(text2)
    assert "SNOW" in entities2["tickers"]


def test_headline_salience_and_clickbait_detection():
    """Test distinguishing primary-subject headlines from incidental mentions and clickbait."""
    aliases = ["Robinhood Markets", "Robinhood", "HOOD"]

    # Primary headline subject
    assert is_primary_headline_subject("Robinhood surges 9% on SEC innovation exemption", "HOOD", aliases) is True
    assert is_primary_headline_subject("SEC grants tokenization exemption to Robinhood", "HOOD", aliases) is True

    # Incidental mention (headline is about general markets or macro)
    assert is_primary_headline_subject("Tech stocks rally as Treasury yields retreat", "HOOD", aliases) is False
    assert is_primary_headline_subject("S&P 500 closes at record high on soft-landing hopes", "HOOD", aliases) is False

    # Low-signal SEO clickbait listicles
    assert is_low_signal_clickbait("3 Stocks to Buy Right Now Before the Next Bull Run") is True
    assert is_low_signal_clickbait("Forget Nvidia: Buy This AI Stock Instead") is True
    assert is_low_signal_clickbait("Why Robinhood Soared Today") is True
    assert is_low_signal_clickbait("Is Robinhood a Buy Right Now?") is True
    assert is_low_signal_clickbait("5 Millionaire-Maker Stocks for 2026") is True

    # High-signal institutional headlines should NOT be marked clickbait
    assert is_low_signal_clickbait("SEC Issues Innovation Exemption for Tokenized Stock Trading") is False
    assert is_low_signal_clickbait("Federal Reserve Maintains Target Rate Range at 3.75%-4.00%") is False
    assert is_low_signal_clickbait("Robinhood Reports Q3 Net Interest Margin Expansion") is False


def test_catalyst_provenance_categorization():
    """Test provenance tagging across regulatory, earnings, and corporate actions."""
    sec_item = NewsItem(
        id="n1", title="SEC Issues Innovation Exemption",
        source="SEC Press Releases", url="https://www.sec.gov/news/pressreleases.rss",
        published_at=datetime.utcnow(), summary="Exemption granted",
        category=NewsCategory.SEC_FILING, source_reliability_score=0.99
    )
    assert categorize_catalyst_provenance(sec_item) == "REGULATORY / SEC ACTION"

    fed_item = NewsItem(
        id="n2", title="Federal Reserve FOMC Statement",
        source="federalreserve.gov", url="https://federalreserve.gov/press",
        published_at=datetime.utcnow(), summary="Rate decision",
        category=NewsCategory.MACRO, source_reliability_score=0.99
    )
    assert categorize_catalyst_provenance(fed_item) == "CENTRAL BANK / MACRO"

    earn_item = NewsItem(
        id="n3", title="Robinhood Q3 Earnings Beat Consensus by $0.08",
        source="Reuters", url="https://reuters.com",
        published_at=datetime.utcnow(), summary="Quarterly results",
        category=NewsCategory.EARNINGS, source_reliability_score=0.92
    )
    assert categorize_catalyst_provenance(earn_item) == "EARNINGS / GUIDANCE"

    corp_item = NewsItem(
        id="n4", title="Robinhood Launches Tokenized Equities for Retail Traders",
        source="CNBC", url="https://cnbc.com",
        published_at=datetime.utcnow(), summary="Product launch",
        category=NewsCategory.BREAKING, source_reliability_score=0.85
    )
    assert categorize_catalyst_provenance(corp_item) == "CORPORATE CATALYST"


def test_sec_fair_access_user_agent_headers(tmp_path):
    """Test that SEC requests use the declared Fair Access User-Agent."""
    store = StateStore(str(tmp_path / "test_sec.db"))
    agent = NewsIngestionAgent(store)

    sec_feed = {
        "name": "SEC Press Releases",
        "url": "https://www.sec.gov/news/pressreleases.rss",
        "category": "SEC_FILING"
    }

    captured_headers = {}

    def mock_get(url, headers=None, **kwargs):
        captured_headers.update(headers or {})
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """<?xml version="1.0"?>
        <rss version="2.0">
            <channel>
                <title>SEC Press Releases</title>
                <item>
                    <title>SEC Issues Exemption for Tokenized Securities</title>
                    <link>https://www.sec.gov/news/pressrelease/2026-99.htm</link>
                    <description>Commission order under Section 36</description>
                </item>
            </channel>
        </rss>"""
        return mock_resp

    with patch("httpx.Client.get", side_effect=mock_get):
        items = agent.fetch_live_feed(sec_feed)

    assert "FinancialSentinel/2.4" in captured_headers.get("User-Agent", "")
    assert "admin@financialsentinel.io" in captured_headers.get("User-Agent", "")
    assert len(items) == 1
    assert "Tokenized" in items[0].title


def test_single_ticker_news_prioritization_and_clickbait_sifting(tmp_path):
    """Test that analyze_single_ticker_structured selects primary ticker news over macro noise."""
    store = StateStore(str(tmp_path / "test_prio.db"))
    agent = PortfolioAnalysisAgent(state_store=store)

    portfolio = Portfolio(name="Test Portfolio", cash=10000.0, holdings=[])

    news_items = [
        # Macro filler / noise
        NewsItem(id="m1", title="US Housing Starts Fall in August", source="MarketWatch",
                 url="http://mw.com", published_at=datetime.utcnow(), summary="",
                 category=NewsCategory.MACRO, source_reliability_score=0.85, related_tickers=[]),
        NewsItem(id="m2", title="Treasury Yields Fluctuate Following Auctions", source="MarketWatch",
                 url="http://mw.com", published_at=datetime.utcnow(), summary="",
                 category=NewsCategory.MACRO, source_reliability_score=0.85, related_tickers=[]),
        # Clickbait listicle that mentions Robinhood in body
        NewsItem(id="c1", title="3 Stocks to Buy Right Now for Massive Gains", source="RetailBlog",
                 url="http://rb.com", published_at=datetime.utcnow(), summary="Robinhood is one of them.",
                 category=NewsCategory.BREAKING, source_reliability_score=0.65, related_tickers=["HOOD"]),
        # High-impact primary regulatory catalyst
        NewsItem(id="r1", title="SEC Issues Innovation Exemption for Tokenized Stock Trading", source="SEC Press Releases",
                 url="https://www.sec.gov", published_at=datetime.utcnow(), summary="Commission ruling",
                 category=NewsCategory.SEC_FILING, source_reliability_score=0.99, related_tickers=["HOOD"]),
        # Primary ticker news without $HOOD in title
        NewsItem(id="t1", title="Robinhood Surges as Tokenized Equity Trading Clears Regulatory Hurdles", source="Reuters",
                 url="https://reuters.com", published_at=datetime.utcnow(), summary="Shares jump 9%",
                 category=NewsCategory.BREAKING, source_reliability_score=0.92, related_tickers=["HOOD"]),
    ]

    captured_prompt = {}

    def mock_query(prompt, system_instruction=None, api_key=None):
        captured_prompt["prompt"] = prompt
        return {
            "verdict": "BULLISH",
            "conviction_score": 88.0,
            "thesis": "Robinhood tokenization catalyst approved.",
            "catalysts": ["SEC Tokenization Innovation Exemption", "Retail crypto surge"],
            "risks": ["Regulatory reversal"],
            "target_price": 38.0,
            "stop_floor": 26.0,
            "suggested_allocation_usd": 1500.0,
            "telegram_html": "🔬 <b>STOCK ANALYSIS: HOOD (Robinhood Markets)</b>\n\nBullish thesis."
        }

    agent.query_llm_json = mock_query

    quote = {"name": "Robinhood Markets, Inc.", "current_price": 28.50, "sector": "Financials"}
    res = agent.analyze_single_ticker_structured(
        ticker="HOOD",
        portfolio=portfolio,
        news_items=news_items,
        quote_data=quote,
        api_key="test_key"
    )

    prompt_text = captured_prompt.get("prompt", "")

    # Verify primary ticker and SEC actions are in the prompt
    assert "Robinhood Surges as Tokenized Equity Trading Clears Regulatory Hurdles" in prompt_text
    assert "SEC Issues Innovation Exemption for Tokenized Stock Trading" in prompt_text
    assert "[REGULATORY / SEC ACTION]" in prompt_text

    # Verify low-signal clickbait was excluded
    assert "3 Stocks to Buy Right Now for Massive Gains" not in prompt_text

    # Verify anti-inflation directive is present in prompt
    assert "CRITICAL CATALYST & PROVENANCE SIFTING DIRECTIVE" in prompt_text
    assert "PRIMARY COMPANY CATALYSTS vs. INCIDENTAL MENTIONS" in prompt_text


def test_equity_context_validation_and_disambiguation():
    """Test universal equity context validation to prevent homonym pollution on dictionary-word tickers."""
    from agents.news_ingestion import matches_ticker_equity_context

    # HOOD (Robinhood)
    hood_aliases = ["Robinhood", "Robinhood Markets", "HOOD"]
    assert matches_ticker_equity_context("Soldier injured in Fort Hood training exercise", "HOOD", hood_aliases) is False
    assert matches_ticker_equity_context("Winter jacket with detachable hood on sale", "HOOD", hood_aliases) is False
    assert matches_ticker_equity_context("Robinhood Markets climbs 9% amid tokenized stock trading framework", "HOOD", hood_aliases) is True
    assert matches_ticker_equity_context("Traders accumulate $HOOD call options into earnings", "HOOD", hood_aliases) is True
    assert matches_ticker_equity_context("HOOD stock gains 4% as brokerage reports net deposit growth", "HOOD", hood_aliases) is True

    # CAT (Caterpillar)
    cat_aliases = ["Caterpillar", "CAT"]
    assert matches_ticker_equity_context("Firefighters rescue local cat stuck in neighborhood tree", "CAT", cat_aliases) is False
    assert matches_ticker_equity_context("Caterpillar boosts quarterly dividend by 8%", "CAT", cat_aliases) is True
    assert matches_ticker_equity_context("Institutional funds buy $CAT shares ahead of infrastructure bill", "CAT", cat_aliases) is True

    # ON (ON Semiconductor)
    on_aliases = ["ON Semiconductor", "ON"]
    assert matches_ticker_equity_context("Power grid turned back on after brief outage", "ON", on_aliases) is False
    assert matches_ticker_equity_context("ON Semiconductor beats EPS estimates on automotive silicon demand", "ON", on_aliases) is True
    assert matches_ticker_equity_context("$ON rallies 5% on semiconductor expansion", "ON", on_aliases) is True


def test_word_boundary_regulatory_categorization():
    """Test that words like 'sector', 'second', 'security' do not falsely trigger SEC categorization."""
    store = StateStore(":memory:")
    agent = NewsIngestionAgent(store)

    # False positive traps with substring 'sec'
    assert agent.infer_category("Tech Sector Faces Pressure", "Overview of the technology sector", "BREAKING") != NewsCategory.SEC_FILING
    assert agent.infer_category("Second Quarter GDP Growth Accelerates", "Economic data shows strength", "BREAKING") != NewsCategory.SEC_FILING
    assert agent.infer_category("Cyber Security Spending Rises", "Enterprises increase security software budget", "BREAKING") != NewsCategory.SEC_FILING

    item_sector = NewsItem(id="s1", title="Technology Sector Outlook", source="MarketWatch", url="", published_at=datetime.utcnow(), summary="")
    assert categorize_catalyst_provenance(item_sector) != "REGULATORY / SEC ACTION"

    # Genuine SEC items
    assert agent.infer_category("SEC Issues Innovation Exemption Order", "Commission provides exemptive relief", "BREAKING") == NewsCategory.SEC_FILING
    assert agent.infer_category("Securities and Exchange Commission Proposes New Rules", "Agency modernizes framework", "BREAKING") == NewsCategory.SEC_FILING

    item_sec = NewsItem(id="s2", title="SEC Announces 24-Hour Trading Roundtable", source="SEC Press Releases", url="", published_at=datetime.utcnow(), summary="")
    assert categorize_catalyst_provenance(item_sec) == "REGULATORY / SEC ACTION"


def test_clickbait_override_for_structural_catalysts():
    """Test that legitimate news with rhetorical questions is exempted from clickbait filtering when structural catalysts are present."""
    # Wire report ending with rhetorical buy question but reporting landmark SEC order
    wire_headline = "SEC Opens U.S. Door to Tokenized Stocks: Is Robinhood a Buy Now?"
    assert is_low_signal_clickbait(wire_headline) is False

    # Structural regulatory / corporate catalyst headlines
    assert is_low_signal_clickbait("SEC Issues Innovation Exemption to Facilitate Tokenized Equities") is False
    assert is_low_signal_clickbait("DOJ Antitrust Division Launches Probe into Big Tech") is False
    assert is_low_signal_clickbait("Biotech Secures FDA Approval for Groundbreaking Therapy") is False

    # Low-signal speculative clickbait without structural catalysts MUST be filtered
    assert is_low_signal_clickbait("Is Robinhood a Buy Right Now?") is True
    assert is_low_signal_clickbait("3 Stocks to Buy Today for Huge Returns") is True
    assert is_low_signal_clickbait("Why Tesla Shares Jumped Today") is True


def test_regulatory_deduplication_and_policy_prioritization():
    """Test that multiple speech transcripts from the same event are clustered and formal orders are prioritized."""
    from agents.analysis_agent import deduplicate_and_prioritize_regulatory_items

    items = [
        NewsItem(id="r1", title="Remarks at the 24-Hour Trading Roundtable", source="SEC Statements",
                 url="", published_at=datetime(2026, 9, 17, 10, 16), summary="", category=NewsCategory.SEC_FILING),
        NewsItem(id="r2", title="Stock Around the Clock: Remarks at the Roundtable on Preparations for 24-Hour Trading", source="SEC Statements",
                 url="", published_at=datetime(2026, 9, 17, 10, 15), summary="", category=NewsCategory.SEC_FILING),
        NewsItem(id="r3", title="Remarks at the Roundtable on Preparations for 24-Hour Trading", source="SEC Statements",
                 url="", published_at=datetime(2026, 9, 17, 10, 14), summary="", category=NewsCategory.SEC_FILING),
        NewsItem(id="r4", title="Remarks at the SEC Roundtable on 24-Hour Trading", source="SEC Statements",
                 url="", published_at=datetime(2026, 9, 17, 10, 3), summary="", category=NewsCategory.SEC_FILING),
        NewsItem(id="r5", title="SEC Issues Innovation Exemption to Facilitate the Trading of Tokenized NMS Stock", source="SEC Press Releases",
                 url="", published_at=datetime(2026, 9, 17, 9, 20), summary="", category=NewsCategory.SEC_FILING),
    ]

    curated = deduplicate_and_prioritize_regulatory_items(items, max_items=4)

    # 1. Innovation Exemption MUST be first or included due to high priority score (formal exemption/order)
    titles = [c.title for c in curated]
    assert any("Innovation Exemption" in t for t in titles)

    # 2. Speeches from the 24-hour trading roundtable MUST be clustered so they do not exhaust all slots
    roundtable_speeches = [t for t in titles if "24-Hour Trading" in t or "24‑Hour Trading" in t]
    assert len(roundtable_speeches) == 1, f"Expected exactly 1 clustered roundtable speech, got: {roundtable_speeches}"


def test_state_store_get_recent_regulatory_news(tmp_path):
    """Test that StateStore correctly retrieves regulatory bulletins."""
    store = StateStore(str(tmp_path / "test_reg_store.db"))

    item_sec = NewsItem(
        id="reg_1", title="SEC Grants Innovation Exemption for Tokenized Securities",
        source="SEC Press Releases", url="https://sec.gov", published_at=datetime.utcnow(),
        summary="Exemption granted", category=NewsCategory.SEC_FILING, source_reliability_score=0.99
    )
    item_macro = NewsItem(
        id="reg_2", title="Federal Reserve FOMC Policy Decision",
        source="federalreserve.gov", url="https://federalreserve.gov", published_at=datetime.utcnow(),
        summary="Rate announcement", category=NewsCategory.MACRO, source_reliability_score=0.99
    )
    item_retail = NewsItem(
        id="reg_3", title="Local retail store expands footprint in Ohio",
        source="LocalGazette", url="https://local.com", published_at=datetime.utcnow(),
        summary="Store opening", category=NewsCategory.BREAKING, source_reliability_score=0.70
    )

    store.save_news_item(item_sec)
    store.save_news_item(item_macro)
    store.save_news_item(item_retail)

    reg_news = store.get_recent_regulatory_news(hours=24, limit=10)
    reg_titles = [r.title for r in reg_news]

    assert len(reg_news) == 2
    assert "SEC Grants Innovation Exemption for Tokenized Securities" in reg_titles
    assert "Federal Reserve FOMC Policy Decision" in reg_titles
    assert "Local retail store expands footprint in Ohio" not in reg_titles


def test_algorithmic_nlp_topic_clustering_across_industries():
    """Test that are_headlines_same_event_cluster dynamically clusters arbitrary events across industries without hardcoded terms."""
    from agents.analysis_agent import are_headlines_same_event_cluster

    # Biopharma / FDA event clustering
    fda_1 = "FDA Advisory Committee Votes in Favor of Alzheimer's Treatment"
    fda_2 = "Briefing Document: FDA Advisory Committee Meeting on Alzheimer's Drug Candidate"
    fda_unrelated = "FDA Approves Pediatric Vaccine for Respiratory Illness"
    assert are_headlines_same_event_cluster(fda_1, fda_2) is True
    assert are_headlines_same_event_cluster(fda_1, fda_unrelated) is False

    # Antitrust / FTC / DOJ clustering
    antitrust_1 = "FTC Sues Tech Conglomerate Over Anti-Competitive Cloud Software Practices"
    antitrust_2 = "Statement by FTC Chair on Antitrust Lawsuit Targeting Cloud Computing Practices"
    sec_unrelated = "SEC Charges Private Fund Manager in Ponzi Scheme"
    assert are_headlines_same_event_cluster(antitrust_1, antitrust_2) is True
    assert are_headlines_same_event_cluster(antitrust_1, sec_unrelated) is False

    # Central Bank / FOMC clustering
    fed_1 = "Federal Reserve Issues FOMC Monetary Policy Statement"
    fed_2 = "Implementation Note Issued Regarding FOMC Policy Decision"
    assert are_headlines_same_event_cluster(fed_1, fed_2) is True


def test_news_item_datetime_utc_coercion():
    """Verify that NewsItem, Portfolio, and BriefingReport automatically coerce naive datetimes to UTC."""
    from datetime import datetime, timezone
    from models import NewsItem, Portfolio, BriefingReport

    # 1. Default factories must produce timezone-aware UTC datetimes
    item_default = NewsItem(id="d1", title="Title", source="src", url="http://x.com", summary="sum")
    assert item_default.published_at.tzinfo is not None
    assert item_default.published_at.tzinfo == timezone.utc

    port_default = Portfolio()
    assert port_default.last_updated.tzinfo is not None
    assert port_default.last_updated.tzinfo == timezone.utc

    rep_default = BriefingReport(report_id="r1", executive_summary="Summary", total_holdings_monitored=5)
    assert rep_default.generated_at.tzinfo is not None
    assert rep_default.generated_at.tzinfo == timezone.utc

    # 2. Naive datetimes passed explicitly must be coerced to UTC
    naive_dt = datetime(2026, 9, 20, 12, 30, 0)
    item_naive = NewsItem(id="n1", title="Title", source="src", url="http://x.com", summary="sum", published_at=naive_dt)
    assert item_naive.published_at.tzinfo == timezone.utc
    assert item_naive.published_at.hour == 12

    port_naive = Portfolio(last_updated=naive_dt)
    assert port_naive.last_updated.tzinfo == timezone.utc

    rep_naive = BriefingReport(report_id="r2", executive_summary="Sum", total_holdings_monitored=2, generated_at=naive_dt)
    assert rep_naive.generated_at.tzinfo == timezone.utc


def test_deduplicate_and_prioritize_regulatory_items_mixed_naive_and_aware():
    """
    Verify deduplicate_and_prioritize_regulatory_items safely handles a mix of offset-naive
    and offset-aware datetimes with identical priority scores without raising TypeError.
    """
    from datetime import datetime, timezone
    from agents.analysis_agent import deduplicate_and_prioritize_regulatory_items
    from models import NewsItem, NewsCategory

    # Create items that yield identical priority scores (_priority_score == 4)
    item_aware = NewsItem(
        id="aware_1",
        title="SEC Issues Commission Order Approving Trading Facilities",
        source="SEC Press Releases",
        url="https://sec.gov/1",
        published_at=datetime(2026, 9, 20, 14, 0, tzinfo=timezone.utc),
        summary="Commission order",
        category=NewsCategory.SEC_FILING
    )

    item_naive = NewsItem(
        id="naive_1",
        title="SEC Grants Innovation Exemption for Tokenized Securities",
        source="SEC Press Releases",
        url="https://sec.gov/2",
        published_at=datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc),
        summary="Exemption order",
        category=NewsCategory.SEC_FILING
    )
    # Simulate a raw or legacy object with an offset-naive datetime bypassing pydantic
    object.__setattr__(item_naive, "published_at", datetime(2026, 9, 20, 10, 0))

    # Also test an item with published_at = None
    item_none = NewsItem(
        id="none_1",
        title="SEC Adopts Final Rule on Reporting Standards",
        source="SEC Press Releases",
        url="https://sec.gov/3",
        summary="Rule adoption",
        category=NewsCategory.SEC_FILING
    )
    object.__setattr__(item_none, "published_at", None)

    # Sorting items with identical scores must not crash with TypeError: can't compare offset-naive and offset-aware datetimes
    curated = deduplicate_and_prioritize_regulatory_items([item_aware, item_naive, item_none], max_items=5)
    assert len(curated) == 3
    # The aware item with the later timestamp (14:00) should appear before the naive item (10:00)
    assert curated[0].id == "aware_1"
    assert curated[1].id == "naive_1"
    assert curated[2].id == "none_1"


def test_state_store_datetime_awareness_and_iso_normalization(tmp_path):
    """
    Verify StateStore parses ISO strings with 'Z', offset-naive strings,
    and offset-aware strings as timezone-aware UTC datetime objects.
    """
    import sqlite3
    from datetime import datetime, timezone
    from storage.state_store import StateStore
    from models import NewsCategory

    db_file = str(tmp_path / "tz_test.db")
    store = StateStore(db_file)

    # Directly insert raw rows with different ISO datetime shapes into SQLite
    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        now_str = datetime.now(timezone.utc).isoformat()
        cursor.execute("""
            INSERT INTO ingested_news (id, raw_hash, title, source, url, published_at, category, reliability_score, related_tickers, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "iso_z", "hash_z", "SEC Orders Action On Exchange Platform Z",
            "SEC Press Releases", "https://sec.gov/z", "2026-09-20T10:00:00Z",
            NewsCategory.SEC_FILING.value, 0.95, '["SRRK"]', now_str
        ))
        cursor.execute("""
            INSERT INTO ingested_news (id, raw_hash, title, source, url, published_at, category, reliability_score, related_tickers, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "iso_naive", "hash_naive", "SEC Issues Exemptive Order For Biotech Issuer",
            "SEC Press Releases", "https://sec.gov/naive", "2026-09-20 09:30:00",
            NewsCategory.SEC_FILING.value, 0.95, '["SRRK"]', now_str
        ))
        cursor.execute("""
            INSERT INTO ingested_news (id, raw_hash, title, source, url, published_at, category, reliability_score, related_tickers, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "iso_aware", "hash_aware", "SRRK Announces Positive Phase 3 Clinical Moat",
            "BusinessWire", "https://bw.com/srrk", "2026-09-20T11:00:00+00:00",
            NewsCategory.BREAKING.value, 0.90, '["SRRK"]', now_str
        ))
        conn.commit()

    # Retrieve via get_recent_news
    recent = store.get_recent_news(hours=48, limit=10)
    assert len(recent) == 3
    for it in recent:
        assert it.published_at is not None
        assert it.published_at.tzinfo is not None, f"Item {it.id} published_at must be timezone-aware"

    # Retrieve via get_recent_regulatory_news
    reg = store.get_recent_regulatory_news(hours=48, limit=10)
    assert len(reg) == 2
    for r in reg:
        assert r.published_at.tzinfo is not None, f"Regulatory item {r.id} published_at must be timezone-aware"

    # Retrieve via get_recent_news_for_ticker
    ticker_news = store.get_recent_news_for_ticker("SRRK", hours=48, limit=10)
    assert len(ticker_news) == 3
    for tn in ticker_news:
        assert tn.published_at.tzinfo is not None


