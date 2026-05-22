"""Telegram notifier with password-gated /start subscription flow."""

from __future__ import annotations

import logging

import requests

from rentbuster.db import Database
from rentbuster.models import Listing

log = logging.getLogger(__name__)


class TelegramNotifier:
    name = "telegram"

    def __init__(self, bot_token: str, password: str, db: Database) -> None:
        self.bot_token = bot_token
        self.password = password
        self.db = db
        self._last_update_id = 0

    def _send(self, chat_id: int, text: str) -> bool:
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                },
                timeout=10,
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def process_commands(self) -> None:
        """Poll getUpdates and handle /start <password> and /stop commands."""
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{self.bot_token}/getUpdates",
                params={"offset": self._last_update_id + 1, "timeout": 0},
                timeout=10,
            )
        except requests.RequestException as exc:
            log.warning("telegram getUpdates failed: %s", exc)
            return

        data = resp.json() if resp.ok else {}
        if not data.get("ok") or not data.get("result"):
            return

        for update in data["result"]:
            self._last_update_id = update["update_id"]
            message = update.get("message") or {}
            text = (message.get("text") or "").strip()
            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            if not chat_id or not text:
                continue

            first_name = chat.get("first_name") or ""
            username = (message.get("from") or {}).get("username") or ""

            if text.startswith("/start"):
                parts = text.split(maxsplit=1)
                given = parts[1] if len(parts) > 1 else ""
                if given != self.password:
                    self._send(chat_id, "🔒 Password required.\n\nSend: <code>/start yourpassword</code>")
                    continue
                is_new = self.db.add_telegram_subscriber(chat_id, username, first_name)
                if is_new:
                    self._send(
                        chat_id,
                        "✅ <b>Subscribed!</b>\n\n"
                        "You'll receive alerts for bustable Amsterdam apartments.\n\n"
                        "Send /stop to unsubscribe.",
                    )
                    log.info("new telegram subscriber: %s (%s)", first_name, chat_id)
                else:
                    self._send(chat_id, "You're already subscribed! 👍")

            elif text == "/stop":
                removed = self.db.remove_telegram_subscriber(chat_id)
                if removed:
                    self._send(chat_id, "👋 Unsubscribed. Send <code>/start yourpassword</code> to resubscribe.")
                    log.info("telegram unsubscribe: %s (%s)", first_name, chat_id)
                else:
                    self._send(chat_id, "You weren't subscribed.")

    def send_listings(self, listings: list[Listing]) -> None:
        subscribers = self.db.get_telegram_subscribers()
        if not subscribers:
            return

        for listing in listings:
            text = self._format_listing(listing)
            sent = sum(1 for chat_id in subscribers if self._send(chat_id, text))
            log.info(
                "telegram: %s/%s → %d/%d subscribers",
                listing.source.value,
                listing.source_id,
                sent,
                len(subscribers),
            )

    def _format_listing(self, listing: Listing) -> str:
        address = f"{listing.street} {listing.house_number}"
        if listing.house_number_addition:
            address += f"-{listing.house_number_addition}"
        city = listing.city.title() if listing.city else "Unknown"

        savings = listing.wws_savings or 0
        max_rent = listing.wws_max_rent or 0
        points = listing.wws_points or 0
        conf = listing.wws_confidence.value if listing.wws_confidence else "unknown"
        energy = listing.energy_label.value if listing.energy_label else "unknown"

        size_parts = []
        if listing.surface_area_m2:
            size_parts.append(f"{listing.surface_area_m2}m²")
        if listing.num_rooms:
            size_parts.append(f"{listing.num_rooms} rooms")
        size_info = " • ".join(size_parts) if size_parts else "unknown"

        woz_info = f"€{listing.woz_value:,}" if listing.woz_value else "estimated"
        if listing.woz_value and not listing.woz_verified:
            woz_info += " (est.)"

        return (
            f"🏠 <b>Bustable listing found!</b>\n\n"
            f"📍 <b>{address}, {city}</b>\n"
            f"💰 Asking: €{listing.asking_rent}/mo\n"
            f"⚖️ Max legal ({points:.0f} pts): €{max_rent:.0f}/mo\n"
            f"💸 <b>Savings: €{savings:.0f}/mo (€{savings * 12:.0f}/yr)</b>\n\n"
            f"📐 {size_info}  |  ⚡ Label {energy}  |  WOZ {woz_info}\n"
            f"📊 Confidence: {conf}\n\n"
            f"⚠️ Verify with Huurcommissie before disputing\n"
            f'🔗 <a href="{listing.url}">View listing</a>'
        )
