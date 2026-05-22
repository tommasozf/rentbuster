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
    pararius_enabled: bool = True
    pararius_max_pages: int = 5
    rentbuster_nl_enabled: bool = True
    rentbuster_nl_max_pages: int = 10
    playwright_headless: bool = True
    fetch_details: bool = True
    detail_fetch_delay: float = 2.0

    # ── WWS filtering ──
    wws_bustable_only: bool = True
    wws_min_savings: int = 50  # minimum EUR/month savings to notify

    # ── Notifications: Discord ──
    discord_webhook_url: str | None = None

    # ── Notifications: Telegram ──
    telegram_bot_token: str | None = None
    telegram_password: str | None = Field(
        default=None,
        description=(
            "Password users send with /start <password> to subscribe. "
            "Required if TELEGRAM_BOT_TOKEN is set."
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

    # ── Scraper scheduling ──
    check_interval_min: int = 120  # longer than Kamernet — Playwright is heavier
    check_interval_max: int = 300

    # ── City / Profile ──
    city: str = "amsterdam"
    profile: str = Field(
        default="amsterdam",
        description="Profile name (looks up profiles/<name>.yaml) or absolute path to a YAML file.",
    )

    # ── User agent ──
    user_agent: str = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def load_settings() -> Settings:
    return Settings()
