"""
Unit tests for SEC catalyst ingestion, dynamic ticker alias resolution,
headline salience scoring, anti-inflation clickbait filtering, and catalyst routing.
"""
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock
from models import NewsItem, NewsCategory, Portfolio, PortfolioHolding
from storage.state_store import StateStore
from agents.news_ingestion import (
    NewsIngestionAgent, resolve_ticker_aliases, extract_stem_aliases,
    is_primary_headline_subject, is_low_signal_clickbait,
    categorize_catalyst_provenance, COMMON_TICKER_ALIASES
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

    with patch("httpx.get", side_effect=mock_get):
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
