# Story 2.1: Stooq Rate-Limit Resilience

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an operator of the daily refresh pipeline,
I want the Stooq price provider to be paced, to treat its rate-limit 404s as soft/retryable, and to be bounded per run,
so that a burst of fallback requests (when Alpha Vantage / Twelve Data quotas are exhausted) doesn't get the whole batch IP-throttled and recorded as hard failures.

## Context

During a full-universe refresh on 2026-06-20, ~100 symbols cascaded to Stooq in a single run (Alpha Vantage's ~25/day and Twelve Data quotas were already spent). Stooq's keyless CSV endpoint (`stooq.com/q/d/l/`) IP-throttles bulk requests and returns **HTTP 404 once throttled** — for valid symbols. (A genuinely missing symbol returns HTTP 200 with a `"No data"` body, not 404, so 404 is the throttle signal.)

Today this surfaces as a large block of `stooq_error` failures (e.g. `AMZN`, `ORCL`, `JPM`), and the affected symbols are marked **failed** rather than retryable. The symbol normalization is correct — `normalize_symbol_for_stooq` already appends `.us` for US tickers (`amzn.us`); this is purely a pacing/limit/classification problem.

Relevant code:
- `src/stock_rating/ingest/prices.py`: `fetch_stooq_daily` (retry loop), `TRANSIENT_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}` (404 absent → not retried), `_is_transient_http_error`, `StooqResponseError`.
- `src/stock_rating/pipeline/daily.py`: `execute_stooq_refresh_plan` (~line 809) — unlike `execute_alpha_vantage_refresh_plan` / `execute_finnhub_analyst_refresh_plan`, it has **no `request_pause_seconds`** inter-symbol pacing. The Alpha Vantage plan pauses via `settings.alpha_vantage_min_interval_seconds`.
- `src/stock_rating/config.py`: existing `alpha_vantage_min_interval_seconds`, `finnhub_analyst_min_interval_seconds` settings to mirror.

## Acceptance Criteria

1. **Pacing:** `execute_stooq_refresh_plan` accepts a `request_pause_seconds` (mirroring the Alpha Vantage plan) and sleeps between symbols when > 0. A new `STOOQ_MIN_INTERVAL_SECONDS` setting (sensible default, e.g. 1.0s) feeds it from `run_pipeline`.
2. **Soft-fail on throttle:** a Stooq HTTP 404 (the throttle signal) is classified as a **rate-limit / transient** condition, not a hard `failed`. The affected symbol is recorded with a `rate_limited` status (or equivalent) so the existing `unresolved_tasks_from` retry logic re-attempts it on a later run rather than leaving it permanently failed. A distinct `StooqRateLimitError` (mirroring `AlphaVantageAnalystRateLimitError`) is the clean way to signal this.
3. **Per-run budget:** Stooq fallback is capped per run (e.g. `STOOQ_MAX_REQUESTS_PER_RUN`) so an exhausted-upstream cascade can't dump the whole universe onto Stooq at once; symbols beyond the cap are left unresolved (retried next run) and the cap is logged, not silently swallowed.
4. **Backoff already exists** for genuinely transient codes (`fetch_stooq_daily` retries 408/429/5xx) — extend or document so a 404-as-throttle either gets a short bounded backoff within the call or is surfaced as the rate-limit error for the plan to handle. Don't infinite-retry.
5. **Distinguish real "no data" from throttle:** a true empty/`"No data"` response (HTTP 200) must still be treated as "no bars for this symbol" (existing `No daily bars returned` path), NOT as a rate-limit — only the 404/blocked signal is the throttle.
6. Tests cover: 404 → rate-limit classification (retryable, not failed), pacing sleep invoked between symbols, the per-run cap leaving overflow unresolved, and that a real `200 "No data"` still raises the no-bars error.

## Tasks / Subtasks

- [ ] **Task 1: Classify the throttle (AC: #2, #4, #5)**
  - [ ] Add `StooqRateLimitError(RuntimeError)` in `ingest/prices.py`. In `fetch_stooq_daily`, on `HTTPError` with `code == 404` (after the transient-retry path is exhausted/not applicable), raise `StooqRateLimitError` instead of `StooqResponseError`. Keep the `No daily bars returned` (HTTP 200 empty) path as a normal `StooqResponseError` — that is a real no-data, not a throttle.
  - [ ] Consider whether 429 (also a throttle) should likewise raise `StooqRateLimitError` rather than just retrying.
- [ ] **Task 2: Pace the plan (AC: #1)**
  - [ ] Add `request_pause_seconds: float = 0.0` + `sleep_fn=time.sleep` to `execute_stooq_refresh_plan` and sleep between symbols (copy the pattern at `daily.py:719-720`).
  - [ ] Add `stooq_min_interval_seconds` to `config.py` (env `STOOQ_MIN_INTERVAL_SECONDS`, default ~1.0) and pass it from `execute_price_refresh_plan` / `run_pipeline` into the Stooq plan.
- [ ] **Task 3: Handle the rate-limit status + budget (AC: #2, #3)**
  - [ ] In `execute_stooq_refresh_plan`, catch `StooqRateLimitError` and record the symbol run as `rate_limited` (mirroring how the Alpha Vantage plan records `AlphaVantageAnalystRateLimitError`), so `unresolved_tasks_from` retries it.
  - [ ] Add `STOOQ_MAX_REQUESTS_PER_RUN` (config) and stop issuing Stooq calls once hit; record the remainder as skipped/unresolved with a clear `provider_error_code` and a `log`/print line stating how many were deferred.
- [ ] **Task 4: Tests (AC: #6)**
  - [ ] Extend `tests/test_price_ingest.py`: a fake `urlopen` raising `HTTPError(404)` → `fetch_stooq_daily` raises `StooqRateLimitError`; a `200` body `"No data"` → `StooqResponseError` (no-bars). 
  - [ ] Extend the pipeline tests (`tests/test_refresh_planning.py` or the price-plan tests): 404 path records `rate_limited` (not `failed`) and the symbol is returned by `unresolved_tasks_from`; pacing `sleep_fn` called between symbols; per-run cap leaves overflow unresolved.

## Dev Notes

- **Not a symbol-format bug.** `normalize_symbol_for_stooq` is correct (`.us` suffix). Do not change normalization.
- **404 ≠ missing symbol on Stooq.** Missing → `200 "No data"`. 404 → throttled/blocked. This distinction (AC #5) is the crux; don't collapse them.
- **Mirror the Alpha Vantage plan.** It already models pacing (`request_pause_seconds`), a per-run cap (`alpha_vantage_max_requests_per_run`), and rate-limit classification (`AlphaVantageAnalystRateLimitError` → `rate_limited`). This story brings Stooq to parity — prefer reusing those patterns over inventing new ones.
- **Stooq is the last-resort fallback.** Under normal tier-based planning few symbols reach it per run; the failure mode only appears when upstream quotas are exhausted AND a large refresh is forced. The per-run cap is the structural guard against that.
- **DI everywhere** — keep `urlopen_fn` / `sleep_fn` injectable for tests (already the case).

### Project Structure Notes

- Changes confined to `ingest/prices.py`, `pipeline/daily.py`, `config.py`, and the price/pipeline tests. No schema or model changes.
- Coordinate config naming with existing keys: `STOCK_RATING_*` / provider `*_MIN_INTERVAL_SECONDS` / `*_MAX_REQUESTS_PER_RUN` conventions.

### References

- [Source: src/stock_rating/ingest/prices.py#fetch_stooq_daily] — retry loop + transient codes (404 absent), lines 341-378.
- [Source: src/stock_rating/ingest/prices.py#normalize_symbol_for_stooq] — `.us` suffix (correct), lines 162-176.
- [Source: src/stock_rating/pipeline/daily.py#execute_stooq_refresh_plan] — Stooq plan (~809), missing the pacing the AV plan has (719-720); `unresolved_tasks_from` retry logic.
- [Source: src/stock_rating/config.py] — `alpha_vantage_min_interval_seconds`, `alpha_vantage_max_requests_per_run` to mirror.
- 2026-06-20 session: diagnosed the 404-as-throttle behavior during the v6 full-universe re-rate (Stooq got ~100 cascaded requests at once).

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
