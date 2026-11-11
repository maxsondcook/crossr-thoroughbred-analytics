# Breeding and racing value (master prompt Phases 10–11)

Code: `crossr_core/breeding_value.py`, used by `ValueProjector.project_hip_value`
(colt / filly / broodmare breeding value and racing value on every sale lot).
Everything is computed from Postgres tables at runtime and cached per table
version; the API builds the tables in its startup warm-up thread.

## Identity

Stallions are their stud cards (`sample_db.stallion_roster_from_fees`): first
season and foaling year are identity-checked there. A son is matched to his sire by
the sire and foaling year the leading-sire lists record for him (`identity.card_sire`),
else by a `race_horses` row with his exact name **and** foaling year, and only if he
was foaled after his sire's first crop could be. Never by name alone. Only stallions
standing in North America (`identity.stands_in_north_america`) count as sons at stud
and as comparables; other countries' data is kept for later.

## Phase 10 — expected stallion deal value (one value per colt)

    deal value of a son        = his initial stud fee × 400
    colts of racing age        = (total foals − foals born in the last three years) / 2
                               = half his live foals aged 3+ (breeding_seasons, crop = season + 1)
    value of a colt            = Σ deal values of the sire's sons at stud / colts of racing age

- **Initial stud fee** = his fee in his first season (or the first listed within two
  years). The fee table often starts only when a stallion first makes a ranking list,
  so many sons have none (Audible: first fee on file 2023, first season 2020). Those
  sons' initial fee is **estimated** from their first-season book, log fee = a + b · log
  mares bred, fitted on stallions whose initial fee is on file (r = 0.65, n = 1,260),
  and each is labelled "estimated". The value with fees on file only is also returned
  (`value_fees_on_file_only`).
- **Limited racing-age progeny** = first crop under **seven** years old (decision
  2026-10-31; the prompt's example line was four). A sire's first son reaches stud a
  median seven years after his first crop (730 sires; quartiles 5 and 9), so before
  that his own sons say little. Such a sire is valued from **comparable stallions**:
  North American, first crop at least seven years old, initial fee within ±10% of his
  initial fee or current fee within ±10% of his current fee. Each comparable's own value
  per colt is scaled by **target fee / comparable fee** (the fee that matched: $200,000
  vs $220,000 → × 0.909), and the colt's value is their mean. The comparables, the fee
  each matched on and the factors are returned with the value.
- No sire's-sire fallback; a sire with no card, or a young sire with no comparable,
  gets no value.

`COMPARABLE_MIN_CROP_AGE` (7) sets both lines.

## Phase 11

### 1. Added value

Every yearling and weanling sale with a price (RNAs, outs, unpriced lots and duplicate
catalog rows — same file, hip and horse — excluded):

    added value = sale price / sire's stud fee at conception (foaling year − 1)

When the sire has no fee that year (dead, pensioned, or unlisted), his latest earlier
fee is used and the lot is flagged `historical_fee` (6% of lots). Summaries (n, mean,
median, p25, p75): overall, by broodmare sire, by sale year, by yearling / weanling.
Broodmare sire = the lot's damsire, else the dam's sire from `race_horses`.

Full sales history (147,685 valid lots): median 1.6, mean 3.0, p25–p75 0.6–3.6;
yearlings median 1.63, weanlings 1.50.

### 2–3. Broodmare-sire fee relationship and future fee estimate

    log(covering sire's fee at conception) = a + b · log(broodmare sire's highest fee)

Highest rather than latest: a broodmare sire's fee only falls after his peak, and the
mating decisions reflect his standing. On the full history: b = 0.19 (SE 0.003,
t = 61), R² = 0.055, n = 64,528 lots. The relationship is real but weak: a broodmare
sire with a 10× higher fee goes with covering sires about 1.55× pricier.

For a broodmare sire with no grandchildren at auction or fewer than 20 sold
(`MIN_BMS_LOTS`), `expected_cover_fee(fee)` returns the estimated covering fee with
an 80% prediction interval and the method, labelled "estimate (not an advertised
fee)". Inputs outside the fees seen in fitting are clamped to that range (no
extrapolation).

### 4. Broodmare breeding value

    value = realized sale prices of her foals sold
          + remaining foals × expected covering fee × her added-value multiple

- Remaining foals = 7 expected live foals (project rule: 10 foals × 0.70) − foals
  already sold, so a mare with many good foals keeps that credit.
- Expected covering fee: the mean fee behind her last three sold foals; for a filly or
  an unproven mare, the Phase 11.3 estimate from her sire's highest fee.
- Added-value multiple: her own mean log added value, shrunk toward her sire's (as
  broodmare sire), which is itself shrunk toward the overall mean (empirical Bayes;
  k = 9 lots, estimated from the data).
- Range (when her sire has ≥ 20 lots): realized + remaining × covering fee × his p25 /
  p75 added value.
- Racing performance of progeny is not used: the dam's stakes-winning foals added
  nothing significant to price once her foals' sale prices were known (docs/sales-model.md).

### 5. Racing value (per foal by the sire)

    racing value = average earnings per runner × (runners / foals aged 3+)

- Earnings and runners: one North American leading-sire row per stallion and
  season (General list, else his First/Second/Third-Crop row), summed over seasons, in
  USD. "Runners" are runner-seasons, consistent with how earnings are summed, so
  runners / foals can exceed 1; the product is career progeny earnings per foal aged 3+.
- Foals aged 3+: live foals in crops at least three years old (breeding_seasons).
- Seasons in which he made no list are missing, which understates lesser sires.
- A sire with no leading-sire seasons yet (no runners on file) is valued from the
  Phase 10 comparables, scaled by fee ratio.

### 6. Geldings

No breeding value; racing value only.
