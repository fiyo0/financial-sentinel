"""
System configuration and environment management.
"""
import os
from typing import List, Dict
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()


class SystemConfig(BaseModel):
    # App Metadata
    app_name: str = "Financial Sentinel & Alpha Multi-Agent System"
    version: str = "2.4.0"

    # LLM Settings
    gemini_api_key: str = Field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    model_name: str = Field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))

    use_llm: bool = Field(default_factory=lambda: bool(os.getenv("GEMINI_API_KEY", "")))

    # Storage
    db_path: str = Field(default_factory=lambda: os.getenv("STATE_DB_PATH", "storage/state.db"))

    # Noise & Risk Thresholds
    p0_impact_threshold_pct: float = 3.5  # Expected price movement >= 3.5% triggers P0 instant alert
    critic_min_confidence_pct: float = 60.0  # Critic threshold to approve
    max_sector_concentration_pct: float = 35.0  # Alert if a single sector > 35%

    # Token Budget & Cost Constraints
    daily_token_limit: int = Field(default_factory=lambda: int(os.getenv("DAILY_TOKEN_LIMIT", "500000")))
    max_tokens_per_cycle: int = Field(default_factory=lambda: int(os.getenv("MAX_TOKENS_PER_CYCLE", "50000")))
    enable_token_governance: bool = True

    # Notification Channels & Security
    telegram_bot_token: str = Field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = Field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))
    telegram_allowed_usernames: List[str] = Field(
        default_factory=lambda: [u.strip().replace("@", "") for u in os.getenv("TELEGRAM_ALLOWED_USERNAMES", "").split(",") if u.strip()]
    )
    telegram_allowed_chat_ids: List[str] = Field(
        default_factory=lambda: [c.strip() for c in os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",") if c.strip()]
    )
    discord_webhook_url: str = Field(default_factory=lambda: os.getenv("DISCORD_WEBHOOK_URL", ""))
    slack_webhook_url: str = Field(default_factory=lambda: os.getenv("SLACK_WEBHOOK_URL", ""))
    generic_webhook_url: str = Field(default_factory=lambda: os.getenv("GENERIC_WEBHOOK_URL", ""))
    email_smtp_host: str = Field(default_factory=lambda: os.getenv("SMTP_HOST", ""))
    email_recipient: str = Field(default_factory=lambda: os.getenv("EMAIL_RECIPIENT", ""))

    # Default RSS Feeds
    rss_feeds: List[Dict[str, str]] = Field(
        default_factory=lambda: [
            {"name": "Yahoo Finance Top", "url": "https://finance.yahoo.com/news/rssindex", "category": "BREAKING"},
            {"name": "CNBC Finance", "url": "https://search.cnbc.com/rs/search/combinedlist/view.xml?partnerId=wrss01&id=10000664", "category": "BREAKING"},
            {"name": "MarketWatch Top Stories", "url": "http://feeds.marketwatch.com/marketwatch/topstories/", "category": "MACRO"},
            {"name": "SEC Press Releases", "url": "https://www.sec.gov/news/pressreleases.rss", "category": "SEC_FILING"},
            {"name": "SEC Regulatory Statements", "url": "https://www.sec.gov/news/statements.rss", "category": "SEC_FILING"},
            {"name": "Federal Reserve Press", "url": "https://www.federalreserve.gov/feeds/press_all.xml", "category": "MACRO"},
        ]
    )

    # Server & Auth Settings
    web_host: str = "0.0.0.0"
    web_port: int = 8000
    dashboard_auth_enabled: bool = Field(default_factory=lambda: os.getenv("DASHBOARD_AUTH_ENABLED", "true").lower() in ("true", "1", "yes"))
    dashboard_password: str = Field(default_factory=lambda: os.getenv("DASHBOARD_PASSWORD", ""))
    telegram_webhook_secret: str = Field(default_factory=lambda: os.getenv("TELEGRAM_WEBHOOK_SECRET", ""))
    cron_secret: str = Field(default_factory=lambda: os.getenv("CRON_SECRET", ""))

    @property
    def resolved_telegram_webhook_secret(self) -> str:
        if self.telegram_webhook_secret:
            return self.telegram_webhook_secret
        app_sec = os.getenv("APP_SECRET_KEY", "")
        if app_sec:
            import hashlib
            return hashlib.sha256(f"tg_webhook_{app_sec}".encode("utf-8")).hexdigest()
        return ""


config = SystemConfig()
