"""Command-line entry point: `python -m rentbuster`."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rentbuster import __version__
from rentbuster.config import load_settings
from rentbuster.db import Database
from rentbuster.notify import build_notifiers
from rentbuster.profile import PROFILES_DIR, load_profile
from rentbuster.scheduler import RentBuster

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s · %(message)s",
        datefmt="%H:%M:%S",
    )


def _ensure_schema(db: Database) -> None:
    """Apply schema.sql automatically so a fresh DB is ready with no manual init-db step.

    schema.sql is written entirely with CREATE TABLE/INDEX IF NOT EXISTS, so running it
    on every startup is a safe no-op once the schema already exists.
    """
    if not SCHEMA_PATH.exists():
        logging.warning("schema file not found at %s — skipping auto schema init", SCHEMA_PATH)
        return
    try:
        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        db.init_schema(schema_sql)
        logging.info("database schema is up to date")
    except Exception as exc:
        logging.error("automatic schema init failed: %s", exc)


def _cmd_init_db(args: argparse.Namespace) -> int:
    settings = load_settings()
    if not settings.database_url:
        print("error: DATABASE_URL is not set", file=sys.stderr)
        return 1
    if not SCHEMA_PATH.exists():
        print(f"error: schema file not found at {SCHEMA_PATH}", file=sys.stderr)
        return 1
    db = Database(settings.database_url)
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    print(f"running {SCHEMA_PATH} against {settings.database_url.rsplit('@', 1)[-1]}")
    db.init_schema(schema_sql)
    db.close()
    print("schema initialized ✓")
    return 0


def _cmd_top(args: argparse.Namespace) -> int:
    settings = load_settings()
    if not settings.database_url:
        print("error: DATABASE_URL is not set", file=sys.stderr)
        return 1
    db = Database(settings.database_url)
    listings = db.get_top_listings(args.limit)
    db.close()

    if not listings:
        print("No bustable listings found.")
        return 0

    col_widths = {
        "rank": 4,
        "address": 35,
        "asking": 10,
        "max_rent": 10,
        "pts": 6,
        "savings": 10,
        "conf": 10,
        "score": 8,
        "source": 14,
    }
    header = (
        f"{'#':<{col_widths['rank']}} "
        f"{'Address':<{col_widths['address']}} "
        f"{'Asking':>{col_widths['asking']}} "
        f"{'Max rent':>{col_widths['max_rent']}} "
        f"{'Pts':>{col_widths['pts']}} "
        f"{'Savings':>{col_widths['savings']}} "
        f"{'Confidence':<{col_widths['conf']}} "
        f"{'Score':>{col_widths['score']}} "
        f"{'Source':<{col_widths['source']}}"
    )
    sep = "-" * len(header)
    print(f"\nTop {len(listings)} bustable listings (ranked by bust score)\n")
    print(header)
    print(sep)

    for i, r in enumerate(listings, 1):
        street = r.get("street") or ""
        num = r.get("house_number") or ""
        addition = r.get("house_number_addition") or ""
        addr = f"{street} {num}"
        if addition:
            addr += f"-{addition}"
        city = (r.get("city") or "").title()
        full_addr = f"{addr}, {city}"
        asking = r.get("asking_rent") or 0
        max_rent = r.get("wws_max_rent") or 0
        pts = r.get("wws_points") or 0
        savings = r.get("wws_savings") or 0
        conf = r.get("wws_confidence") or "?"
        score = r.get("bust_score") or 0
        source = r.get("source") or "?"
        url = r.get("url") or ""

        print(
            f"{i:<{col_widths['rank']}} "
            f"{full_addr[: col_widths['address']]:<{col_widths['address']}} "
            f"€{asking:>{col_widths['asking'] - 1}} "
            f"€{max_rent:>{col_widths['max_rent'] - 1}.0f} "
            f"{pts:>{col_widths['pts']}.0f} "
            f"€{savings:>{col_widths['savings'] - 1}.0f} "
            f"{conf:<{col_widths['conf']}} "
            f"{score:>{col_widths['score']}.1f} "
            f"{source:<{col_widths['source']}}"
        )
        print(f"     {url}")

    print(sep)
    return 0


def _cmd_recalculate(args: argparse.Namespace) -> int:
    import json

    from rentbuster.llm import LLMExtraction, extract_batch
    from rentbuster.models import EnergyLabel, Listing, Source
    from rentbuster.woz import lookup_woz
    from rentbuster.wws import calculate_wws

    settings = load_settings()
    if not settings.database_url:
        print("error: DATABASE_URL is not set", file=sys.stderr)
        return 1

    profile = load_profile(settings.profile)
    db = Database(settings.database_url)
    rows = db.load_all_listings()
    if not rows:
        print("no listings in database")
        db.close()
        return 0

    print(f"loaded {len(rows)} listings from database")

    listings: list[Listing] = []
    for r in rows:
        images = r["images"] or "[]"
        if isinstance(images, str):
            images = json.loads(images)
        listings.append(
            Listing(
                source=Source(r["source"]),
                source_id=r["source_id"],
                url=r["url"],
                street=r.get("street") or "",
                house_number=r.get("house_number") or "",
                house_number_addition=r.get("house_number_addition") or "",
                postal_code=r.get("postal_code") or "",
                city=r.get("city") or "",
                neighborhood=r.get("neighborhood") or "",
                asking_rent=r.get("asking_rent") or 0,
                surface_area_m2=r.get("surface_area_m2") or 0,
                num_rooms=r.get("num_rooms") or 0,
                energy_label=EnergyLabel.from_string(r.get("energy_label")),
                construction_year=r.get("construction_year"),
                property_type=r.get("property_type") or "",
                interior=r.get("interior") or "",
                description=r.get("description") or "",
                images=images,
                available_from=r.get("available_from") or "",
                agency_name=r.get("agency_name") or "",
                woz_value=r.get("woz_value"),
                woz_reference_date=r.get("woz_reference_date"),
                woz_verified=bool(r.get("woz_verified")),
                rb_points=r.get("rb_points"),
                rb_estimated_max_rent=r.get("rb_estimated_max_rent"),
                rb_savings=r.get("rb_savings"),
                rb_confidence=r.get("rb_confidence"),
            )
        )

    extractions: dict[str, LLMExtraction] = {}
    if settings.llm_enabled and settings.gemini_api_key:
        print("running LLM extraction...")
        extractions = extract_batch(listings, settings.gemini_api_key, settings.llm_model)
        print(f"extracted features for {len(extractions)}/{len(listings)} listings")
    else:
        print("LLM disabled — recalculating with defaults only")

    print("recalculating WWS scores...")
    bustable = 0
    for listing in listings:
        if not listing.woz_value:
            lookup_woz(listing)
        key = f"{listing.source.value}:{listing.source_id}"
        calculate_wws(listing, llm_extraction=extractions.get(key), defaults=profile.wws)
        if listing.wws_is_bustable:
            bustable += 1

    if not args.dry_run:
        print("updating database...")
        for listing in listings:
            db.upsert_listing(listing)
        print(f"updated {len(listings)} listings ({bustable} bustable)")
    else:
        print(f"dry-run: would update {len(listings)} listings ({bustable} bustable)")

    db.close()
    return 0


def _cmd_list_profiles(args: argparse.Namespace) -> int:
    profiles = sorted(PROFILES_DIR.glob("*.yaml"))
    if not profiles:
        print(f"no profiles found in {PROFILES_DIR}")
        return 1
    print(f"profiles in {PROFILES_DIR}:")
    for path in profiles:
        try:
            p = load_profile(path.stem)
            print(f"  • {p.name:<35} {p.description}")
        except Exception as exc:
            print(f"  ! {path.stem:<35} (load error: {exc})")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    settings = load_settings()
    if args.profile:
        settings.profile = args.profile

    profile = load_profile(settings.profile)

    db: Database | None = None
    if settings.database_url:
        db = Database(settings.database_url)
        _ensure_schema(db)
    else:
        logging.warning("DATABASE_URL not set — running without persistence")

    notifiers = build_notifiers(settings, db, profile=profile)
    rentbuster = RentBuster(
        settings=settings,
        profile=profile,
        db=db,
        notifiers=notifiers,
        dry_run=args.dry_run,
    )

    try:
        if args.once:
            rentbuster.check_once()
        else:
            rentbuster.run_forever()
    finally:
        if db:
            db.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rentbuster",
        description="RentBuster — find Amsterdam apartments where asking rent exceeds the legal WWS maximum.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")

    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="scrape and notify (default)")
    run_parser.add_argument("--once", action="store_true", help="run a single check and exit")
    run_parser.add_argument(
        "--profile",
        help="profile name or path to YAML file; overrides PROFILE env",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and score but skip notifications and DB writes",
    )
    run_parser.set_defaults(func=_cmd_run)

    subparsers.add_parser(
        "init-db", help="run schema.sql against DATABASE_URL to create tables"
    ).set_defaults(func=_cmd_init_db)

    top_parser = subparsers.add_parser("top", help="show top bustable listings ranked by bust score")
    top_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        metavar="N",
        help="number of listings to show (default: 10)",
    )
    top_parser.set_defaults(func=_cmd_top)

    recalc_parser = subparsers.add_parser(
        "recalculate", help="re-score all listings in the database with LLM extraction + WWS"
    )
    recalc_parser.add_argument("--dry-run", action="store_true", help="recalculate but don't write to DB")
    recalc_parser.set_defaults(func=_cmd_recalculate)

    subparsers.add_parser("list-profiles", help="list available profiles").set_defaults(
        func=_cmd_list_profiles
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "verbose", False))

    if not args.command:
        args.command = "run"
        args.once = False
        args.profile = None
        args.dry_run = False
        args.func = _cmd_run

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
