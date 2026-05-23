"""Discord notifier — rich embeds for bustable listing alerts."""

from __future__ import annotations

import logging
import time

from discord_webhook import DiscordEmbed, DiscordWebhook

from rentbuster.models import ConfidenceLevel, Listing

log = logging.getLogger(__name__)

BATCH_SIZE = 9  # 10-embed Discord limit minus 1 for summary header


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _embed_color(listing: Listing) -> int:
    savings = listing.wws_savings or 0
    if savings > 200:
        return 0x44FF44  # green — big win
    if savings > 50:
        return 0xFFD700  # gold — moderate savings
    return 0x0099FF  # blue — minimal savings


def _confidence_emoji(listing: Listing) -> str:
    mapping = {
        ConfidenceLevel.HIGH: "🟢",
        ConfidenceLevel.MEDIUM: "🟡",
        ConfidenceLevel.LOW: "🟠",
        ConfidenceLevel.VERY_LOW: "🔴",
    }
    return mapping.get(listing.wws_confidence, "⚪")


def format_listing(listing: Listing) -> DiscordEmbed:
    address = f"{listing.street} {listing.house_number}"
    if listing.house_number_addition:
        address += f"-{listing.house_number_addition}"
    city = listing.city.title() if listing.city else "Unknown"

    savings = listing.wws_savings or 0
    max_rent = listing.wws_max_rent or 0
    points = listing.wws_points or 0

    embed = DiscordEmbed(
        title=_truncate(f"Bustable: {address}, {city}", 256),
        description=_truncate(
            f"**€{listing.asking_rent}/mo** asking vs **€{max_rent:.0f}/mo** legal maximum\n"
            f"Save **€{savings:.0f}/mo** (€{savings * 12:.0f}/yr) by disputing with Huurcommissie",
            500,
        ),
        url=listing.url,
        color=_embed_color(listing),
    )

    embed.add_embed_field(name="💰 Asking Rent", value=f"€{listing.asking_rent}/mo", inline=True)
    embed.add_embed_field(
        name=f"⚖️ Max Legal ({points:.0f} pts)",
        value=f"€{max_rent:.0f}/mo",
        inline=True,
    )
    embed.add_embed_field(name="💸 Monthly Savings", value=f"**€{savings:.0f}**", inline=True)

    size_info = f"{listing.surface_area_m2}m²" if listing.surface_area_m2 else "unknown"
    if listing.num_rooms:
        size_info += f" • {listing.num_rooms} rooms"
    embed.add_embed_field(name="📐 Size", value=size_info, inline=True)

    energy = listing.energy_label.value if listing.energy_label else "unknown"
    if listing.woz_value:
        flags = listing.wws_flags or []
        if "woz_sibling" in flags:
            woz_info = f"WOZ €{listing.woz_value:,} (neighboring unit)"
        elif not listing.woz_verified:
            woz_info = f"⚠️ WOZ €{listing.woz_value:,} (ESTIMATED)"
        else:
            woz_info = f"WOZ €{listing.woz_value:,}"
    else:
        woz_info = "⚠️ WOZ ESTIMATED"
    embed.add_embed_field(name="⚡ Energy / WOZ", value=f"{energy} • {woz_info}", inline=True)

    conf_emoji = _confidence_emoji(listing)
    embed.add_embed_field(
        name="📊 Confidence",
        value=f"{conf_emoji} {listing.wws_confidence.value if listing.wws_confidence else 'unknown'}",
        inline=True,
    )

    if listing.wws_flags:
        # Show the most important flags (not the trivial defaults)
        important_flags = [
            f for f in listing.wws_flags if "assumed" in f or "unknown" in f or "estimated" in f
        ]
        if important_flags:
            embed.add_embed_field(
                name="⚠️ Assumptions",
                value=_truncate("\n".join(f"• {f}" for f in important_flags[:4]), 1024),
                inline=False,
            )

    if listing.agency_name:
        embed.add_embed_field(name="🏢 Agency", value=listing.agency_name, inline=True)

    if listing.available_from:
        embed.add_embed_field(name="📅 Available", value=listing.available_from, inline=True)

    if listing.images:
        embed.set_thumbnail(url=listing.images[0])

    embed.set_footer(
        text=f"RentBuster | WWS Points: {points:.0f} (< 187 = regulated) | Source: {listing.source.value}",
    )
    embed.set_timestamp()
    return embed


def _summary_header(listings: list[Listing], total_batches: int) -> DiscordEmbed:
    n = len(listings)
    savings_vals = [ls.wws_savings for ls in listings if ls.wws_savings]
    min_s = min(savings_vals) if savings_vals else 0
    max_s = max(savings_vals) if savings_vals else 0

    header = DiscordEmbed(
        title=f"RentBuster Alert — {n} bustable listing{'s' if n != 1 else ''} found!",
        description=(
            f"Savings: **€{min_s:.0f}-€{max_s:.0f}/month** per listing\n"
            "Verify with the official Huurcommissie calculator before acting."
        ),
        color=0xFF6B35,
    )
    footer = "RentBuster • WWS points calculator"
    if total_batches > 1:
        footer += f" • Batch 1 of {total_batches}"
    header.set_footer(text=footer)
    header.set_timestamp()
    return header


class DiscordNotifier:
    name = "discord"

    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    def send_listings(self, listings: list[Listing]) -> None:
        if not listings:
            return

        sorted_listings = sorted(listings, key=lambda x: -(x.wws_savings or 0))
        total_batches = (len(sorted_listings) + BATCH_SIZE - 1) // BATCH_SIZE

        for batch_num in range(total_batches):
            start = batch_num * BATCH_SIZE
            batch = sorted_listings[start : start + BATCH_SIZE]
            webhook = DiscordWebhook(url=self.webhook_url)

            if batch_num == 0:
                webhook.add_embed(_summary_header(sorted_listings, total_batches))
            else:
                hdr = DiscordEmbed(
                    title=f"RentBuster — Batch {batch_num + 1} of {total_batches}",
                    color=0x0099FF,
                )
                webhook.add_embed(hdr)

            for listing in batch:
                webhook.add_embed(format_listing(listing))

            resp = webhook.execute()
            if resp.status_code in (200, 204):
                log.info("discord batch %d/%d sent", batch_num + 1, total_batches)
            else:
                try:
                    body = resp.text
                except Exception:
                    body = str(resp.content)
                log.warning(
                    "discord webhook failed: %s — body: %s — retrying individually",
                    resp.status_code,
                    body[:500],
                )
                # Retry: send each embed as its own message (guaranteed under limits)
                for listing in batch:
                    single = DiscordWebhook(url=self.webhook_url)
                    single.add_embed(format_listing(listing))
                    single_resp = single.execute()
                    if single_resp.status_code not in (200, 204):
                        log.warning("discord individual send failed: %s", single_resp.status_code)
                    time.sleep(1)

            if batch_num < total_batches - 1:
                time.sleep(3)
