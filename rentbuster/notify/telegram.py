"""Telegram notifier with password-gated subscription and interactive commands."""

from __future__ import annotations

import json
import logging

import requests

from rentbuster.db import Database
from rentbuster.models import Listing

log = logging.getLogger(__name__)

_HELP_TEXT = (
    "📋 <b>RentBuster commands</b>\n\n"
    "/start &lt;password&gt; — subscribe to alerts\n"
    "/stop — unsubscribe\n"
    "/list [N] — top N bustable listings (default 5)\n"
    "/detail &lt;id&gt; — full details for a listing\n"
    "/drop &lt;id&gt; — hide listing from future alerts\n"
    "/status — last run time and listing counts\n"
    "/filters — current search profile settings\n"
    "/help — show this message"
)


class TelegramNotifier:
    name = "telegram"

    def __init__(self, bot_token: str, password: str, db: Database, profile=None) -> None:
        self.bot_token = bot_token
        self.password = password
        self.db = db
        self.profile = profile
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

    def _require_subscribed(self, chat_id: int) -> bool:
        return chat_id in self.db.get_telegram_subscribers()

    # ── Command handlers ──────────────────────────────────────────────────────

    def _cmd_start(self, chat_id: int, args: str, username: str, first_name: str) -> None:
        given = args.strip()
        if given != self.password:
            self._send(chat_id, "🔒 Password required.\n\nSend: <code>/start yourpassword</code>")
            return
        is_new = self.db.add_telegram_subscriber(chat_id, username, first_name)
        if is_new:
            self._send(
                chat_id,
                "✅ <b>Subscribed!</b>\n\n"
                "You'll receive alerts for bustable Amsterdam apartments.\n\n"
                "Send /help to see available commands.",
            )
            log.info("new telegram subscriber: %s (%s)", first_name, chat_id)
        else:
            self._send(chat_id, "You're already subscribed! 👍\n\nSend /help for commands.")

    def _cmd_stop(self, chat_id: int, first_name: str) -> None:
        removed = self.db.remove_telegram_subscriber(chat_id)
        if removed:
            self._send(chat_id, "👋 Unsubscribed. Send <code>/start yourpassword</code> to resubscribe.")
            log.info("telegram unsubscribe: %s (%s)", first_name, chat_id)
        else:
            self._send(chat_id, "You weren't subscribed.")

    def _cmd_help(self, chat_id: int) -> None:
        self._send(chat_id, _HELP_TEXT)

    def _cmd_list(self, chat_id: int, args: str) -> None:
        if not self._require_subscribed(chat_id):
            self._send(chat_id, "🔒 Subscribe first with <code>/start yourpassword</code>")
            return
        try:
            n = int(args.strip()) if args.strip() else 5
            n = max(1, min(n, 20))
        except ValueError:
            n = 5
        listings = self.db.get_top_listings(n)
        if not listings:
            self._send(chat_id, "No bustable listings found yet.")
            return
        lines = [f"🏆 <b>Top {len(listings)} bustable listings</b>\n"]
        for r in listings:
            addr = f"{r.get('street', '')} {r.get('house_number', '')}"
            addition = r.get("house_number_addition") or ""
            if addition:
                addr += f"-{addition}"
            city = (r.get("city") or "").title()
            asking = r.get("asking_rent") or 0
            max_rent = r.get("wws_max_rent") or 0
            pts = r.get("wws_points") or 0
            savings = r.get("wws_savings") or 0
            conf = r.get("wws_confidence") or "?"
            db_id = r.get("id") or "?"
            url = r.get("url") or ""
            lines.append(
                f"<b>#{db_id}</b> {addr}, {city}\n"
                f"€{asking}/mo → €{max_rent:.0f}/mo max ({pts:.0f} pts)\n"
                f"💸 €{savings:.0f}/mo saved | 📊 {conf}\n"
                f'🔗 <a href="{url}">View</a> | /detail {db_id} | /drop {db_id}\n'
            )
        self._send(chat_id, "\n".join(lines))

    def _cmd_detail(self, chat_id: int, args: str) -> None:
        if not self._require_subscribed(chat_id):
            self._send(chat_id, "🔒 Subscribe first with <code>/start yourpassword</code>")
            return
        try:
            listing_id = int(args.strip())
        except (ValueError, AttributeError):
            self._send(chat_id, "Usage: /detail &lt;id&gt;")
            return
        r = self.db.get_listing_by_id(listing_id)
        if not r:
            self._send(chat_id, f"Listing #{listing_id} not found.")
            return
        addr = f"{r.get('street', '')} {r.get('house_number', '')}"
        if r.get("house_number_addition"):
            addr += f"-{r['house_number_addition']}"
        city = (r.get("city") or "").title()
        asking = r.get("asking_rent") or 0
        max_rent = r.get("wws_max_rent") or 0
        pts = r.get("wws_points") or 0
        savings = r.get("wws_savings") or 0
        conf = r.get("wws_confidence") or "?"
        energy = r.get("energy_label") or "?"
        woz = r.get("woz_value")
        woz_verified = r.get("woz_verified")

        flags = r.get("wws_flags") or "[]"
        if isinstance(flags, str):
            flags = json.loads(flags)

        breakdown = r.get("wws_breakdown") or "{}"
        if isinstance(breakdown, str):
            breakdown = json.loads(breakdown)

        woz_str = _format_woz(woz, woz_verified, flags)

        bd_lines = []
        for k, v in breakdown.items():
            if k != "total" and isinstance(v, (int, float)):
                bd_lines.append(f"  {k}: {v:.1f}")

        suitability = []
        if r.get("suitable_for_students") is not None:
            suitability.append(f"students: {'✅' if r['suitable_for_students'] else '❌'}")
        if r.get("suitable_for_sharing") is not None:
            suitability.append(f"sharing: {'✅' if r['suitable_for_sharing'] else '❌'}")
        if r.get("guarantor_accepted") is not None:
            suitability.append(f"guarantor: {'✅' if r['guarantor_accepted'] else '❌'}")

        lines = [
            f"🏠 <b>#{listing_id}: {addr}, {city}</b>",
            f"💰 €{asking}/mo asking → €{max_rent:.0f}/mo max ({pts:.0f} pts)",
            f"💸 <b>Savings: €{savings:.0f}/mo</b> | 📊 {conf}",
            f"⚡ Energy: {energy} | {woz_str}",
        ]
        if suitability:
            lines.append("👤 " + " | ".join(suitability))
        if bd_lines:
            lines.append("\n<b>WWS breakdown:</b>\n" + "\n".join(bd_lines))
        if r.get("available_from"):
            lines.append(f"📅 Available: {r['available_from']}")
        if r.get("agency_name"):
            lines.append(f"🏢 {r['agency_name']}")
        desc = (r.get("description") or "")[:400]
        if desc:
            lines.append(f"\n{desc}…" if len(r.get("description", "")) > 400 else f"\n{desc}")
        lines.append(f'\n🔗 <a href="{r.get("url", "")}">View listing</a>')
        self._send(chat_id, "\n".join(lines))

    def _cmd_drop(self, chat_id: int, args: str) -> None:
        if not self._require_subscribed(chat_id):
            self._send(chat_id, "🔒 Subscribe first with <code>/start yourpassword</code>")
            return
        try:
            listing_id = int(args.strip())
        except (ValueError, AttributeError):
            self._send(chat_id, "Usage: /drop &lt;id&gt;")
            return
        r = self.db.get_listing_by_id(listing_id)
        if not r:
            self._send(chat_id, f"Listing #{listing_id} not found.")
            return
        self.db.drop_listing(chat_id, r["source"], r["source_id"])
        addr = f"{r.get('street', '')} {r.get('house_number', '')}"
        self._send(chat_id, f"✅ {addr} (#{listing_id}) hidden from future alerts.")
        log.info("telegram: chat %s dropped listing #%s", chat_id, listing_id)

    def _cmd_status(self, chat_id: int) -> None:
        if not self._require_subscribed(chat_id):
            self._send(chat_id, "🔒 Subscribe first with <code>/start yourpassword</code>")
            return
        run = self.db.get_last_scrape_run()
        counts = self.db.get_listing_count()
        lines = ["📊 <b>RentBuster Status</b>\n"]
        if run:
            finished = run.get("finished_at")
            lines.append(f"Last run: {finished or run.get('started_at') or 'unknown'}")
            lines.append(
                f"Found: {run.get('total_found', 0)} total, {run.get('new_found', 0)} new, {run.get('bustable_found', 0)} bustable"
            )
            if run.get("errors"):
                lines.append(f"⚠️ Errors: {run['errors']}")
        else:
            lines.append("No scrape runs recorded yet.")
        lines.append(
            f"\nDB: {counts['total']} total listings | {counts['bustable']} bustable | {counts['active']} active"
        )
        self._send(chat_id, "\n".join(lines))

    def _cmd_filters(self, chat_id: int) -> None:
        if not self._require_subscribed(chat_id):
            self._send(chat_id, "🔒 Subscribe first with <code>/start yourpassword</code>")
            return
        if not self.profile:
            self._send(chat_id, "No profile loaded.")
            return
        s = self.profile.search
        w = self.profile.wws
        lines = [
            f"⚙️ <b>Profile: {self.profile.name}</b>\n",
            f"City: {s.city}",
            f"Max rent: €{s.max_rent}/mo",
            f"Max rooms: {s.max_rooms or 'any'}",
            f"Property types: {', '.join(s.property_types) or 'any'}",
            f"Students allowed: {'required' if s.must_allow_students else 'not filtered'}",
            f"Sharing allowed: {'required' if s.must_allow_sharing else 'not filtered'}",
            f"Guarantor accepted: {'required' if s.must_accept_guarantor else 'not filtered'}",
            f"\nWWS: min savings €{w.min_savings}/mo | bustable only: {w.bustable_only}",
        ]
        self._send(chat_id, "\n".join(lines))

    def _cmd_top(self, chat_id: int, args: str) -> None:
        # Legacy /top command — delegate to /list
        self._cmd_list(chat_id, args)

    # ── Dispatch ──────────────────────────────────────────────────────────────

    _DISPATCH: dict[str, str] = {
        "/start": "_cmd_start",
        "/stop": "_cmd_stop",
        "/help": "_cmd_help",
        "/list": "_cmd_list",
        "/detail": "_cmd_detail",
        "/drop": "_cmd_drop",
        "/status": "_cmd_status",
        "/filters": "_cmd_filters",
        "/top": "_cmd_top",
    }

    def process_commands(self) -> None:
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

            # Split command and args (handle /command@botname form)
            cmd_part, _, rest = text.partition(" ")
            cmd_base = cmd_part.split("@")[0].lower()
            args = rest.strip()

            handler_name = self._DISPATCH.get(cmd_base)
            if not handler_name:
                continue

            try:
                if cmd_base == "/start":
                    self._cmd_start(chat_id, args, username, first_name)
                elif cmd_base == "/stop":
                    self._cmd_stop(chat_id, first_name)
                elif cmd_base == "/help":
                    self._cmd_help(chat_id)
                elif cmd_base in ("/list", "/top"):
                    self._cmd_list(chat_id, args)
                elif cmd_base == "/detail":
                    self._cmd_detail(chat_id, args)
                elif cmd_base == "/drop":
                    self._cmd_drop(chat_id, args)
                elif cmd_base == "/status":
                    self._cmd_status(chat_id)
                elif cmd_base == "/filters":
                    self._cmd_filters(chat_id)
            except Exception as exc:
                log.warning("telegram: handler %s failed: %s", cmd_base, exc)

    def send_text(self, text: str) -> None:
        for chat_id in self.db.get_telegram_subscribers():
            self._send(chat_id, text)

    def send_listings(self, listings: list[Listing]) -> None:
        subscribers = self.db.get_telegram_subscribers()
        if not subscribers:
            return

        for listing in listings:
            text = self._format_listing(listing)
            for chat_id in subscribers:
                # Filter dropped listings per subscriber
                dropped = self.db.get_dropped_keys(chat_id)
                if (listing.source.value, listing.source_id) in dropped:
                    continue
                self._send(chat_id, text)
            log.info(
                "telegram: sent %s/%s to subscribers",
                listing.source.value,
                listing.source_id,
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

        flags = listing.wws_flags or []
        woz_str = _format_woz(listing.woz_value, listing.woz_verified, flags)
        estimated_footer = ""
        if listing.woz_value and not listing.woz_verified:
            estimated_footer = "\n⚠️ WOZ is estimated — actual points may differ"
        elif "woz_sibling" in flags:
            estimated_footer = "\nℹ️ WOZ from neighboring unit"

        rb_line = ""
        if listing.rb_points is not None:
            rb_line = f"🔍 rent-buster.nl: {listing.rb_points:.0f} pts"
            if listing.rb_points >= 187:
                rb_line += " (free market)"
            elif listing.rb_estimated_max_rent is not None:
                rb_line += f", max €{listing.rb_estimated_max_rent:.0f}/mo"
            rb_line += "\n"

        return (
            f"🏠 <b>Bustable listing found!</b>\n\n"
            f"📍 <b>{address}, {city}</b>\n"
            f"💰 Asking: €{listing.asking_rent}/mo\n"
            f"⚖️ Max legal ({points:.0f} pts): €{max_rent:.0f}/mo\n"
            f"💸 <b>Savings: €{savings:.0f}/mo (€{savings * 12:.0f}/yr)</b>\n\n"
            f"📐 {size_info}  |  ⚡ Label {energy}  |  {woz_str}\n"
            f"📊 Confidence: {conf}{estimated_footer}\n"
            f"{rb_line}\n"
            f"⚠️ Verify with Huurcommissie before disputing\n"
            f"📡 Source: {listing.source.value}\n"
            f'🔗 <a href="{listing.url}">View listing</a>'
        )


def _format_woz(woz_value: int | None, woz_verified: bool | None, flags: list) -> str:
    if not woz_value:
        return "⚠️ WOZ ESTIMATED"
    if "woz_sibling" in flags:
        return f"WOZ €{woz_value:,} (neighboring unit)"
    if not woz_verified:
        return f"<b>⚠️ WOZ €{woz_value:,} (ESTIMATED)</b>"
    return f"WOZ €{woz_value:,}"
