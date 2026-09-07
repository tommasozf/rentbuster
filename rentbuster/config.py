"""Runtime settings loaded from environment / .env file."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Database ──
    database_url: str | None = Field(
        default=None,
        description="Postgres connection string. If unset, the scraper runs without persistence.",
    )

    # ── Sources ──
    # Which sources run and how the browser behaves. What to search for (city, rent ceiling,
    # number of pages, minimum savings) lives in the profile YAML, not here.
    pararius_enabled: bool = True
    rentbuster_nl_enabled: bool = True
    playwright_headless: bool = True
    fetch_details: bool = True
    detail_fetch_delay: float = 2.0

    # ── Notifications: Discord ──
    discord_webhook_url: str | None = None

    # ── Notifications: Telegram ──
    telegram_bot_token: str | None = None
    telegram_password: str | None = Field(
        default=None,
        description=(
            "Password users send with /start <password> to subscribe. Required if TELEGRAM_BOT_TOKEN is set."
        ),
    )

    # ── Notifications: Apprise ──
    apprise_urls: str | None = Field(
        default=None,
        description=(
            "Comma-separated Apprise URLs (e.g. 'ntfy://mytopic,slack://tokenA/tokenB/tokenC'). "
            "See https://github.com/caronc/apprise/wiki for syntax."
        ),
    )

    # ── LLM feature extraction ──
    llm_enabled: bool = False
    gemini_api_key: str | None = None
    llm_model: str = "gemini-2.5-flash-lite"

    # ── Scraper scheduling ──
    check_interval_min: int = 120
    check_interval_max: int = 300

    # ── Profile ──
    profile: str = Field(
        default="amsterdam",
        description="Profile name (looks up profiles/<name>.yaml) or absolute path to a YAML file.",
    )

    # ── User agent ──
    user_agent: str = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def load_settings() -> Settings:
    return Settings()
