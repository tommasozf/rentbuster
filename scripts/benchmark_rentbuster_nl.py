"""Compare our WWS scoring with the points and max rent rent-buster.nl attaches to its ads.

rent-buster.nl already publishes a points total and an estimated max rent for every ad in its
feed. Scoring those same ads with our calculator and looking at the gap is the quickest way to
see whether a change to wws.py made things better or worse.

    uv run python scripts/benchmark_rentbuster_nl.py            # Amsterdam, all pages
    uv run python scripts/benchmark_rentbuster_nl.py --pages 5  # quicker
    uv run python scripts/benchmark_rentbuster_nl.py --show 15  # print the 15 biggest gaps

No database, no LLM, no notifications: this only reads the public feed.
"""

from __future__ import annotations

import argparse
import statistics

from rentbuster.models import Listing
from rentbuster.sources.rentbuster_nl import RentbusterNLSource
from rentbuster.wws import LIBERALIZATION_THRESHOLD, calculate_wws


def _rb_bustable(listing: Listing, rb_points: float, rb_max: float) -> bool:
    return rb_points < LIBERALIZATION_THRESHOLD and listing.asking_rent > rb_max


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--city", default="amsterdam")
    ap.add_argument("--pages", type=int, default=25)
    ap.add_argument("--show", type=int, default=10, help="print the N listings with the biggest point gap")
    args = ap.parse_args()

    source = RentbusterNLSource(city=args.city, max_pages=args.pages)
    listings = source._fetch_listings_sync()
    rows = []
    for ls in listings:
        rb_points, rb_max = ls.wws_points, ls.rb_estimated_max_rent
        if not rb_points or not rb_max or not ls.asking_rent or ls.property_type == "room":
            continue
        calculate_wws(ls)
        rows.append((ls, rb_points, rb_max))

    if not rows:
        print("no comparable listings found")
        return 1

    gaps = [ls.wws_points - rb_pts for ls, rb_pts, _ in rows]
    ours_bust = [ls.wws_is_bustable for ls, _, _ in rows]
    theirs_bust = [_rb_bustable(ls, rb_pts, rb_max) for ls, rb_pts, rb_max in rows]
    agree = sum(a == b for a, b in zip(ours_bust, theirs_bust, strict=True))
    only_ours = sum(a and not b for a, b in zip(ours_bust, theirs_bust, strict=True))
    only_theirs = sum(b and not a for a, b in zip(ours_bust, theirs_bust, strict=True))

    print(f"{len(listings)} listings fetched, {len(rows)} with rent-buster.nl points and max rent\n")
    print(
        f"points gap (ours - theirs):  mean {statistics.mean(gaps):+.1f}   median {statistics.median(gaps):+.1f}"
    )
    print(f"within 10 points:            {sum(abs(g) <= 10 for g in gaps)}/{len(gaps)}")
    print(f"bustable, ours / theirs:     {sum(ours_bust)} / {sum(theirs_bust)}")
    print(
        f"agreement on bustable:       {agree}/{len(rows)}   (only we flag: {only_ours}, only they flag: {only_theirs})\n"
    )

    rows.sort(key=lambda r: abs(r[0].wws_points - r[1]), reverse=True)
    print(
        f"{'address':36s} {'ask':>5s} {'m2':>3s} {'lbl':>4s} {'ours':>6s} {'ours max':>8s} {'theirs':>6s} {'their max':>9s}"
    )
    for ls, rb_pts, rb_max in rows[: args.show]:
        addr = f"{ls.street} {ls.house_number}{'-' + ls.house_number_addition if ls.house_number_addition else ''}"
        label = ls.energy_label.value if ls.energy_label else "?"
        print(
            f"{addr[:36]:36s} {ls.asking_rent:5d} {ls.surface_area_m2:3d} {label:>4s} "
            f"{ls.wws_points:6.0f} {ls.wws_max_rent:8.0f} {rb_pts:6.0f} {rb_max:9.0f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
