# Precomputed hip pages

Upcoming-sale hip pages (`GET /api/sales/upcoming/{catalog}/lots/{hip}`) are
precomputed per catalog and stored in Postgres, so opening a hip is one read.
Code: `crossr_core/hip_cache.py`, `scripts/precompute_catalogs.py`.

## Behaviour

- **Never blocks.** A catalog shows on the site as soon as it is loaded. A hip with
  no stored page is computed live, stored and returned; later opens read it.
- **Stays current.** Each stored page records a fingerprint of its inputs: the
  catalog, the models (`sales_model`, `fee_bands`), the tables
  `sales_lots`, `stud_fees`, `breeding_seasons`, `race_horses`, `race_seasons`,
  `race_starts`, `graded_stakes`, `stallion_rankings`, `pedigrees`
  (`crossr_table_versions`), and `CACHE_VERSION` (bump it when the page's code or
  shape changes). A stale page is returned at once (`cache.stale = true`) and
  refreshed in the background.
- **Live fields.** Stars / notes and the pedigree-scrape status are read on every
  open, never stored with the page.

Response additions:

| Field | Meaning |
|---|---|
| `data_status.status` | `complete` / `updating` / `not_found` / `null` (from the pipeline's `upcoming_hip_status`) |
| `data_status.catalog_complete` | `true` once the pipeline marks the whole catalog scraped (`upcoming_catalog_status`) |
| `cache.computed_at`, `cache.stale` | when the stored page was built; whether a refresh is under way |

## Running it

```bash
E=scripts/pipeline/railway_env.sh
$E crossr_dev .venv/bin/python scripts/precompute_catalogs.py                       # all upcoming catalogs, stale hips (nightly)
$E crossr_dev .venv/bin/python scripts/precompute_catalogs.py "Keeneland November Breeding Stock Sale (2026)"
$E crossr_dev .venv/bin/python scripts/precompute_catalogs.py --rescraped "<catalog>"   # hips re-scraped since stored
$E crossr_dev .venv/bin/python scripts/precompute_catalogs.py --full "<catalog>"        # every hip (catalog complete)
$E railway .venv/bin/python scripts/load_railway.py --only catalogs --precompute         # load + precompute
```

The data pipeline calls these: after publishing a catalog, after each scrape
batch (`--rescraped`), once the catalog is complete (`--full`), and nightly.

## Speed (crossr_dev, from the Mac)

Keeneland November Breeding Stock Sale (2026), 3,405 hips:

- Full precompute: 25.4 minutes, 0 failures (~0.45 s per hip once warm; needs the
  lot-page speed fixes, 7.4 s per hip without them).
- Stored read, 40 random hips: median 214 ms, p90 224 ms end to end from the Mac,
  most of it the network round trip to Railway (one connection per read).
- A hip with no stored page: computed live (first open ~1 s once the API is warm).

## Tables

- `upcoming_hip_cache(catalog_id, hip, payload jsonb, inputs, computed_at)`: owned
  here, created on first use; rows of a removed catalog are deleted by
  `load_railway.py`.
- `upcoming_hip_status(catalog_id, hip, status, updated_at)` and
  `upcoming_catalog_status(catalog_id, complete, updated_at)`: owned by the data
  pipeline; read only if they exist.
