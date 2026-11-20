# CROSSR models: summary of results

*2026-11-19. The data isn't in this repository (see Limits), so the numbers below
can't be re-run from a fresh clone. The no-peeking tests can: they use synthetic data.*

## What was built

The project has two models:

1. **Expected sale price** of a thoroughbred at auction, as a weanling, yearling or
   2-year-old, with an 80% range.
2. **Expected breeding value:**
   - **Stallions:** lifetime AEI (Average Earnings Index: his foals' race earnings compared
     with the average runner, where 1.00 is average).
   - **Mares:** her foal's sale price as a multiple of the stud fee.

The models use public auction results (Keeneland, Fasig-Tipton, OBS), IFHA graded-stakes
results, stud fees and race records collected for the CROSSR app. None of it is redistributed
here.

## How the models were tested

- **Time split, never random.** Training uses earlier years and scoring uses later ones. The
  price model's test years are **2021–2025**, which were never used to fit or tune anything.
- **Retrained every year.** For each test year Y, the price model is trained only on years
  before Y, the way it would be used in practice.
- **No peeking, enforced by tests.** Every input is built from information available before
  the horse sold. `tests/test_leakage.py` scrambles the prices of every horse sold at the same
  time or later and checks that a prediction doesn't move. It catches deliberately injected
  leaks. Run it with `python -m pytest models_final/tests -q`; 17 tests pass.
- **Unsold horses count.** A horse that went unsold (RNA) shows the price was *below* the
  reserve. It's treated as an upper bound, not dropped (a censored model).
- **The scores used:**
  - **R²** on log price: the share of price variation explained.
  - **Typical miss:** the median factor between predicted and actual price; ×1.8 means
    within about 1.8× either way for half of horses.
  - **80% range coverage.**
  - **Accuracy in dollars.**

## 1. Sale price

### The final model

**The core:**
- **Algorithm:** gradient-boosted trees (LightGBM).
- **Training:** retrained each year on all earlier years, with recent years weighted more
  (half-life 8 years).
- **Unsold horses:** handled as described above.

**What it knows about each horse, all from before the sale:**
- **The sale and the market:**
  - the sale's average price last year;
  - a forecast of this year's market move, based on earlier sales this year.
- **The horse's place in the sale:**
  - hip position in the catalog (Keeneland orders horses by quality);
  - consignor.
- **Sire:**
  - stud fee;
  - his earlier foals' prices;
  - his graded-stakes winners (IFHA);
  - his crop number.
- **Dam:**
  - her earlier foals' prices;
  - her produce record: black-type, graded winner, G1 winner, multiple G1 winner (IFHA);
  - her own race class;
  - her age.
- **The horse itself:**
  - sex and age;
  - foaling state and date;
  - the price at its own earlier sale, if it was resold.

**Three corrections, each learned only from earlier data:**
- **Bias shift:** the model's average miss over the previous three years is added back.
- **Within-sale update:** once a sale's earlier sessions have sold, later horses move by how
  far off those were. The amount is scaled down when only a few horses have sold.
- **Sire update:** a sire whose foals have sold above or below the model earlier this year
  moves his later foals the same way, again scaled down when the evidence is thin.

### Results (test years 2021–2025)

**How the model improved, step by step.** Each row adds to the one above:

| Model | Lots scored | R² | Typical miss | 80% range holds |
|---|---|---|---|---|
| v1: linear regression, yearlings only | 26,690 yearlings | 0.399 | ×2.23 | 82% |
| v1: gradient boosting, yearlings only | 26,690 yearlings | 0.406 | ×2.17 | 77% |
| v2: censored model, family effects, market forecast, all ages | 32,758 | 0.465 | ×2.10 | 79% |
| v3: + catalog inputs (hip, consignor, black type, …) | 32,758 | 0.552 | ×1.97 | 80% |
| + retrained every year, bias shift | 32,758 | 0.582 | ×1.84 | 80% |
| + within-sale update | 32,758 | 0.592 | ×1.83 | 80% |
| **Final: + consignor from raw files, foaling date, sire update** | 32,758 | **0.608** | **×1.79** | **80%** |

On the same 26,690 yearlings as v1, the final model scores **R² 0.621**, up from 0.406.

**By age:** weanlings 0.603, yearlings 0.621, 2-year-olds 0.362.
**By test year:** 2021 0.573, 2022 0.611, 2023 0.608, 2024 0.612, 2025 0.617.
**Average miss:** about zero (bias −0.008 on the log scale).

**The 80% range** holds 79.9% of prices overall: cheap sales 82.9%, middle 79.8%, top-priced
sales (Keeneland September, the leading 2-year-old sales) 78.6%.

**In dollars** (all test lots including OBS, grouped by the predicted price):

| Predicted price | Median sold price | Median $ error | Within ±25% | Within ±50% |
|---|---|---|---|---|
| Under $10k | $7,500 | $4,429 | 19% | 38% |
| $10k–30k | $20,000 | $10,379 | 21% | 43% |
| $30k–100k | $55,000 | $29,342 | 23% | 47% |
| $100k–300k | $170,000 | $75,777 | 27% | 55% |
| $300k+ | $400,000 | $168,812 | 32% | 60% |
| **All** | **$42,000** | **$21,778** | **23%** | **47%** |

**What matters most** (the drop in test R² when an input is shuffled, v3): hip position
0.29, sale level and market forecast 0.16, family effects 0.03, consignor 0.03, sire's crop
0.03. Everything else is under 0.02 each.

### How much is pedigree?

`decompose_price.py` refits the model with only some inputs. Placement here means sale,
hip position and consignor; sales companies place horses after inspecting them, so it mixes
pedigree with a first look at the horse. Test years 2021–2025, share of log-price variation:

| | Share |
|---|---|
| Pedigree only (sire, dam, family, stud fee, black type) | 9% |
| Shared by pedigree and placement (incl. age, sex, foaling date) | 33% |
| Placement only | 17% |
| **Unexplained: physical, vet, buyer competition, luck** | **42%** |

- **Pedigree on its own explains about 41%** (R² 0.414); placement on its own 50%; both 58%.
- **Physical, vet and luck account for at least 42%,** and up to about 59% if placement's own
  17% is mostly the inspectors' view of the horse.
- **Within one sale session, pedigree explains 16%** of the differences between horses and
  everything together 28%. Most of what separates neighbouring horses isn't in the catalog.

### Breeze times for 2-year-olds

At 2-year-old sales, horses breeze (gallop a short timed distance) days before the sale.
For OBS sales (2024–2026, about 6,000 timed breezes), a separate model adds the breeze time.
The time is scored against horses that breezed the same distance on the same day.

| OBS 2-year-olds, test 2025–2026 | R² | Typical miss |
|---|---|---|
| Without breeze time | 0.315 | ×1.95 |
| **With breeze time** | **0.575** | **×1.66** |

| Breeze speed (vs same distance, same day) | Fastest 20% | 2nd | 3rd | 4th | Slowest 20% |
|---|---|---|---|---|---|
| Median sold price | $120,000 | $70,000 | $50,000 | $45,000 | $23,000 |

**The value of speed:** on an eighth-mile breeze, each fifth of a second faster is worth
about ×1.5 in price.

**Routing:** timed OBS 2-year-olds go to this model, and the rest go to the main model. On
2025 OBS 2-year-olds, routing lifts R² from 0.327 to 0.567.

## 2. Breeding value

**The method:** linear regression. It beat gradient boosting on both targets, since there
are few rows for this many inputs. Inputs that tried to bring in the price model's knowledge
(v2) didn't improve either target, so v1 is kept.

Because a single foal's price is very noisy, the models are scored on how well they
**rank** mares and stallions as well as on R².

| | Mares (1,659 test foals, 2021–2025) | Stallions (259 sires, 5-fold CV ×10) |
|---|---|---|
| R² | 0.063 | 0.125 |
| Ranking correlation (Spearman) | 0.265 | 0.429 |
| Pairs put in the right order | 59% | 65% |
| Model's top fifth vs bottom fifth | **×2.69** (median foal price ÷ fee: 3.10 vs 1.15) | ×1.26 (median AEI: 1.44 vs 1.14) |

**Mares, the decision rule.** Breed when the predicted foal price beats the stud fee plus
$30k upkeep:

| | Foals | Mean profit per foal | Profitable |
|---|---|---|---|
| Rule says breed | 183 | **$179,385** | **68%** |
| Rule says don't | 1,476 | $12,319 | 30% |

**How to read this:**
- **The mare model's low R² understates it.** It puts mares in a clearly rising order, and
  following its rule separates profitable matings from unprofitable ones.
- **The stallion model is limited by its data.** The AEI list covers only about 259 leading
  sires, so the model never sees sires who failed.

## What was tried and not kept

| Idea | Result |
|---|---|
| A plain market-trend input | Worse than the growth forecast; dropped |
| Session (sale day) as an input | Lowered R² (0.583 vs 0.592); hip position already carries it |
| Breeding-value inputs from the price model (v2) | No gain on either target |
| Three fixes for the Keeneland September session 2–3 overshoot (below) | None beat the saved setup on the years used to choose |

## Limits

- **Single horses are hard to price.** Within one Keeneland session, R² is only 0.06–0.33.
  What separates horses there (conformation, vet reports, how a horse walks) isn't in any
  catalog column.
- **2-year-olds without breeze times are weak** (R² 0.36). Breeze times only exist for OBS,
  for 2024–2026.
- **Keeneland September sessions 2–3 are predicted about 15% too high in 2021–2025.** That
  session's results spill into the next through the updates. Three fixes were tried; none
  passed on the earlier years, so none is used. Retraining each year re-checks the options
  automatically.
- **Top-priced ranges run slightly narrow** (78.6% instead of 80%). Market jumps hit those
  sales hardest.
- **The data:**
  - Sale catalogs (Keeneland, Fasig-Tipton) and IFHA graded-stakes results are the core.
  - The breeding-value models also use race records and the AEI sire list.
  - The data isn't in this repository, since several sources can't be redistributed.
- **Bugs fixed during the work:**
  - Hip numbers were ranked as text.
  - The dam's black-type foals were matched only when the damsire was blank.
  - Both affected v3. The fixed versions are the ones reported.

## Reproducing the results

From `models_final/`, with `pip install -r ../requirements-model.txt` and the data in `../data/`:

```bash
python rolling_price.py --obs --hl 8   # final price model (about 45 minutes)
python rolling_price.py --obs --post-only  # updates, ranges, dollar scores only
python obs_2yo.py                       # breeze-time model
python train_ebv.py                     # breeding-value models (v1, saved)
python ebv_ranking.py                   # ranking scores
python decompose_price.py               # pedigree vs placement shares (about 5 minutes)
python -m pytest tests -q               # no-peeking tests (no data needed)
```

**Predictions for new horses:** `predict.py` has these functions:
- `sale_price_rolling` (main price model);
- `sale_price_2yo_breeze` (OBS 2-year-olds with a breeze time);
- `stallion_aei` and `mare_value` (breeding values).

The saved models are trained through 2025, for 2026 sales. Retrain them after each year's
last sale.
