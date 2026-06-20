# Story 1.3: Estimate-Revisions / Analyst-Sentiment Factor

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a maintainer of the stock-rating model,
I want the rating composite to include an analyst estimate-revisions / sentiment-momentum factor derived from the already-ingested `analyst_consensus_daily` history,
so that the score captures the single most consistently profitable signal used by Zacks and AAII — the direction and magnitude of change in analyst opinion — which the current `v4` model ignores entirely.

## Context

The 2026-06-20 research session (see `M:\ai\sessions\2026-06-20_stock-rating-systems-research.md`) concluded that estimate-revision momentum is the highest-value *new* signal missing from the model. Two analyst providers (Alpha Vantage OVERVIEW, Finnhub) already populate `analyst_consensus_daily`, but that data is **never read back into the rating** — `compute_rating_breakdown` only consumes price, fundamental, and macro features.

**Scope honesty:** the project's free data does not include true forward EPS-estimate revisions. The realistic, achievable signal from existing data is *analyst sentiment momentum*: the trailing change in `suggestion_score`, the change in `analyst_target_price`, and net upgrade/downgrade drift in the recommendation counts. The story implements that and labels it accordingly — it is the project's proxy for an estimate-revisions factor, not a Zacks clone.

## Acceptance Criteria

1. A new feature family is computed per symbol from `analyst_consensus_daily` history and persisted into `features_daily` via the existing `persist_features` path. At minimum: `analyst_suggestion_score_delta` (latest vs. prior snapshot) and `analyst_target_price_change_pct`. A blended `analyst_revision_score` (0–100, `_clamp_decimal` convention) is also produced.
2. The revision feature is computed **per source** and combined deterministically (e.g. average of available sources), so a symbol with both Alpha Vantage and Finnhub data is handled, as is a symbol with only one.
3. `compute_rating_breakdown` incorporates the new factor. The composite weights are rebalanced to include it and still sum to 1.0; the change is reflected in `docs/rating_methodology.md` and the model version is bumped (`v4` → `v5`) in `build_rating` and `build_rating_record`.
4. When a symbol has no analyst history (or only a single snapshot, so no delta is computable), the factor degrades gracefully — the rating is still produced and the factor contributes a neutral value rather than dropping the symbol or throwing.
5. Existing behavior is preserved for symbols already rated: no exceptions in the daily/weekend pipelines, and `model_version` is the only forced change for price-only symbols.
6. Unit tests cover: delta computation across ≥2 snapshots, single-snapshot/no-history neutral fallback, multi-source blending, and the reweighted composite producing a different score when revision signal is present vs. absent.

## Tasks / Subtasks

- [ ] **Task 1: Read analyst history back (AC: #1, #2, #4)**
  - [ ] Add a loader in `src/stock_rating/repository/analyst.py` that returns the last N dated snapshots per source for a symbol (extend beyond the existing `load_latest_analyst_consensus`, which returns only one row). Query `analyst_consensus_daily` ordered by `date desc`, grouped/partitioned by `source`.
  - [ ] Keep the defensive `try/except → return default` style used throughout this repo (see existing functions returning `None`/`{}` on failure).
- [ ] **Task 2: Compute the revision features (AC: #1, #2, #4)**
  - [ ] Add `compute_analyst_revision_features(symbol, as_of_date, snapshots_by_source) -> list[FeatureValue]` in a new module `src/stock_rating/transform/analyst_features.py` (mirror the shape of `transform/features.py` and `transform/fundamentals.py`).
  - [ ] `analyst_suggestion_score_delta` = latest `suggestion_score` − prior snapshot's `suggestion_score` (per source, then averaged). Neutral = 0 when <2 snapshots.
  - [ ] `analyst_target_price_change_pct` = (latest − prior) / prior `analyst_target_price`, guarding divide-by-zero/None.
  - [ ] `analyst_revision_score` = `_clamp_decimal(Decimal("50") + suggestion_delta * W1 + target_change_pct * W2)` — pick W1/W2 in the spirit of the existing magic constants in `model_v1.py` and document them.
- [ ] **Task 3: Wire into feature assembly (AC: #1, #4, #5)**
  - [ ] In `build_symbol_features` (`src/stock_rating/pipeline/daily.py:413`), inject a `load_analyst_history_fn` + `compute_analyst_revision_features_fn` (defaulted, same DI pattern as fundamentals/macro) and append their output to the returned list at line 437.
  - [ ] Thread the new default params through the public pipeline entrypoints that already pass `compute_fundamental_features_fn` (the `refresh_*` functions around lines 797, 912, 1018, 1114, 1209).
- [ ] **Task 4: Add the factor to the score (AC: #3, #5)**
  - [ ] In `compute_rating_breakdown` (`src/stock_rating/rating/model_v1.py:38`), read `analyst_revision_score` / the deltas from `feature_map`, fold into a factor, and rebalance the final-score weights (currently 0.25/0.25/0.20/0.20/0.10 at lines 144–149) so they include the new factor and sum to 1.0.
  - [ ] Add the factor to `RatingBreakdown` and `RatingRecord` if it should be persisted as its own column (check `repository/ratings.py` + `sql/` for a migration — add one under `sql/migrations/` following the `003_*` pattern if a new column is needed).
  - [ ] Bump `model_version` to `v5` in `build_rating` (line 34) and `build_rating_record` (line 181).
- [ ] **Task 5: Docs + tests (AC: #3, #6)**
  - [ ] Update `docs/rating_methodology.md` (new weights, new feature family, model version) and `docs/data_sources.md` if relevant.
  - [ ] Add `tests/test_analyst_features.py`; extend `tests/test_analyst_ingest.py` only if the loader lives near ingestion. Follow existing pure-function test style (no DB; pass fixture snapshot lists directly).

## Dev Notes

- **The data already exists; nothing new needs fetching.** `analyst_consensus_daily` (schema: `sql/migrations/003_add_analyst_consensus_daily.sql`) has PK `(symbol, date, source)`, so dated snapshots accumulate over time per source. Columns: `analyst_target_price`, `strong_buy_count`, `buy_count`, `hold_count`, `sell_count`, `strong_sell_count`, `suggestion_label`, `suggestion_score`, `source`.
- **`suggestion_score` is already a signed scalar** computed by `derive_analyst_suggestion` (`src/stock_rating/ingest/analyst.py:155`): `(strongBuy*2 + buy − sell − strongSell*2) / total`, range roughly [−2, +2]. Its *change over time* is the cleanest revision proxy.
- **Bonus latent signal:** Finnhub's `recommendation` endpoint returns a *list* of monthly periods, but `parse_finnhub_analyst_consensus` (`analyst.py:352`) currently keeps only `recommendation_payload[0]`. A follow-up could persist prior periods to get revision history from a single API call — out of scope here, but note it in Dev Agent Record if you touch that parser.
- **Rating is built from a flat `FeatureValue` list.** `compute_rating_breakdown` reads features by name out of `feature_map` (`model_v1.py:39`). Adding a feature = appending `FeatureValue`s in `build_symbol_features` and reading them by name in the breakdown. No schema change required for `features_daily` (it's name/value rows).
- **DI everywhere.** Every pipeline function takes injectable `*_fn` defaults for testability. Match that pattern — do not call repositories directly inside the new transform.
- **Decimal + clamp conventions:** use `Decimal`, and `_clamp_decimal` (0–100 bound) for any sub-score, consistent with `model_v1.py:187`.

### Project Structure Notes

- New transform module sits beside existing ones: `src/stock_rating/transform/{features,fundamentals,technicals,macro}.py` → add `analyst_features.py`.
- New repository loader extends `src/stock_rating/repository/analyst.py` (already has `load_latest_analyst_consensus`, `load_latest_analyst_dates_for_source`).
- Migrations live in `sql/migrations/` (latest is `003_*`); add `004_*` only if persisting the factor as a rating column.

### References

- [Source: docs/rating_methodology.md] — current v4 weights (25/25/20/20/10) and feature families.
- [Source: docs/data_sources.md#Analyst-consensus-providers] — two-provider model, per-source freshness tiers.
- [Source: src/stock_rating/rating/model_v1.py#compute_rating_breakdown] — composite assembly and final weights (lines 141–151).
- [Source: src/stock_rating/ingest/analyst.py#derive_analyst_suggestion] — `suggestion_score` definition (lines 155–183).
- [Source: src/stock_rating/repository/analyst.py#load_latest_analyst_consensus] — existing single-row loader to extend (lines 23–80).
- [Source: src/stock_rating/pipeline/daily.py#build_symbol_features] — feature merge point (lines 413–437).
- [Source: sql/migrations/003_add_analyst_consensus_daily.sql] — table shape.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8[1m]

### Debug Log References

- `./.venv/Scripts/python.exe -m pytest -q` → 158 passed (151 prior + 7 new).

### Completion Notes List

- Implemented as a **true sixth composite factor** (per AC #3) rather than folding into an existing one. Composite weights rebalanced from 25/25/20/20/10 to **22.5/22.5/18/18/9 + 10% analyst_revision** (sum 1.0), updated in the single-source `COMPOSITE_WEIGHTS`.
- **`model_version` bumped to `v6`** — the composite formula changed (new factor + new weights), so v6 ratings differ from v5; this is not purely additive. (Stories 1.1/1.2 were v5; 1.3 is the first to alter the composite.)
- Factor is the project's **proxy** for estimate-revision momentum (free data has no forward EPS estimates): trailing change in `suggestion_score` + mean target price across the two latest `analyst_consensus_daily` snapshots, averaged across providers. `analyst_revision_score = clamp(50 + suggestion_delta*15 + target_change_pct*100)`.
- **Graceful degradation (AC #4):** no analyst history → transform emits nothing → `compute_rating_breakdown` defaults the factor to a neutral 50. The sub-score column is therefore always populated, so the percentile pass never drops a symbol.
- Ripple handled end-to-end: `FactorScores`, `FACTORS`, `RatingBreakdown`, `RatingRecord` + `persist_ratings`, `LatestFactorScore` + loader, `PercentileGradeUpdate` + persister, `universe_grading`, schema + migration `006`.
- **Bonus signal noted (not done):** Finnhub's `recommendation` endpoint returns a list of monthly periods but `parse_finnhub_analyst_consensus` keeps only `[0]`; persisting prior periods would give revision history from a single API call — left as a follow-up (parser untouched).

### File List

- `src/stock_rating/transform/analyst_features.py` (new) — revision feature transform
- `src/stock_rating/repository/analyst.py` — `load_recent_analyst_consensus_by_source`
- `src/stock_rating/rating/percentile_ranking.py` — 6th factor in weights / FACTORS / FactorScores / composite
- `src/stock_rating/rating/model_v1.py` — factor in `compute_rating_breakdown` + `RatingBreakdown`; `MODEL_VERSION=v6`
- `src/stock_rating/rating/universe_grading.py` — 6th factor through the percentile pass
- `src/stock_rating/repository/ratings.py` — `RatingRecord`/`LatestFactorScore`/`PercentileGradeUpdate` + persist/load
- `src/stock_rating/pipeline/daily.py` — analyst features in `build_symbol_features`
- `sql/migrations/006_add_analyst_revision_factor.sql` (new); `sql/schema.sql`
- `docs/rating_methodology.md`, `src/stock_rating/pipeline/report.py` (methodology page), `CHANGELOG.md`
- `tests/test_analyst_features.py` (new); updated `tests/test_ratings.py`, `tests/test_percentile_ranking.py`, `tests/test_universe_grading.py` for the 6th factor
