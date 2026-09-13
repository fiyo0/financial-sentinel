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
