from analytics.market_data import fetch_market_overview
from agents.news_ingestion import NewsIngestionAgent
from models import NewsItem, NewsCategory, Portfolio
from agents.opportunity_agent import OpportunityDiscoveryAgent
from agents.market_briefing_agent import MarketBriefingAgent


def test_fetch_market_overview_fails_loudly_on_fetch_failure(monkeypatch):
    """Verify fetch_market_overview flags is_live=False and populates fields_unavailable rather than faking 0.0 price."""
    from analytics import market_data

    def mock_fetch_quote(ticker):
        if ticker == "SPY":
            raise RuntimeError("Upstream provider 503 Service Unavailable")
        return {
            "ticker": ticker,
            "current_price": 450.0,
            "change_pct": 0.5,
            "is_live": True,
            "name": ticker,
        }

    monkeypatch.setattr(market_data, "fetch_live_quote", mock_fetch_quote)
    overview = fetch_market_overview()

    spy_res = overview["indices"]["SPY"]
    assert spy_res["is_live"] is False
    assert spy_res["current_price"] is None
    assert spy_res["change_pct"] is None
    assert "Failed fetching benchmark quote" in spy_res["error"]

    assert overview["market_tone"] == "Unavailable"
    assert "SPY" in overview["fields_unavailable"]


def test_news_ingestion_uses_pooled_http_client(monkeypatch):
    """Verify fetch_live_feed acquires the persistent HTTP/2 connection pool."""
    agent = NewsIngestionAgent()
    pool_called = []

    class MockResponse:
        status_code = 200
        text = "<rss><channel><title>Mock Feed</title></channel></rss>"
        headers = {"content-type": "application/rss+xml"}

    class MockHttpClient:
        def get(self, url, **kwargs):
            pool_called.append(url)
            return MockResponse()

    from analytics import market_data
    monkeypatch.setattr(market_data, "get_market_http_client", lambda: MockHttpClient())

    res = agent.fetch_live_feed({"name": "test_feed", "url": "https://example.com/rss.xml"})
    assert len(pool_called) == 1
    assert pool_called[0] == "https://example.com/rss.xml"
    assert isinstance(res, list)


def test_opportunity_agent_symmetrical_delimiters(monkeypatch):
    """Verify OpportunityDiscoveryAgent encapsulates untrusted headlines with matching closing tags."""
    agent = OpportunityDiscoveryAgent()
    captured = []

    monkeypatch.setattr(agent, "query_llm_json", lambda prompt, *args, **kwargs: captured.append(prompt) or {"opportunities": []})
    item = NewsItem(
        id="news_1",
        title="Breaking: Tech earnings surge",
        source="Reuters",
        url="https://reuters.com/tech",
        summary="Positive tech quarter announced.",
        category=NewsCategory.BREAKING
    )
    portfolio = Portfolio(name="Test", cash=50000.0, holdings=[])
    agent.scan_opportunities([item], portfolio, api_key="dummy_key")

    assert len(captured) == 1
    prompt = captured[0]
    assert "<<<UNTRUSTED_HEADLINE source=\"Reuters\">>>" in prompt
    assert "<<</UNTRUSTED_HEADLINE>>>" in prompt


def test_briefing_agent_symmetrical_delimiters():
    """Verify MarketBriefingAgent formats headlines with symmetrical closing tags."""
    agent = MarketBriefingAgent()
    item = NewsItem(
        id="news_1",
        title="Fed Signals Steady Rates",
        source="Bloomberg",
        url="https://bloomberg.com/fed",
        summary="Rates remain steady.",
        category=NewsCategory.MACRO
    )
    formatted = agent._format_news_summary([item])
    assert "<<<UNTRUSTED_HEADLINE source=\"Bloomberg\">>>" in formatted
    assert "<<</UNTRUSTED_HEADLINE>>>" in formatted
