"""
Pytest configuration and test environment setup.
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ["APP_SECRET_KEY"] = "test-secret-key-32-chars-long-for-pytest-execution!"
os.environ["DASHBOARD_PASSWORD"] = "test-ci-dashboard-passcode-32b"
os.environ["GEMINI_API_KEY"] = "AIzaSyTestMockGeminiApiKeyForTestingOnly"
os.environ["TELEGRAM_BOT_TOKEN"] = "123456789:MockTelegramBotTokenForTesting"
os.environ["DASHBOARD_AUTH_ENABLED"] = "true"
os.environ["ALLOW_INSECURE_LOCAL"] = "1"
os.environ["TELEGRAM_WEBHOOK_SECRET"] = "test-telegram-secret-token"
os.environ["CRON_SECRET"] = "test-cron-secret-token"
os.environ["TELEGRAM_ALLOWED_CHAT_IDS"] = "test_chat"
os.environ["TELEGRAM_ALLOWED_USERNAMES"] = "investor"

from config import config
config.dashboard_password = os.environ["DASHBOARD_PASSWORD"]
config.gemini_api_key = os.environ["GEMINI_API_KEY"]
config.telegram_bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
config.telegram_webhook_secret = os.environ["TELEGRAM_WEBHOOK_SECRET"]
config.cron_secret = os.environ["CRON_SECRET"]
config.telegram_allowed_chat_ids = ["test_chat"]
config.telegram_allowed_usernames = ["investor"]


