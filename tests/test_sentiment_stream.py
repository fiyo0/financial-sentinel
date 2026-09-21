"""
Unit tests for analytics/sentiment_stream.py.
Verifies StockTwits ingestion, Reddit RSS search parsing, RVOL calculations, and sentiment scoring.
"""
from unittest.mock import patch, MagicMock
from analytics.sentiment_stream import (
    fetch_social_sentiment_snapshot,
    _fetch_stocktwits_stream,
    SentimentSnapshot,
)


def test_sentiment_unavailable_fallback():
    with patch("analytics.sentiment_stream._fetch_stocktwits_stream") as mock_st, \
         patch("analytics.sentiment_stream._fetch_reddit_discussion") as mock_rd:
        mock_st.return_value = {"success": False, "total_messages": 0, "bulls": 0, "bears": 0, "bull_pct": 50.0, "sample_comments": []}
        mock_rd.return_value = {"count": 0, "sample_titles": []}

        snap = fetch_social_sentiment_snapshot("XYZ")
        assert snap.is_live is False
        assert snap.composite_sentiment_score == 50.0
        assert "Unavailable" in snap.to_summary_line()
        assert "currently unavailable" in snap.to_telegram_block()


def test_bullish_euphoria_with_high_rvol():
    with patch("analytics.sentiment_stream._fetch_stocktwits_stream") as mock_st, \
         patch("analytics.sentiment_stream._fetch_reddit_discussion") as mock_rd:
        mock_st.return_value = {
            "success": True,
            "total_messages": 35,
            "bulls": 25,
            "bears": 5,
            "bull_pct": 83.3,
            "rate_per_hour": 25.0,
            "acceleration_factor": 2.2,
            "span_hours": 1.4,
            "sample_comments": ["$NVDA breaking ATH again!", "Strong volume today"]
        }
        mock_rd.return_value = {
            "count": 8,
            "sample_titles": ["NVDA DD for earnings", "Why NVDA is not stopping"]
        }

        snap = fetch_social_sentiment_snapshot("NVDA", live_volume=120000000, avg_volume_20=60000000)
        assert snap.is_live is True
        assert snap.ticker == "NVDA"
        assert snap.relative_volume == 2.0  # 120M / 60M
        assert snap.social_velocity in ("SURGING", "HIGH_SURGE")
        assert snap.sentiment_verdict == "EXTREME_EUPHORIA"
        assert snap.composite_sentiment_score > 80.0
        assert snap.acceleration_factor == 2.2
        assert snap.format_recency_window() == "1.4h"
        assert len(snap.sample_stocktwits_comments) > 0
        assert len(snap.sample_reddit_headlines) > 0

        summary = snap.to_summary_line()
        assert "83% Bullish" in summary
        assert snap.social_velocity in summary
        assert "1.4h" in summary

        tg = snap.to_telegram_block()
        assert "Retail Social Sentiment" in tg
        assert "RVOL" in tg
        assert "Chatter" not in tg


def test_bearish_distrust():
    with patch("analytics.sentiment_stream._fetch_stocktwits_stream") as mock_st, \
         patch("analytics.sentiment_stream._fetch_reddit_discussion") as mock_rd:
        mock_st.return_value = {
            "success": True,
            "total_messages": 18,
            "bulls": 4,
            "bears": 14,
            "bull_pct": 22.2,
            "rate_per_hour": 8.0,
            "acceleration_factor": 1.5,
            "span_hours": 2.0,
            "sample_comments": ["Dump incoming", "Puts printing"]
        }
        mock_rd.return_value = {
            "count": 4,
            "sample_titles": ["TSLA losing margin", "Tesla deliveries fall"]
        }

        snap = fetch_social_sentiment_snapshot("TSLA", live_volume=30000000, avg_volume_20=30000000)
        assert snap.is_live is True
        assert snap.social_velocity == "ELEVATED"
        assert snap.sentiment_verdict == "EXTREME_CAPITULATION"
        assert snap.composite_sentiment_score <= 25.0


@patch("analytics.sentiment_stream.get_stocktwits_client")
def test_fetch_stocktwits_stream_parsing(mock_client_fn):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "messages": [
            {
                "body": "Going to the moon soon! Buy calls now",
                "entities": {"sentiment": {"basic": "Bullish"}}
            },
            {
                "body": "Too overbought, expecting a steep drop",
                "entities": {"sentiment": {"basic": "Bearish"}}
            },
            {
                "body": "Just holding my shares here patiently",
                "entities": {"sentiment": None}
            }
        ]
    }
    mock_client = MagicMock()
    mock_client.get.return_value = mock_resp
    mock_client_fn.return_value = mock_client

    data = _fetch_stocktwits_stream("AAPL")
    assert data["success"] is True
    assert data["total_messages"] == 3
    assert data["bulls"] == 1
    assert data["bears"] == 1
    assert data["bull_pct"] == 50.0


def test_recency_window_and_acceleration_logic():
    snap_mins = SentimentSnapshot(
        ticker="TEST",
        retail_bull_pct=60.0,
        retail_bear_pct=40.0,
        total_messages_analyzed=30,
        social_velocity="SURGING",
        reddit_post_count=0,
        span_hours=0.5,
        acceleration_factor=3.5
    )
    assert snap_mins.format_recency_window() == "30m"

    snap_days = SentimentSnapshot(
        ticker="CEG",
        retail_bull_pct=50.0,
        retail_bear_pct=50.0,
        total_messages_analyzed=30,
        social_velocity="DORMANT",
        reddit_post_count=0,
        span_hours=96.0,
        acceleration_factor=0.4
    )
    assert snap_days.format_recency_window() == "4.0d"
    summary = snap_days.to_summary_line()
    assert "4.0d" in summary


def test_apathy_gate_with_few_messages():
    """Verify that tickers with fewer than 8 messages trigger DORMANT_APATHY and institutional domain."""
    with patch("analytics.sentiment_stream._fetch_stocktwits_stream") as mock_st, \
         patch("analytics.sentiment_stream._fetch_reddit_discussion") as mock_rd:
        mock_st.return_value = {
            "success": True,
            "total_messages": 4,
            "is_sample_significant": False,
            "bulls": 3,
            "bears": 1,
            "bull_pct": 50.0,
            "rate_per_hour": 0.1,
            "acceleration_factor": 1.0,
            "span_hours": 40.0,
            "sample_comments": ["Quiet stock"]
        }
        mock_rd.return_value = {"count": 1, "sample_titles": ["Honeywell dividend"]}

        snap = fetch_social_sentiment_snapshot("HON", rvol=1.0)
        assert snap.is_live is True
        assert snap.is_sample_significant is False
        assert snap.social_velocity == "DORMANT_APATHY"
        assert snap.sentiment_verdict == "DORMANT_APATHY"
        assert snap.display_label == "Dormant / Insufficient Posts"
        assert "INSTITUTIONAL DOMAIN" in snap.contrarian_signal

        tg = snap.to_telegram_block()
        assert "DORMANT / APATHY" in tg
        assert "Insufficient retail presence" in tg
        assert "Contrarian Signal" in tg


def test_dynamic_polarity_and_recentered_baseline():
    """Verify that 62% is NEUTRAL_BASELINE and <45% displays XX% Bearish."""
    # 1. 62% Bullish should be Neutral Baseline
    with patch("analytics.sentiment_stream._fetch_stocktwits_stream") as mock_st, \
         patch("analytics.sentiment_stream._fetch_reddit_discussion") as mock_rd:
        mock_st.return_value = {
            "success": True,
            "total_messages": 20,
            "is_sample_significant": True,
            "bulls": 12,
            "bears": 8,
            "bull_pct": 62.0,
            "rate_per_hour": 5.0,
            "acceleration_factor": 1.0,
            "span_hours": 4.0,
            "sample_comments": []
        }
        mock_rd.return_value = {"count": 0, "sample_titles": []}

        snap_neutral = fetch_social_sentiment_snapshot("SPY", rvol=1.0)
        assert snap_neutral.sentiment_verdict == "NEUTRAL_BASELINE"
        assert "Neutral / Balanced" in snap_neutral.display_label
        assert "BALANCED PARTICIPATION" in snap_neutral.contrarian_signal

        # 2. 35% Bullish should display as 65% Bearish
        mock_st.return_value = {
            "success": True,
            "total_messages": 25,
            "is_sample_significant": True,
            "bulls": 7,
            "bears": 18,
            "bull_pct": 35.0,
            "rate_per_hour": 10.0,
            "acceleration_factor": 1.0,
            "span_hours": 2.5,
            "sample_comments": ["Selling off"]
        }
        snap_bearish = fetch_social_sentiment_snapshot("AMD", rvol=1.0)
        assert snap_bearish.sentiment_verdict == "BEARISH_DISTRUST"
        assert "65% Bearish" in snap_bearish.display_label
        assert "WALL OF WORRY" in snap_bearish.contrarian_signal
        tg_bear = snap_bearish.to_telegram_block()
        assert "🔴 <code>65% Bearish</code>" in tg_bear


def test_contrarian_signals_matrix():
    """Verify contrarian signaling across high-volume momentum vs exhaustion trap."""
    with patch("analytics.sentiment_stream._fetch_stocktwits_stream") as mock_st, \
         patch("analytics.sentiment_stream._fetch_reddit_discussion") as mock_rd:
        mock_rd.return_value = {"count": 0, "sample_titles": []}

        # Euphoria with high RVOL -> Institutional Momentum
        mock_st.return_value = {
            "success": True, "total_messages": 30, "is_sample_significant": True,
            "bulls": 25, "bears": 5, "bull_pct": 85.0, "rate_per_hour": 20.0,
            "acceleration_factor": 1.5, "span_hours": 1.5, "sample_comments": []
        }
        snap_mom = fetch_social_sentiment_snapshot("NVDA", rvol=1.5)
        assert "INSTITUTIONAL MOMENTUM" in snap_mom.contrarian_signal

        # Euphoria with low RVOL -> Retail Exhaustion Trap
        snap_trap = fetch_social_sentiment_snapshot("HYPE", rvol=0.70)
        assert snap_trap.sentiment_verdict == "RETAIL_DIVERGENCE_TRAP"
        assert "EXHAUSTION WARNING" in snap_trap.contrarian_signal


@patch("analytics.sentiment_stream.get_stocktwits_client")
def test_stocktwits_high_velocity_cursor_pagination(mock_client_fn):
    """Verify that when 30 messages span < 2 hours and cursor has max, page 2 is fetched."""
    mock_client = MagicMock()
    mock_client_fn.return_value = mock_client

    # Page 1: 30 messages over 30 minutes
    p1_msgs = [
        {"id": 100 + i, "created_at": f"2026-09-21T18:{30 - (i % 30):02d}:00Z", "body": "P1 post", "entities": {"sentiment": {"basic": "Bullish"}}}
        for i in range(30)
    ]
    resp1 = MagicMock()
    resp1.status_code = 200
    resp1.json.return_value = {"messages": p1_msgs, "cursor": {"max": 9999}}

    # Page 2: 10 messages from earlier
    p2_msgs = [
        {"id": 200 + i, "created_at": f"2026-09-21T17:{50 - (i % 50):02d}:00Z", "body": "P2 post", "entities": {"sentiment": {"basic": "Bullish"}}}
        for i in range(10)
    ]
    resp2 = MagicMock()
    resp2.status_code = 200
    resp2.json.return_value = {"messages": p2_msgs, "cursor": None}

    mock_client.get.side_effect = [resp1, resp2]

    res = _fetch_stocktwits_stream("NVDA")
    assert res["success"] is True
    assert res["total_messages"] == 40  # 30 from page 1 + 10 from page 2
    assert mock_client.get.call_count == 2


def test_classify_comments_sentiment_caching():
    """Verify that Gemini 2.5 Flash batch responses are cached with a 15-minute TTL."""
    from analytics.sentiment_stream import classify_comments_sentiment
    from storage.cache_manager import cache_manager

    test_comments = ["Great quarter", "Terrible margins, avoiding"]
    cache_manager.clear()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "candidates": [{
            "content": {
                "parts": [{"text": '["BULLISH", "BEARISH"]'}]
            }
        }]
    }

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        # Call 1: should call Gemini 2.5 Flash
        res1 = classify_comments_sentiment(test_comments, api_key="fake-key")
        assert res1 == ["BULLISH", "BEARISH"]
        assert mock_post.call_count == 1

        # Call 2: should hit in-memory cache without calling Gemini
        res2 = classify_comments_sentiment(test_comments, api_key="fake-key")
        assert res2 == ["BULLISH", "BEARISH"]
        assert mock_post.call_count == 1  # Still 1!

