# Story 1.2: Piotroski F-Score, Magic Formula & Acquirer's Multiple

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a maintainer of the stock-rating model,
I want fully-specified, performance-published value/quality scores — Piotroski F-Score, Greenblatt Magic Formula, and the Acquirer's Multiple — computed alongside the composite,
so that the project has transparent, externally-validated benchmark ratings (with documented historical returns) to sanity-check the homegrown composite against, using the existing SEC fundamentals pipeline.

## Context

From the 2026-06-20 research session (`M:\ai\sessions\2026-06-20_stock-rating-systems-research.md`): these three are the Tier-1 "documented + replicable + published returns" systems and map cleanly onto the existing SEC data path. They are exact formulas, not black boxes — ideal as standalone benchmark scores rather than being blended into the composite (keep them separate so they remain interpretable and comparable to published backtests).

The fundamentals pipeline already exists: SEC concepts → `FundamentalFact` (`ingest/sec_companyfacts.py`) → `compute_fundamental_features` (`transform/fundamentals.py`) → `FeatureValue`s consumed by the rating. The gap is that the **specific GAAP line items these formulas need are not yet mapped**, and there's no module that assembles the three scores.

**Known limitation (document it):** Piotroski's F-Score was designed as a *second-stage filter on already-cheap stocks*, and Magic/Acquirer's are deep-value tilts that have had weak multi-year stretches. These are benchmark/diagnostic scores, not standalone buy signals — note this in the methodology doc.

## Acceptance Criteria

1. SEC ingestion is extended to pull the additional GAAP concepts these formulas require (see Dev Notes for the exact list), mapped in `CORE_SEC_METRIC_CONCEPTS` (`ingest/sec_companyfacts.py:16`) with sensible fallback concept tags, matching the existing tuple-of-fallbacks pattern.
2. **Piotroski F-Score (0–9)** is computed from the 9 standard signals, using year-over-year comparison where required (reuse `_year_over_year_growth` style logic over `FundamentalFact` history). Missing inputs degrade gracefully (a signal that can't be evaluated scores 0, and the score notes how many of 9 were computable).
3. **Magic Formula** rank inputs are computed per symbol: Return on Capital = EBIT / (net working capital + net fixed assets) and Earnings Yield = EBIT / Enterprise Value. (The cross-sectional *rank* combination can piggyback on the universe pass from story 1.1, or be computed in a dedicated step — see Tasks.)
4. **Acquirer's Multiple** = Enterprise Value / EBIT is computed per symbol.
5. All three are persisted (as `features_daily` rows and/or dedicated columns) and exposed in the rating explanation, clearly labeled and **kept separate from the composite score** (they do not change the 0.25/0.25/0.20/0.20/0.10 weighting).
6. Financials/utilities exclusions and the min-market-cap filter that Greenblatt specifies are applied to the Magic Formula ranking (or explicitly deferred with a logged note if sector data isn't available yet).
7. `docs/rating_methodology.md` and `docs/data_sources.md` are updated (new SEC fields, the three scores, the "benchmark not buy-signal" caveat). Tests cover each formula with fixture facts, including missing-data fallbacks.

## Tasks / Subtasks

- [ ] **Task 1: Extend SEC concept mapping (AC: #1)**
  - [ ] Add to `CORE_SEC_METRIC_CONCEPTS` (`ingest/sec_companyfacts.py:16`) and `CORE_FUNDAMENTAL_METRICS` (`transform/fundamentals.py:8`):
    - `operating_income` → `("OperatingIncomeLoss",)` (EBIT proxy)
    - `ppe_net` → `("PropertyPlantAndEquipmentNet",)`
    - `current_assets` → `("AssetsCurrent",)`
    - `current_liabilities` → `("LiabilitiesCurrent",)`
    - `cash` → `("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents")`
    - `long_term_debt` → `("LongTermDebtNoncurrent", "LongTermDebt")` and `long_term_debt_current` → `("LongTermDebtCurrent",)`
    - `gross_profit` → `("GrossProfit",)` with fallback derivation from `revenue − CostOfGoodsAndServicesSold`/`CostOfRevenue`
  - [ ] Verify `parse_company_facts` (`sec_companyfacts.py:103`) picks these up automatically once mapped (it iterates `CORE_SEC_METRIC_CONCEPTS`), and the persist upsert key still holds.
- [ ] **Task 2: Compute the three scores (AC: #2, #3, #4)**
  - [ ] New module `src/stock_rating/transform/benchmark_scores.py`:
    - `compute_piotroski_fscore(symbol, as_of, facts) -> FeatureValue | (score, signals_computable)`. 9 signals: ROA>0; CFO>0; ΔROA>0; CFO>NetIncome (accruals); ΔLeverage<0 (long-term debt/assets); ΔCurrentRatio>0; no new shares issued (Δshares≤0); ΔGrossMargin>0; ΔAssetTurnover>0.
    - `compute_magic_formula_inputs(...)` → ROIC and earnings yield (EBIT/EV).
    - `compute_acquirers_multiple(...)` → EV/EBIT.
  - [ ] EV = market cap + total debt − cash, where market cap = `latest_price × shares_diluted`; total debt = `long_term_debt + long_term_debt_current`. Guard None/zero throughout (reuse the `not in {None, Decimal("0")}` guard style from `fundamentals.py`).
- [ ] **Task 3: Wire into feature assembly (AC: #5)**
  - [ ] Append the new `FeatureValue`s in `build_symbol_features` (`pipeline/daily.py:413–437`) next to `compute_fundamental_features`, via an injected `compute_benchmark_scores_fn` default (same DI pattern as the other transforms).
  - [ ] Add the scores to the rating explanation (`rating/explanations.py`) without altering the composite score.
- [ ] **Task 4: Magic Formula ranking + exclusions (AC: #3, #6)**
  - [ ] Magic Formula is a *combined rank* (rank by ROIC + rank by EY, sum). Compute the cross-sectional ranks in a universe pass — coordinate with story 1.1's percentile pass (reuse its universe loader) rather than building a second cross-sectional scan.
  - [ ] Apply Greenblatt's exclusions (financials, utilities, min market cap). If sector classification isn't in `symbols` yet, log the gap and apply only the market-cap filter for now (note in completion).
- [ ] **Task 5: Persistence, docs, tests (AC: #5, #7)**
  - [ ] Persist scores — `features_daily` rows are sufficient for F-Score / EV-EBIT; if Magic Formula *rank* should be stored, add columns via `sql/migrations/004_*` (coordinate the migration number with story 1.1).
  - [ ] Update `docs/rating_methodology.md` + `docs/data_sources.md`.
  - [ ] Add `tests/test_benchmark_scores.py` covering each formula and missing-data paths; extend `tests/` for the new SEC concepts if ingestion parsing changes.

## Dev Notes

- **Currently mapped SEC concepts** (`sec_companyfacts.py:16–23`): revenue, net_income, operating_cash_flow, assets, liabilities, stockholders_equity, eps_diluted, shares_diluted. Everything else the formulas need must be added (Task 1).
- **Reused inputs already available:** ROA = net_income/assets (`fundamentals.py:79`), CFO = operating_cash_flow, accruals test = CFO vs net_income, asset turnover = revenue/assets — so several Piotroski signals need no new fields, only the new ones (gross profit, current ratio, long-term debt) do.
- **YoY comparisons:** `FundamentalFact` history is already sorted newest-first inside `compute_fundamental_features` (`fundamentals.py:31–39`); the existing `_year_over_year_growth` (`fundamentals.py:149`) shows the latest-vs-previous pattern to reuse for ΔROA, ΔleverageΔ, Δcurrent-ratio, Δgross-margin, Δasset-turnover, Δshares.
- **EBIT proxy:** `OperatingIncomeLoss` is the pragmatic free-data stand-in for EBIT; note the approximation in the methodology doc (true EBIT may differ by non-operating items).
- **Keep them OUT of the composite.** AC #5 is deliberate — these are interpretable benchmarks with published backtests; blending them in would destroy that comparability. Surface them next to the composite, not inside it.
- **Sector data caveat:** Greenblatt excludes financials/utilities. If `symbols` lacks a sector/industry column, that exclusion can't be applied precisely yet — flag it rather than silently skipping (the data-sources doc already notes US/SEC equities are the high-confidence path).

### Project Structure Notes

- New transform: `src/stock_rating/transform/benchmark_scores.py` beside `fundamentals.py`.
- SEC concept additions are config-like edits to the existing `CORE_SEC_METRIC_CONCEPTS` dict — low risk, picked up automatically by `parse_company_facts`.
- Migration numbering and `model_version` bump must be coordinated with stories 1.1 and 1.3.

### References

- [Source: src/stock_rating/ingest/sec_companyfacts.py] — `CORE_SEC_METRIC_CONCEPTS` (16–23), `parse_company_facts` (103), persist upsert (159).
- [Source: src/stock_rating/transform/fundamentals.py] — `CORE_FUNDAMENTAL_METRICS` (8–17), metric extraction (41–54), guards (57–127), `_year_over_year_growth` (149–157).
- [Source: src/stock_rating/pipeline/daily.py#build_symbol_features] — feature merge point (413–437).
- [Source: docs/data_sources.md] — SEC EDGAR as official fundamentals source; US/SEC-covered equities as the high-confidence path.
- External: Greenblatt *The Little Book That Beats the Market* (Magic Formula); Piotroski (2000) 9-signal F-Score; Carlisle *The Acquirer's Multiple* (EV/EBIT). See session summary for cited backtest figures.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
