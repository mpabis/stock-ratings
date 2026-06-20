# Story 1.1: AAII-Style Percentile Ranking of Factors

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a maintainer of the stock-rating model,
I want each factor and the final grade to be assigned by **percentile rank against the whole tracked universe** instead of fixed absolute score thresholds,
so that grades are robust to outliers and regime drift and follow the documented, transparent AAII A+ approach (even 20% A–F buckets) rather than the brittle hand-tuned bands in `map_score_to_label`.

## Context

From the 2026-06-20 research session (`M:\ai\sessions\2026-06-20_stock-rating-systems-research.md`): every credible transparent system (AAII, IBD, Zacks) grades on **cross-sectional percentile ranks**, not fixed cutoffs. The current model maps a per-symbol composite into fixed bands at 90/75/55/35 (`src/stock_rating/rating/scoring.py:map_score_to_label`), which means a grade depends on the absolute magic constants in `compute_rating_breakdown` rather than on how a stock compares to its peers.

**Central architectural change:** today the rating is computed and persisted **one symbol at a time** inline in the pipeline (`build_rating_record(task, features)` → `persist_ratings`). Percentile ranking is inherently **cross-sectional** — it needs every symbol's sub-scores for a given date before any grade can be assigned. This story introduces a second, universe-wide ranking pass.

The factor sub-scores already exist and are already persisted per symbol: `valuation_score`, `quality_score`, `growth_score`, `momentum_score`, `risk_score` columns on `ratings_daily` (see `RatingRecord`, `repository/ratings.py:9`). That makes a clean two-pass design possible without re-deriving features.

## Acceptance Criteria

1. A new universe-ranking step computes, for the latest rating date, the **percentile rank of each factor sub-score** (valuation/quality/growth/momentum/risk) across all active symbols, and a percentile rank of the weighted composite.
2. Final grade is assigned from the **composite percentile** in even 20% buckets: top 20% → A, next 20% → B, … bottom 20% → F (AAII convention), replacing the fixed 90/75/55/35 bands.
3. Each factor also gets an A–F letter grade from its own percentile (so the explanation can show "Value: A, Quality: C, …" like AAII).
4. Ties and small universes are handled deterministically (define percentile method, e.g. `rank / n`; document it). A universe of size 1 must not crash and should yield a defined grade.
5. The per-symbol pass still runs and persists raw sub-scores; the ranking pass is idempotent and re-runnable for a date without corrupting data.
6. `model_version` is bumped (current `v4`) and `docs/rating_methodology.md` is rewritten to describe percentile grading, the bucket boundaries, and that grades are relative to the tracked universe (not absolute quality).
7. Tests cover: percentile assignment across a fixture universe, even-bucket boundary behavior, single-symbol and tie edge cases, and that a stock's grade changes when the universe changes even though its raw scores do not.

## Tasks / Subtasks

- [ ] **Task 1: Decide & document the ranking pass shape (AC: #1, #5)**
  - [ ] Add a ranking module, e.g. `src/stock_rating/rating/percentile_ranking.py`, with a pure function `assign_percentile_grades(rows) -> list[GradedRating]` taking the universe's factor sub-scores for one date and returning per-factor percentiles + composite percentile + letter grades.
  - [ ] Keep it pure/DI-friendly (no DB inside), matching the repo's testing style (`tests/` call pure functions with fixtures).
- [ ] **Task 2: Universe read + write (AC: #1, #5)**
  - [ ] Add a repository loader in `src/stock_rating/repository/ratings.py` to read all active symbols' latest-date sub-scores (join `ratings_daily` to `symbols.active = true`, mirroring `load_rating_repair_states` at line 34).
  - [ ] Persist the assigned percentile grades. Decide: extend `ratings_daily` with `*_percentile` + `*_grade` columns (new migration `sql/migrations/004_*` following the `003_*` pattern) **or** store them in `explanation_json`. Prefer real columns for the factor percentiles + final grade; document the choice.
- [ ] **Task 3: Replace the grade mapping (AC: #2, #3)**
  - [ ] `map_score_to_label` (`src/stock_rating/rating/scoring.py:10`) becomes percentile-driven, or is superseded by the new grader. Keep the A–F label vocabulary ("A / Very Attractive" … "F / Very Unattractive") so the API/report layer is unaffected.
  - [ ] Surface per-factor letter grades in `build_rating_explanation` output.
- [ ] **Task 4: Wire the second pass into the pipeline (AC: #4, #5)**
  - [ ] After the per-symbol loop in the daily/weekend refresh (`src/stock_rating/pipeline/daily.py` — the refresh functions around lines 797, 912, 1018, 1114, 1209 that call `build_rating_record_fn` then `persist_ratings_fn`), run the universe-ranking pass once for the run's date and persist grades.
  - [ ] Confirm partial-universe refreshes still produce a coherent ranking (the data-sources doc notes refreshes can span multiple runs — decide whether ranking runs over the full active universe's latest available scores, not just symbols touched this run; recommend the former).
- [ ] **Task 5: Docs + tests (AC: #6, #7)**
  - [ ] Rewrite `docs/rating_methodology.md` (percentile grading, buckets, relative-not-absolute caveat, model version bump).
  - [ ] Add `tests/test_percentile_ranking.py`. Extend API/report tests if grade fields change shape.

## Dev Notes

- **Two-pass is the crux.** Pass 1 (existing) = per-symbol raw factor sub-scores. Pass 2 (new) = cross-sectional percentile → grade. Do not try to compute percentiles inside the per-symbol path; you don't have the universe there.
- **Sub-scores already persisted** — `valuation_score`/`quality_score`/`growth_score`/`momentum_score`/`risk_score` are columns on `ratings_daily` (`RatingRecord`, `repository/ratings.py:9–23`; insert at lines 97–123). Pass 2 can read them straight back; no feature recomputation.
- **Risk score direction:** higher `risk_score` currently means *safer* (it's a 0–100 where high is good, see `model_v1.py:124–133`). Make sure the percentile rank treats it consistently (high = better) so the composite direction is preserved.
- **Relative grading changes semantics** — under percentiles, exactly ~20% of the universe is always an "A". Call this out in `docs/rating_methodology.md`; it's a behavior change from "absolute quality" grades and will surprise anyone comparing to old `v4` labels.
- **Idempotency:** the upsert key on `ratings_daily` is `(symbol, date, model_version)` (`repository/ratings.py:112`). The ranking pass must upsert grades under the new `model_version` so re-runs overwrite rather than duplicate.
- **Composite weights** (currently 0.25/0.25/0.20/0.20/0.10, `model_v1.py:144–149`) can stay as the weighting *before* percentiling the composite — or you can percentile each factor first then weight the percentiles. Pick one and document it; weighting raw sub-scores then percentiling the composite is the smaller change.

### Project Structure Notes

- New module beside existing rating code: `src/stock_rating/rating/{scoring,model_v1,explanations}.py` → add `percentile_ranking.py`.
- Coordinate the `model_version` bump with stories 1.2 and 1.3 (all three touch versioning + `docs/rating_methodology.md`) to avoid collisions — agree on the next version string before merging.

### References

- [Source: docs/rating_methodology.md] — current fixed-band mapping (90/75/55/35) and weights.
- [Source: src/stock_rating/rating/scoring.py#map_score_to_label] — the bands to replace (lines 10–24).
- [Source: src/stock_rating/rating/model_v1.py#compute_rating_breakdown] — sub-score production + final weights (lines 38–160).
- [Source: src/stock_rating/repository/ratings.py] — `RatingRecord` columns (9–23), `load_rating_repair_states` universe-read pattern (34–83), upsert key (112).
- [Source: src/stock_rating/pipeline/daily.py] — per-symbol rating loop / persist sites (≈ lines 842–846 and the parallel refresh functions).

## Dev Agent Record

### Agent Model Used

claude-opus-4-8[1m]

### Debug Log References

- `pytest -q` → 136 passed (includes new percentile + universe-grading suites). Run with the project `.venv` (`./.venv/Scripts/python.exe -m pytest`).

### Completion Notes List

- Implemented the two-pass design from the story: per-symbol pass unchanged (still emits absolute sub-scores + provisional `map_score_to_label` label), new cross-sectional pass assigns universe-relative percentiles + A-F grades and overwrites `rating_label`/`rating_score` on the same `v5` row.
- **Decisions settled** (were flagged as cross-story collisions): `model_version` = `v5` via new `MODEL_VERSION` constant in `model_v1.py`; migration number = `005` (004 was already taken by `004_add_fundamental_period_metadata.sql`). Composite = weight-then-percentile (weights unchanged 0.25/0.25/0.20/0.20/0.10). Percentile = mid-rank `(below + 0.5·equal)/n`; singleton → 0.5 → C.
- `rating_score` is now the composite percentile rescaled to 0-100, so `report.fetch_latest_ratings`' `order by rating_score desc` still ranks the universe correctly. The report picks the latest-`created_at` row per symbol (no `model_version` filter), so the pass-2 update to the v5 row wins over legacy v4 rows automatically.
- Per-factor percentiles + grades are persisted as new `ratings_daily` columns (`*_percentile`, `*_grade`). Surfacing them in the HTML report / explanation_json is a small follow-up (data layer is done, satisfying AC #3's "explanation *can* show" capability).
- Risk-direction confirmed: high `risk_score` = safer in `model_v1`, so all five factors share "higher is better" — percentile direction is uniform.
- **Code-review fixes applied** (high-effort review, 8 angles): (1) updated the in-report methodology page (`report.py`) — lead, Rating Scale table, and Final Composite Score section — for v5 percentile grading; (2) added the `2026-06-20` CHANGELOG entry (AGENTS.md §"Record … changes in CHANGELOG.md"); (3) deduped `COMPOSITE_WEIGHTS` to a single source in `percentile_ranking.py`, imported by `model_v1`; (4) switched `rating_score` rescale to Decimal `ROUND_HALF_UP` (no float round-trip). Not done: integration test for the `run_pipeline` pass-2 path (noted as low-priority follow-up); the generated `artifacts/reports/ratings-methodology.html` is stale until the next report run regenerates it from the fixed `report.py`.

### File List

- `src/stock_rating/rating/percentile_ranking.py` (new) — pure percentile/grade logic
- `src/stock_rating/rating/universe_grading.py` (new) — pass-2 orchestration (pure `build_percentile_updates` + `apply_universe_percentile_grades`)
- `src/stock_rating/rating/scoring.py` — added `GRADE_LABELS` + `label_for_grade`
- `src/stock_rating/rating/model_v1.py` — `MODEL_VERSION="v5"` constant; bumped both write sites
- `src/stock_rating/repository/ratings.py` — `LatestFactorScore`, `PercentileGradeUpdate`, `load_latest_factor_scores`, `persist_percentile_grades`
- `src/stock_rating/pipeline/daily.py` — wired pass-2 into `run_pipeline` after rating writes
- `sql/migrations/005_add_rating_percentile_grades.sql` (new); `sql/schema.sql` — new columns
- `docs/rating_methodology.md` — rewritten for v5 percentile grading
- `tests/test_percentile_ranking.py` (new), `tests/test_universe_grading.py` (new), `tests/test_ratings.py` (v4→v5 assertion)
