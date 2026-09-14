"""
Unit tests for FinancialSentinelTelegramBot command routing,
specifically testing the /<ticker> shorthand feature and auto-archiving.
"""
import os
import pytest
from unittest.mock import patch
from orchestrator import FinancialSentinelOrchestrator
from channels.telegram_bot import FinancialSentinelTelegramBot, RESERVED_COMMANDS


@pytest.fixture
def temp_orchestrator(tmp_path):
    db_path = str(tmp_path / "test_telegram.db")
    return FinancialSentinelOrchestrator(db_path=db_path)


@pytest.fixture
def sample_portfolio_obj(temp_orchestrator):
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data/sample_portfolio.json")
    return temp_orchestrator.load_portfolio_from_file(path)


@pytest.fixture
def telegram_test_setup(temp_orchestrator, sample_portfolio_obj):
    temp_orchestrator.persist_active_portfolio(sample_portfolio_obj)
    admin = temp_orchestrator.state_store.get_or_create_default_admin()
    bot = FinancialSentinelTelegramBot(orchestrator=temp_orchestrator)

    sent_messages = []
    edited_messages = []

    bot.send_message = lambda msg, chat_id=None: sent_messages.append({"text": msg, "chat_id": chat_id})
    bot.send_message_returning_id = lambda msg, chat_id=None: 999
    bot.edit_message = lambda msg, chat_id=None, msg_id=None: edited_messages.append({"text": msg, "chat_id": chat_id, "id": msg_id})

    return bot, temp_orchestrator, admin, sent_messages, edited_messages


def test_reserved_commands_coverage():
    """Ensure standard bot commands are in RESERVED_COMMANDS."""
    expected = {
        "start", "help", "menu", "schedule", "portfolio", "scan",
        "cash", "status", "analysis", "deepdive", "add", "rm"
    }
    for cmd in expected:
        assert cmd in RESERVED_COMMANDS


def test_ticker_shorthand_execution(telegram_test_setup):
    """Test that sending /NVDA, /aapl, and $TSLA triggers single ticker deep dive and auto-archives."""
    bot, orch, admin, sent, edited = telegram_test_setup

    mock_analysis = "🔬 <b>STOCK ANALYSIS: NVDA</b>\n<b>Verdict:</b> 🟢 <b>BUY (ACCUMULATE)</b>"

    with patch("analytics.market_data.fetch_live_quote", return_value={"name": "NVIDIA Corporation", "current_price": 130.0, "sector": "Technology"}), \
         patch.object(orch.analysis_agent, "analyze_single_ticker", return_value=mock_analysis) as mock_agent_call:

        # Test uppercase /NVDA
        bot._handle_incoming_message("/NVDA", "test_chat", "Investor")

        assert mock_agent_call.called
        assert mock_agent_call.call_args[1]["ticker"] == "NVDA"
        assert any("BUY" in m["text"] for m in edited)

        # Verify auto-archived in state_store
        deepdives = orch.state_store.get_user_deepdives(admin["id"], ticker="NVDA")
        assert len(deepdives) >= 1
        assert deepdives[0]["ticker"] == "NVDA"
        assert deepdives[0]["verdict"] == "BULLISH"
        assert deepdives[0]["current_price"] == 130.0


def test_ticker_shorthand_lowercase_and_cashtag(telegram_test_setup):
    """Test lowercase /aapl and cashtag $tsla."""
    bot, orch, admin, sent, edited = telegram_test_setup

    mock_analysis = "🔬 <b>STOCK ANALYSIS: AAPL</b>\n<b>Verdict:</b> 🟡 <b>HOLD</b>"

    with patch("analytics.market_data.fetch_live_quote", return_value={"name": "Apple Inc.", "current_price": 230.0, "sector": "Technology"}), \
         patch.object(orch.analysis_agent, "analyze_single_ticker", return_value=mock_analysis) as mock_agent_call:

        # Lowercase /aapl
        bot._handle_incoming_message("/aapl", "test_chat", "Investor")
        assert mock_agent_call.call_args[1]["ticker"] == "AAPL"

        # Cashtag $TSLA
        mock_agent_call.return_value = "🔬 <b>STOCK ANALYSIS: TSLA</b>\n<b>Verdict:</b> 🔴 <b>PASS</b>"
        bot._handle_incoming_message("$TSLA", "test_chat", "Investor")
        assert mock_agent_call.call_args[1]["ticker"] == "TSLA"


def test_analysis_command_aliases(telegram_test_setup):
    """Test /analysis MSFT and /deepdive MSFT."""
    bot, orch, admin, sent, edited = telegram_test_setup

    mock_analysis = "🔬 <b>STOCK ANALYSIS: MSFT</b>\n<b>Verdict:</b> 🟢 <b>BUY</b>"

    with patch("analytics.market_data.fetch_live_quote", return_value={"name": "Microsoft", "current_price": 420.0, "sector": "Technology"}), \
         patch.object(orch.analysis_agent, "analyze_single_ticker", return_value=mock_analysis) as mock_agent_call:

        bot._handle_incoming_message("/analysis MSFT", "test_chat", "Investor")
        assert mock_agent_call.call_args[1]["ticker"] == "MSFT"

        bot._handle_incoming_message("/deepdive MSFT", "test_chat", "Investor")
        assert mock_agent_call.call_args[1]["ticker"] == "MSFT"


def test_cash_and_status_commands(telegram_test_setup):
    """Test dedicated /cash and /status handlers."""
    bot, orch, admin, sent, edited = telegram_test_setup

    bot._handle_incoming_message("/cash", "test_chat", "Investor")
    assert any("DEPLOYABLE CASH" in m["text"] for m in sent)

    bot._handle_incoming_message("/status", "test_chat", "Investor")
    assert any("OPERATIONAL" in m["text"] for m in sent)


def test_unrecognized_slash_command(telegram_test_setup):
    """Test that invalid slash commands show the helpful guidance message."""
    bot, orch, admin, sent, edited = telegram_test_setup

    bot._handle_incoming_message("/notarealcommand123", "test_chat", "Investor")
    assert any("Unrecognized Command" in m["text"] for m in sent)
    assert any("/&lt;ticker&gt;" in m["text"] or "/<ticker>" in m["text"] for m in sent)


def test_regular_chat_routing(telegram_test_setup):
    """Test that non-slash messages are routed to Gemini portfolio chat."""
    bot, orch, admin, sent, edited = telegram_test_setup

    with patch.object(orch.notification_agent, "query_llm_text", return_value="Here is your portfolio analysis answer.") as mock_llm:
        bot._handle_incoming_message("Should I rebalance into defensive energy?", "test_chat", "Investor")
        assert mock_llm.called
        assert any("AI Portfolio Analyst" in m["text"] for m in sent)


def test_webhook_process_update(telegram_test_setup):
    """Test process_webhook_update with /NVDA payload."""
    bot, orch, admin, sent, edited = telegram_test_setup

    update_payload = {
        "update_id": 10001,
        "message": {
            "message_id": 42,
            "from": {"id": 12345, "first_name": "Frank", "username": "frank"},
            "chat": {"id": 12345, "type": "private"},
            "text": "/NVDA"
        }
    }
    with patch.object(bot, "_handle_incoming_message") as mock_handler:
        res = bot.process_webhook_update(update_payload)
        assert res["status"] == "ok"
        assert res["update_id"] == 10001
        # Allow thread to call handler
        import time
        time.sleep(0.05)
        mock_handler.assert_called_once_with("/NVDA", "12345", "Frank", "frank")

        # Test duplicate update idempotency
        res_dup = bot.process_webhook_update(update_payload)
        assert res_dup["status"] == "already_processed"
        assert res_dup["update_id"] == 10001
        assert mock_handler.call_count == 1


def test_greetings_routing(telegram_test_setup):
    """Test that /hello, /hi, /hey route to welcome/help menu instead of ticker analysis."""
    bot, orch, admin, sent, edited = telegram_test_setup

    with patch.object(orch.analysis_agent, "analyze_single_ticker") as mock_analysis:
        bot._handle_incoming_message("/hello", "test_chat", "Investor")
        assert not mock_analysis.called
        assert any("Welcome to Financial Sentinel" in m["text"] for m in sent)

        bot._handle_incoming_message("/hi", "test_chat", "Investor")
        assert not mock_analysis.called


def test_unrecognized_ticker_aborts_without_llm(telegram_test_setup):
    """Test that non-existent tickers or typos like /foobar abort early without burning LLM tokens."""
    bot, orch, admin, sent, edited = telegram_test_setup

    with patch("analytics.market_data.fetch_live_quote", return_value={"name": "FOOBAR", "current_price": 0.0}), \
         patch.object(orch.analysis_agent, "analyze_single_ticker") as mock_analysis:

        bot._handle_incoming_message("/foobar", "test_chat", "Investor")
        assert not mock_analysis.called
        assert any("Ticker 'FOOBAR' Not Recognized" in m["text"] for m in sent)
        assert any("Unable to verify live trade data" in m["text"] for m in sent)


def test_setup_webhook_secret_token(telegram_test_setup, monkeypatch):
    """Ensure setup_webhook passes secret_token to Telegram setWebhook endpoint."""
    bot, _, _, _, _ = telegram_test_setup
    bot.bot_token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"

    posted_payload = {}

    def mock_post(url, json=None, timeout=None):
        nonlocal posted_payload
        posted_payload = json
        class MockResp:
            status_code = 200
            text = '{"ok": true}'
            def json(self):
                return {"ok": True}
        return MockResp()

    from config import config
    # 1. With configured telegram_webhook_secret
    monkeypatch.setattr(config, "telegram_webhook_secret", "custom_secret_abc")
    with patch("httpx.post", side_effect=mock_post):
        res = bot.setup_webhook("https://example.run.app")
        assert res is True
        assert posted_payload.get("url") == "https://example.run.app/api/telegram/webhook"
        assert posted_payload.get("secret_token") == "custom_secret_abc"

    # 2. With fallback derived from APP_SECRET_KEY
    monkeypatch.setattr(config, "telegram_webhook_secret", "")
    monkeypatch.setenv("APP_SECRET_KEY", "test_app_secret_123")
    with patch("httpx.post", side_effect=mock_post):
        res = bot.setup_webhook("https://example.run.app")
        assert res is True
        assert posted_payload.get("url") == "https://example.run.app/api/telegram/webhook"
        assert len(posted_payload.get("secret_token")) == 64

