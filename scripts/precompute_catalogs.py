"""
Precompute upcoming-sale hip pages into Postgres (crossr_core/hip_cache.py).

    scripts/pipeline/railway_env.sh crossr_dev .venv/bin/python scripts/precompute_catalogs.py [catalog ...]

    (no flags)    stale hips only: never stored, or stored before the catalog,
                  models or source tables last changed. Run nightly and after loads.
    --full        every hip, stored or not.
    --rescraped   only hips whose pedigree scrape finished after their page was
                  stored (the data pipeline's upcoming_hip_status table).

With no catalog names it covers every upcoming catalog.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("catalogs", nargs="*")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--full", action="store_true")
    mode.add_argument("--rescraped", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="at most this many hips per catalog (testing)")
    args = ap.parse_args()

    from crossr_api import app as api
    from crossr_core import hip_cache, railway_db, upcoming

    catalogs = args.catalogs or [row["id"] for row in railway_db.list_upcoming_catalogs()]
    started = time.time()
    book = api.profiles()
    print(f"warm-up {time.time() - started:.1f}s", flush=True)
    for catalog_id in catalogs:
        lots = upcoming.list_lots(catalog_id)
        if lots is None:
            print(f"{catalog_id}: not found", flush=True)
            continue
        hips = [lot["hip"] for lot in lots if lot.get("hip")]
        if args.rescraped:
            hips = [h for h in hips if h in set(hip_cache.rescraped_hips(catalog_id))]
        elif not args.full:
            hips = hip_cache.stale_hips(catalog_id, hips, hip_cache.inputs_fingerprint())
        if args.limit is not None:
            hips = hips[: args.limit]
        print(f"{catalog_id}: {len(hips):,} of {len(lots):,} hips to compute", flush=True)
        if not hips:
            continue

        def progress(done, total, secs):
            rate = secs / done
            print(f"  {done:,}/{total:,}  {rate:.2f}s/hip  ~{(total - done) * rate / 60:.0f} min left", flush=True)

        result = hip_cache.build(
            catalog_id,
            lots,
            lambda cid, lot: api.build_upcoming_lot_payload(cid, lot, book),
            hips=hips,
            progress=progress,
        )
        print(f"  done: {result['hips']:,} hips in {result['seconds'] / 60:.1f} min, {result['failed']} failed", flush=True)


if __name__ == "__main__":
    main()
