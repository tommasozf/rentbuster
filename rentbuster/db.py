"""Postgres persistence layer (psycopg3)."""

from __future__ import annotations

import logging
from typing import Any

try:
    import psycopg
    from psycopg import Connection

    HAS_PSYCOPG = True
except ImportError:
    HAS_PSYCOPG = False
    Connection = Any  # type: ignore[assignment,misc]

from rentbuster.models import Listing

log = logging.getLogger(__name__)


UPSERT_SQL = """
INSERT INTO listings (
    source, source_id, url,
    street, house_number, house_number_addition,
    postal_code, city, neighborhood, address_key,
    asking_rent, surface_area_m2, num_rooms,
    energy_label, construction_year, property_type, interior,
    description, images, available_from, agency_name,
    wws_points, wws_max_rent, wws_savings, wws_is_bustable,
    wws_confidence, wws_breakdown, wws_flags,
    woz_value, woz_reference_date, woz_verified,
    rb_estimated_max_rent, rb_savings, rb_confidence,
    bust_score,
    first_seen_at, last_seen_at
) VALUES (
    %(source)s, %(source_id)s, %(url)s,
    %(street)s, %(house_number)s, %(house_number_addition)s,
    %(postal_code)s, %(city)s, %(neighborhood)s, %(address_key)s,
    %(asking_rent)s, %(surface_area_m2)s, %(num_rooms)s,
    %(energy_label)s, %(construction_year)s, %(property_type)s, %(interior)s,
    %(description)s, %(images)s, %(available_from)s, %(agency_name)s,
    %(wws_points)s, %(wws_max_rent)s, %(wws_savings)s, %(wws_is_bustable)s,
    %(wws_confidence)s, %(wws_breakdown)s, %(wws_flags)s,
    %(woz_value)s, %(woz_reference_date)s, %(woz_verified)s,
    %(rb_estimated_max_rent)s, %(rb_savings)s, %(rb_confidence)s,
    %(bust_score)s,
    NOW(), NOW()
)
ON CONFLICT (source, source_id) DO UPDATE SET
    asking_rent         = EXCLUDED.asking_rent,
    wws_points          = EXCLUDED.wws_points,
    wws_max_rent        = EXCLUDED.wws_max_rent,
    wws_savings         = EXCLUDED.wws_savings,
    wws_is_bustable     = EXCLUDED.wws_is_bustable,
    wws_confidence      = EXCLUDED.wws_confidence,
    wws_breakdown       = EXCLUDED.wws_breakdown,
    wws_flags           = EXCLUDED.wws_flags,
    woz_value           = EXCLUDED.woz_value,
    woz_verified        = EXCLUDED.woz_verified,
    rb_estimated_max_rent = EXCLUDED.rb_estimated_max_rent,
    rb_savings          = EXCLUDED.rb_savings,
    bust_score          = EXCLUDED.bust_score,
    last_seen_at        = NOW(),
    disappeared_at      = NULL,
    updated_at          = NOW()
"""


class Database:
    """Thin wrapper around a psycopg3 connection."""

    def __init__(self, url: str | None) -> None:
        if not HAS_PSYCOPG:
            raise RuntimeError("psycopg (v3) is not installed. Run `pip install 'psycopg[binary]'`.")
        if not url:
            raise ValueError("DATABASE_URL is required to use Database.")
        self.url = url
        self._conn: Connection | None = None
        self._connect()

    def _connect(self) -> None:
        try:
            self._conn = psycopg.connect(self.url, autocommit=True, connect_timeout=10)
            log.info("connected to postgres")
        except Exception as exc:
            log.error("db connect failed: %s", exc)
            self._conn = None

    def _cursor(self):
        if self._conn is None or self._conn.closed:
            self._connect()
        if self._conn is None:
            raise RuntimeError("no database connection available")
        return self._conn.cursor()

    # ── Listings ──

    def load_seen_source_ids(self) -> set[tuple[str, str]]:
        """Return a set of (source, source_id) tuples for all known listings."""
        try:
            with self._cursor() as cur:
                cur.execute("SELECT source, source_id FROM listings")
                return {(row[0], row[1]) for row in cur.fetchall()}
        except Exception as exc:
            log.warning("load seen source_ids failed: %s", exc)
            return set()

    def upsert_listing(self, listing: Listing) -> None:
        try:
            with self._cursor() as cur:
                cur.execute(UPSERT_SQL, listing.to_db_params())
        except Exception as exc:
            log.warning("upsert failed for %s/%s: %s", listing.source, listing.source_id, exc)

    def touch_listings(self, source: str, source_ids: list[str]) -> None:
        if not source_ids:
            return
        try:
            with self._cursor() as cur:
                cur.execute(
                    "UPDATE listings SET last_seen_at = NOW(), disappeared_at = NULL "
                    "WHERE source = %s AND source_id = ANY(%s)",
                    (source, source_ids),
                )
        except Exception as exc:
            log.warning("touch failed: %s", exc)

    def mark_disappeared(self, minutes: int = 60) -> int:
        try:
            with self._cursor() as cur:
                cur.execute(
                    "UPDATE listings SET disappeared_at = NOW() "
                    "WHERE disappeared_at IS NULL "
                    "  AND last_seen_at < NOW() - make_interval(mins => %s)",
                    (int(minutes),),
                )
                return cur.rowcount or 0
        except Exception as exc:
            log.warning("mark disappeared failed: %s", exc)
            return 0

    # ── WOZ cache ──

    def get_cached_woz(self, postal_code: str, house_number: str, addition: str) -> dict | None:
        try:
            with self._cursor() as cur:
                cur.execute(
                    "SELECT woz_value, reference_date, verified, source "
                    "FROM woz_cache "
                    "WHERE postal_code = %s AND house_number = %s AND house_number_addition = %s",
                    (postal_code, house_number, addition or ""),
                )
                row = cur.fetchone()
                if row:
                    return {"value": row[0], "reference_date": row[1], "verified": row[2], "source": row[3]}
        except Exception as exc:
            log.warning("get_cached_woz failed: %s", exc)
        return None

    def cache_woz(
        self,
        postal_code: str,
        house_number: str,
        addition: str,
        value: int,
        reference_date: str | None,
        verified: bool,
        source: str,
    ) -> None:
        try:
            with self._cursor() as cur:
                cur.execute(
                    "INSERT INTO woz_cache "
                    "(postal_code, house_number, house_number_addition, woz_value, reference_date, verified, source) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (postal_code, house_number, house_number_addition) DO UPDATE SET "
                    "woz_value = EXCLUDED.woz_value, reference_date = EXCLUDED.reference_date, "
                    "verified = EXCLUDED.verified, source = EXCLUDED.source, fetched_at = NOW()",
                    (postal_code, house_number, addition or "", value, reference_date, verified, source),
                )
        except Exception as exc:
            log.warning("cache_woz failed: %s", exc)

    # ── Scrape runs ──

    def log_scrape_run(
        self,
        source: str,
        total_found: int,
        new_found: int,
        bustable_found: int = 0,
        error: str | None = None,
    ) -> None:
        try:
            with self._cursor() as cur:
                cur.execute(
                    "INSERT INTO scrape_runs "
                    "(started_at, finished_at, source, total_found, new_found, bustable_found, errors) "
                    "VALUES (NOW(), NOW(), %s, %s, %s, %s, %s)",
                    (source, total_found, new_found, bustable_found, error),
                )
        except Exception as exc:
            log.warning("scrape run log failed: %s", exc)

    # ── Telegram subscribers ──

    def get_telegram_subscribers(self) -> list[int]:
        try:
            with self._cursor() as cur:
                cur.execute("SELECT chat_id FROM telegram_subscribers")
                return [row[0] for row in cur.fetchall()]
        except Exception as exc:
            log.warning("load subscribers failed: %s", exc)
            return []

    def add_telegram_subscriber(self, chat_id: int, username: str, first_name: str) -> bool:
        try:
            with self._cursor() as cur:
                cur.execute(
                    "INSERT INTO telegram_subscribers (chat_id, username, first_name) "
                    "VALUES (%s, %s, %s) ON CONFLICT (chat_id) DO NOTHING",
                    (chat_id, username, first_name),
                )
                return (cur.rowcount or 0) > 0
        except Exception as exc:
            log.warning("add subscriber failed: %s", exc)
            return False

    def remove_telegram_subscriber(self, chat_id: int) -> bool:
        try:
            with self._cursor() as cur:
                cur.execute(
                    "DELETE FROM telegram_subscribers WHERE chat_id = %s",
                    (chat_id,),
                )
                return (cur.rowcount or 0) > 0
        except Exception as exc:
            log.warning("remove subscriber failed: %s", exc)
            return False

    # ── Top listings ──

    def get_top_listings(self, limit: int = 5) -> list[dict]:
        """Return the top bustable listings ranked by bust_score."""
        try:
            with self._cursor() as cur:
                cur.execute(
                    """
                    SELECT source, source_id, url, street, house_number, house_number_addition,
                           postal_code, city, asking_rent, surface_area_m2, num_rooms,
                           energy_label, wws_points, wws_max_rent, wws_savings, wws_confidence,
                           woz_value, woz_verified, bust_score, first_seen_at, available_from
                    FROM listings
                    WHERE wws_is_bustable = TRUE AND disappeared_at IS NULL
                    ORDER BY bust_score DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                cols = [desc[0] for desc in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception as exc:
            log.warning("get_top_listings failed: %s", exc)
            return []

    # ── Bulk load ──

    def load_all_listings(self) -> list[dict]:
        try:
            with self._cursor() as cur:
                cur.execute(
                    "SELECT source, source_id, url, street, house_number, house_number_addition, "
                    "postal_code, city, neighborhood, asking_rent, surface_area_m2, num_rooms, "
                    "energy_label, construction_year, property_type, interior, description, "
                    "images, available_from, agency_name, "
                    "woz_value, woz_reference_date, woz_verified, "
                    "rb_estimated_max_rent, rb_savings, rb_confidence "
                    "FROM listings ORDER BY first_seen_at DESC"
                )
                cols = [desc[0] for desc in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception as exc:
            log.warning("load_all_listings failed: %s", exc)
            return []

    # ── Schema init ──

    def migrate(self) -> None:
        """Apply incremental schema migrations (idempotent ALTER TABLE statements)."""
        migrations = [
            "ALTER TABLE listings ADD COLUMN IF NOT EXISTS bust_score REAL DEFAULT 0",
            (
                "CREATE INDEX IF NOT EXISTS idx_listings_bust_score "
                "ON listings (bust_score DESC) WHERE wws_is_bustable = TRUE"
            ),
        ]
        with self._cursor() as cur:
            for stmt in migrations:
                try:
                    cur.execute(stmt)
                except Exception as exc:
                    log.warning("migration failed (%s): %s", stmt[:60], exc)

    def init_schema(self, schema_sql: str) -> None:
        with self._cursor() as cur:
            cur.execute(schema_sql)
        self.migrate()

    def close(self) -> None:
        if self._conn and not self._conn.closed:
            self._conn.close()
