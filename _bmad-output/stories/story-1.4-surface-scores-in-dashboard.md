# Story 1.4: Surface Analyst-Revision, Benchmark Scores & Factor Grades in the Dashboard

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user reading the ratings dashboard,
I want the analyst-revision factor, the Piotroski/Magic Formula/Acquirer's Multiple benchmark scores, and the per-factor A-F letter grades to be visible per symbol,
so that the outputs of stories 1.1–1.3 are actually inspectable in the HTML report instead of only in the database — the consistently-deferred follow-up from all three code reviews.

## Context

Stories 1.1–1.3 persist their outputs but the dashboard never renders them:

- **1.1** stores per-factor percentiles + A-F grades (`*_percentile`, `*_grade` on `ratings_daily`) — the report shows only the numeric sub-scores, not the grades or the relative percentile.
- **1.2** writes benchmark scores to `features_daily` (`piotroski_fscore`, `magic_formula_roic`, `magic_formula_earnings_yield`, `magic_formula_combined_rank`, `acquirers_multiple`) — the report never joins `features_daily`, so these are invisible.
- **1.3** added a sixth composite factor `analyst_revision_score` (+ percentile/grade) to `ratings_daily` — but `render_rating_row` still renders only five factor cells (valuation/quality/growth/momentum/risk).

The dashboard is generated entirely by `src/stock_rating/pipeline/report.py` (run via `python -m stock_rating.pipeline.report`, output `artifacts/reports/ratings-dashboard.html`). This story is presentation-only — no scoring/model changes.

## Acceptance Criteria

1. The ratings table shows an **Analyst Revision** factor column, rendered the same way as the other five factor cells, fed by `ratings_daily.analyst_revision_score`. The table header and the JS column sort indices (`data-sort-index`) are updated consistently so sorting still targets the correct columns.
2. Each factor cell (all six) optionally surfaces its **A-F letter grade** alongside the numeric sub-score, fed by the `*_grade` columns. (Decide: inline badge on the existing cell vs. a tooltip — keep it compact; the table is already wide.)
3. The benchmark scores are surfaced per symbol: at minimum **Piotroski F-Score** (with `signals_available` as context), **Magic Formula combined rank**, and **Acquirer's Multiple**. Sourced by joining `features_daily` (latest value per `feature_name` per symbol) in `fetch_latest_ratings`.
4. `RatingSnapshot` is extended with the new fields; all of them are **optional/nullable** and render a graceful placeholder (e.g. `—`) when absent, so symbols without analyst history (neutral path), without SEC fundamentals (no benchmark scores), or excluded from the Magic Formula rank (financials/utilities) still render without error.
5. Both `fetch_latest_ratings` SQL variants (the analyst-join version and the fallback version in the `except` block) are updated consistently, and the methodology page (`render_methodology_html`) gains a short note that benchmark scores are diagnostic and excluded from the composite.
6. The dashboard still renders with an empty universe and with a fresh DB where the new columns/features are all null (no exceptions, no broken layout).
7. Tests cover the new rendering helpers and the snapshot mapping (the report has pure render functions — `render_rating_row`, `render_factor_cell` — testable without a DB), including the null/placeholder paths.

## Tasks / Subtasks

- [ ] **Task 1: Extend the data carrier (AC: #1, #3, #4)**
  - [ ] Add to `RatingSnapshot` (`report.py:28`): `analyst_revision_score`, the six `*_grade` strings (optional), and benchmark fields (`piotroski_fscore`, `piotroski_signals_available`, `magic_formula_combined_rank`, `acquirers_multiple` — all `Optional`).
- [ ] **Task 2: Source the data (AC: #3, #4, #5)**
  - [ ] In `fetch_latest_ratings` (`report.py:225`), add `analyst_revision_score` + the `*_grade` columns to the `ranked_ratings` selects (both the primary query and the `except`-block fallback).
  - [ ] Add a `latest_features` CTE (or follow the existing `latest_analyst` / `latest_prices` `distinct on (symbol)` pattern) that pivots the needed `features_daily` rows (`piotroski_fscore`, `piotroski_signals_available`, `magic_formula_combined_rank`, `acquirers_multiple`) and left-joins them. Left join so missing features → null.
  - [ ] Map the new columns into `RatingSnapshot` in the row loop (`report.py:~358`).
- [ ] **Task 3: Render it (AC: #1, #2, #4)**
  - [ ] Add the Analyst Revision factor cell in `render_rating_row` (`report.py:1310`) and a matching `<th>` in the table header (`report.py:1112`); **re-index** the `data-sort-index` values for Analyst/Target (currently 8/9 → shift) and any JS that depends on them.
  - [ ] Extend `render_factor_cell` (`report.py:1572`) to optionally show the letter grade, or add a small helper for the grade badge.
  - [ ] Add a compact benchmark display — either extra columns or a secondary detail row/section — for F-Score, Magic Formula rank, Acquirer's Multiple, with `—` placeholders when null.
- [ ] **Task 4: Methodology note + docs (AC: #5)**
  - [ ] Add a one-line note to `render_methodology_html` that benchmark scores are diagnostic benchmarks shown alongside but excluded from the composite (mirrors `docs/rating_methodology.md`).
- [ ] **Task 5: Tests (AC: #6, #7)**
  - [ ] Add/extend `tests/test_report.py`: render a `RatingSnapshot` with all new fields populated and one with them all null; assert the Analyst Revision cell, grade badges, and benchmark values render (and that nulls become placeholders without throwing).

## Dev Notes

- **Presentation only** — do not touch `compute_rating_breakdown`, the weights, or `model_version`. This story reads already-persisted v6 data.
- **Column re-indexing is the sharp edge.** The header buttons carry `data-sort-index="0..9"` (`report.py:1114-1123`) and the JS table sorter keys off them. Adding an Analyst Revision column between Risk (index 7) and Analyst (index 8) shifts everything after it — update header indices, the `<td>` order in `render_rating_row`, and confirm the sorter still works.
- **Benchmark scores are in `features_daily`, not `ratings_daily`** — they need a join, unlike the analyst-revision factor which is a `ratings_daily` column. `magic_formula_combined_rank` is only present for symbols that passed the sector/market-cap filter, so expect nulls.
- **Two SQL variants exist.** `fetch_latest_ratings` has a primary query (with the analyst join) and a fallback in the `except` block (no analyst join). Both must gain the new columns or the fallback will `KeyError`/index-mismatch on the row mapping.
- **Nullability everywhere.** Pre-v6 rows, fresh DBs, and uncovered symbols all produce nulls — every new field must render a placeholder, matching the existing `render_factor_cell` None handling.
- **F-Score interpretation:** show `piotroski_fscore`/9 and consider dimming when `piotroski_signals_available < 9` (low confidence), consistent with the story 1.2 design.

### Project Structure Notes

- All changes are within `src/stock_rating/pipeline/report.py` and `tests/test_report.py`. No schema, migration, or model changes.
- The generated `artifacts/reports/ratings-dashboard.html` and `ratings-methodology.html` are build outputs — they refresh on the next `python -m stock_rating.pipeline.report` run; do not hand-edit them.

### References

- [Source: src/stock_rating/pipeline/report.py#RatingSnapshot] — data carrier (lines 28-49).
- [Source: src/stock_rating/pipeline/report.py#fetch_latest_ratings] — both SQL variants + row mapping (lines 225-372).
- [Source: src/stock_rating/pipeline/report.py#render_rating_row] — row + factor cells (lines 1285-1330); header (1112-1125); `render_factor_cell` (1572).
- [Source: docs/rating_methodology.md] — v6 factors, weights, and benchmark-score definitions to mirror in the UI copy.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8[1m]

### Debug Log References

- `./.venv/Scripts/python.exe -m pytest -q` → 163 passed (159 prior + 4 new). `import stock_rating.pipeline.report` clean (f-string brace check).

### Completion Notes List

- **Presentation-only**, as scoped — no model/scoring/schema changes.
- **SQL columns appended at the end** (indices 20-30) of both `fetch_latest_ratings` variants rather than inserted, so the existing `row[6..19]` mapping is untouched. The fallback (`except`) variant selects the same 11 columns as `null` to keep row width uniform on pre-v6 schemas.
- Benchmark scores live in `features_daily`, so the primary query gains a `latest_feature_rows` (distinct-on per symbol+feature) + `latest_features` (filtered-aggregate pivot) CTE pair, left-joined.
- Dashboard table grew 10→14 columns: added an **Analyst Rev** factor cell (6th factor) and three benchmark columns (F-Score N/9, Magic rank, EV/EBIT). Header `data-sort-index` re-numbered (Rev=8, Analyst→9, Target→10, benchmarks 11-13); the JS sorter keys off these indices and stays aligned. `render_factor_cell` extended with an optional A-F grade badge; new `render_benchmark_cell` / `render_fscore_cell` helpers (F-Score dimmed when `signals_available < 9`).
- All new fields optional → render `—` placeholders when null (no analyst history / no SEC fundamentals / sector-excluded). Verified mobile view is safe (stacked `display:block`, no position-based `::before` labels).
- **Docs (per user request):** rewrote the stub `docs/architecture.md` to describe the layered design + two-pass v6 rating system; added a "Where to see results" section to `docs/rating_methodology.md`; updated the in-report methodology page (`render_methodology_html`) with an Analyst-Revision + Benchmark-Scores source row and a new Benchmark Scores section.
- **Generated artifact note:** `artifacts/reports/ratings-methodology.html` / `ratings-dashboard.html` regenerate from `report.py` on the next `python -m stock_rating.pipeline.report` run (needs `DATABASE_URL`); not hand-edited.
- **Code-review fixes applied** (high-effort review): (A) empty-ratings row `colspan` 10→14 to match the now-14-column table; (B) added `"Analyst Rev" → "Rev"` to `factor_short_name` so the 6th factor cell stays compact; (C) escaped the F-Score `title` attribute; (D) added clarifying `title` tooltips to the F-Score/Magic/EV-EBIT headers (rank/multiple direction); (E) added the CHANGELOG entry (AGENTS.md requires recording user-facing + documentation changes). Verifiers confirmed SQL column counts (31 in both query variants), header↔row index alignment (14 each), f-string brace escaping, and null-placeholder paths were already correct. 163 tests pass.

### File List

- `src/stock_rating/pipeline/report.py` — SQL (both variants), `RatingSnapshot` + row mapping, `render_rating_row`, `render_factor_cell`, new `render_benchmark_cell`/`render_fscore_cell`, table header, CSS, methodology HTML
- `docs/architecture.md` — expanded from stub to full architecture
- `docs/rating_methodology.md` — "Where to see results" section
- `tests/test_report.py` — grade badge, full-render, null-placeholder, low-confidence F-Score tests
