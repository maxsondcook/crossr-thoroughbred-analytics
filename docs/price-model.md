# Sale-price modeling pipeline (master prompt Phases 12–14)

Infrastructure for predicting the **actual observed sale price** of a horse at a
specific sale. Final training (Phase 15) waits for the data-readiness gate
; the latest preliminary run is in
[price-model-preliminary.md](price-model-preliminary.md).

| Piece | Code |
|---|---|
| Dataset, feature groups, exclusions, leakage check | `crossr_core/price_features.py` |
| Metrics, baselines, models, intervals, segments, SHAP, comparables | `crossr_core/price_eval.py` |
| End-to-end preliminary run and report | `scripts/price_model_preliminary.py` |
| Unit tests (one row per transaction, no future fees / results, truncation) | `tests/test_price_features.py` |

The live app still prices lots with the validated Tobit model (`docs/sales-model.md`).
The other value targets stay separate: stallion deal value, added value, broodmare
breeding value, racing value and the covering-fee estimate (`docs/breeding-value.md`)
are not sale prices.

## Dataset (Phase 14.1)

`build_transactions` keeps **every `sales_lots` row once**, with:

- `transaction_id`: hash of catalog file + hip + horse; a repeated catalog row is a
  duplicate transaction.
- `horse_id`: sire + dam + foaling year. Unnamed youngstock change names later, so
  this follows the horse across sales. A horse sold as a weanling, a yearling and a
  2-year-old has three rows.
- Sale year, month (inferred from the catalog title, since sale dates aren't stored),
  venue, category (by age at sale, sex and title), price (USD; every catalog is North
  American), status (sold / RNA with last bid / RNA without bid / out / no price).
- `model_exclusion`: the reason a row is left out of the yearling/weanling model.
  Duplicate, missing years, impossible age, withdrawn, no price, price above $20M,
  other category, missing sire or dam. Nothing is dropped silently.
- Quality flags kept on the row: catalog title vs age mismatch, same horse twice in
  one sale.
- `Sources` records provenance per table: Postgres, or a CSV snapshot for a named table.

## Features (Phase 12.2), all as of the sale

| Group | Features |
|---|---|
| A sire | fee at conception (and whether it is a historical fee), initial, highest and latest fee to the sale year, latest vs initial, years listed; earlier foals' prices vs their sale (shrunk), count, graded winners to date; progeny racing to the year before (seasons, earnings per runner, win / stakes / graded rate from the leading-sire tables) |
| B dam production | earlier foals' prices (shrunk) and count; foals before this one, foals aged 3+; progeny that had raced / won / won stakes / graded / Grade 1 before the sale year; progeny earnings; % raced, % won, stakes winners per foal aged 3+ (exposure-adjusted); graded winners / placed to date; dam's age at foaling |
| C dam race record | found, starts, wins, earnings, stakes / graded / Grade 1 wins, best speed figure, raced (seasons and starts dated before the sale year) |
| D broodmare sire | earlier grand-foals' prices (shrunk); fee at conception, initial, highest, latest; latest vs initial; years listed |
| E pedigree | sire × broodmare-sire cross residual (shrunk); dam's 4-generation pedigree completeness |
| F progression | sale's average last year, new sale, market trend, market median last year; sire's and broodmare sire's last-year level and trend |
| G horse and sale | weanling, filly, hip position curve for this sale, hip position, sale month, venue |
| X interactions | sire fee × dam record, sire fee × broodmare-sire fee (filly × yearling is in G) |

Not included, and why:
- **Sire and broodmare sire as raw categories:** thousands of sparse columns. They
  enter as shrunk track records instead.
- **Inbreeding:** the pedigree store isn't complete enough yet.
- **Consignor:** missing on most Keeneland lots before 2025.
- **Physical, conformation and veterinary data:** not available.

**Leakage rules (Phase 14.4).**
- Sales, races, rankings and progeny results count only from years before the sale
  year. Sale dates are not stored, so same-year events are excluded.
- Stud fees count through the sale year: a season's fee is advertised before that
  season's sales.
- A graded placing counts from the year it happened, even if the horse later wins.
- A same-named older stallion's fees are separated at a 4+ year gap, using only
  earlier fees.

The same placing rule is now used by the live model's training script
(`scripts/train_sales_model.py`); the live model was trained before that fix, but the
affected feature (dam's graded-placed foals) was already dropped by its keep/drop rule.

`truncation_check(sources, year)` rebuilds the features of a year's sales from source
tables cut off at that year, with later fees, races, rankings and sales removed and that
year's prices blanked, and requires them to equal the full build. In the preliminary
run: 0 mismatched cells across all 74 features for 2012, 2018 and 2024.

## Validation and evaluation (Phases 13, 15.2–15.4)

- **Time-based split:** train ≤ 2018, early stopping 2019–2020, interval calibration
  2021–2022, test 2023–2025.
- **Group-aware check:** test sales whose dam had no sale in the training years.
- **Baselines** (each from sales before the test sale's year):
  - sale-category median
  - sire-year mean and median
  - comparable sales: same sire at the same sale, last two years
  - a regression on sire fee + sex + age + sale level
- **Models:** ridge on all features, with missing indicators; gradient boosting
  (LightGBM, missing values handled natively).
- **Metrics:** R², adjusted R² (linear models only), MAE, RMSE, median absolute
  error, residual SD, median absolute % error, dollar errors.
- **Prediction intervals (80%):** split conformal on the calibration years, normalized
  by segment (yearling/weanling × sire track record × dam track record × fee known),
  so the range widens where the model has erred more. Coverage and width are
  reported on the test years.
- **Quantile intervals:** conformalized quantile regression (LightGBM 10th / 90th
  percentile models, widened by the calibration years' miss scores). In the
  preliminary run it holds ~80% coverage in every predicted-price band (79–83%), with
  ranges that adapt to the horse (about 6.5× upper/lower for $500k+ horses, about 18×
  under $50k). The group-normalized conformal method ranged 75–90% by band, so CQR is
  the recommended method for the final model. Coverage is judged by predicted price:
  grouping by actual price conditions on the outcome.
- **Interaction search:** each Phase 12.4 pair is added to the ridge model one at a time
  and kept only if it raises held-out R². None adds more than +0.003 (sire fee ×
  weanling is the largest); gradient boosting already models interactions.
- **Segments:** sale year, venue, yearling / weanling, sex, price band, fee known,
  dam record found, broodmare-sire fee known, most-sold sires and broodmare sires.
- **Feature-group value:** drop one group, refit, score the test years.
- **Broodmare-sire fee:** highest vs latest vs at conception compared head to head.
- **Explanations:** mean |SHAP| per feature and group; `comparable_sales` for a lot.

## Running it

```bash
# training environment: pip install -r requirements-model.txt (pandas, numpy, scipy, lightgbm)
scripts/pipeline/railway_env.sh crossr_dev <python> scripts/price_model_preliminary.py
# crossr_dev holds a partial copy of some tables; take them from a full snapshot instead:
scripts/pipeline/railway_env.sh crossr_dev <python> scripts/price_model_preliminary.py \
    --csv-dir ~/Desktop/Crossr/data --csv-tables sales_lots race_horses race_seasons race_starts \
    graded_stakes breeding_seasons stallion_rankings
python -m pytest tests/test_price_features.py
```
