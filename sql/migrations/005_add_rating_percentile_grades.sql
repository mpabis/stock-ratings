-- Story 1.1: AAII-style percentile grades.
-- The cross-sectional ranking pass writes universe-relative percentiles (0-1)
-- and per-factor A-F letter grades back onto each rating row. The final
-- composite letter grade continues to live in ratings_daily.rating_label.
alter table if exists ratings_daily
    add column if not exists composite_percentile numeric,
    add column if not exists valuation_percentile numeric,
    add column if not exists quality_percentile numeric,
    add column if not exists growth_percentile numeric,
    add column if not exists momentum_percentile numeric,
    add column if not exists risk_percentile numeric,
    add column if not exists valuation_grade text,
    add column if not exists quality_grade text,
    add column if not exists growth_grade text,
    add column if not exists momentum_grade text,
    add column if not exists risk_grade text;
