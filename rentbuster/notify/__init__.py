"""Notification layer for bustable listing alerts."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from rentbuster.config import Settings
from rentbuster.db import Database
from rentbuster.models import Listing

if TYPE_CHECKING:
    from rentbuster.profile import Profile

log = logging.getLogger(__name__)


@runtime_checkable
class Notifier(Protocol):
    name: str

    def send_listings(self, listings: list[Listing]) -> None: ...


class NotifierBundle:
    """Fan-out to all active notifiers."""

    def __init__(self, notifiers: list[Notifier]) -> None:
        self.notifiers = notifiers

    @property
    def active(self) -> bool:
        return bool(self.notifiers)

    @property
    def active_names(self) -> list[str]:
        return [n.name for n in self.notifiers]

    def send_listings(self, listings: list[Listing]) -> None:
        if not listings:
            return
        for notifier in self.notifiers:
            try:
                notifier.send_listings(listings)
            except Exception as exc:
                log.error("%s notifier failed: %s", notifier.name, exc)

    def start_background(self) -> None:
        """Start whatever a notifier wants running between scrape cycles (Telegram polling)."""
        for notifier in self.notifiers:
            starter = getattr(notifier, "start_polling", None)
            if callable(starter):
                try:
                    starter()
                except Exception as exc:
                    log.error("%s could not start polling: %s", notifier.name, exc)

    def process_commands(self) -> None:
        for notifier in self.notifiers:
            handler = getattr(notifier, "process_commands", None)
            if callable(handler):
                try:
                    handler()
                except Exception as exc:
                    log.error("%s command processing failed: %s", notifier.name, exc)


def build_notifiers(
    settings: Settings,
    db: Database | None,
    profile: Profile | None = None,
) -> NotifierBundle:
    notifiers: list[Notifier] = []

    if settings.discord_webhook_url:
        from rentbuster.notify.discord import DiscordNotifier

        notifiers.append(DiscordNotifier(settings.discord_webhook_url))

    if settings.telegram_bot_token:
        if db is None:
            log.warning(
                "TELEGRAM_BOT_TOKEN is set but DATABASE_URL is not — "
                "telegram subscribers cannot be persisted. Disabling telegram."
            )
        elif not settings.telegram_password:
            log.warning(
                "TELEGRAM_BOT_TOKEN is set but TELEGRAM_PASSWORD is not. "
                "Refusing to run an open subscription bot. Disabling telegram."
            )
        else:
            from rentbuster.notify.telegram import TelegramNotifier

            notifiers.append(
                TelegramNotifier(
                    bot_token=settings.telegram_bot_token,
                    password=settings.telegram_password,
                    db=db,
                    profile=profile,
                )
            )

    if settings.apprise_urls:
        from rentbuster.notify.apprise import AppriseNotifier

        notifiers.append(AppriseNotifier(urls=settings.apprise_urls))

    return NotifierBundle(notifiers)
