"""Listing source protocol and factory."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from rentbuster.models import Listing


@runtime_checkable
class ListingSource(Protocol):
    name: str

    async def fetch_listings(self) -> list[Listing]: ...

    async def close(self) -> None: ...


def build_sources(settings, profile) -> list[ListingSource]:
    """Instantiate enabled listing sources from settings + profile."""
    sources: list[ListingSource] = []

    search = profile.search

    if settings.pararius_enabled:
        from rentbuster.sources.pararius import ParariusSource

        sources.append(
            ParariusSource(
                city=search.city,
                max_rent=search.max_rent,
                min_size=search.min_size,
                max_pages=search.pararius_max_pages,
                headless=settings.playwright_headless,
                detail_delay=settings.detail_fetch_delay,
                fetch_details=settings.fetch_details,
            )
        )

    if settings.rentbuster_nl_enabled:
        from rentbuster.sources.rentbuster_nl import RentbusterNLSource

        sources.append(
            RentbusterNLSource(
                city=search.city,
                max_pages=search.rentbuster_nl_max_pages,
                user_agent=settings.user_agent,
            )
        )

    return sources
