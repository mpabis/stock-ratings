-- Story 1.3: analyst estimate-revisions / sentiment-momentum factor.
-- A sixth composite factor (model v6). Stores its per-symbol sub-score plus the
-- cross-sectional percentile and A-F grade produced by the universe pass.
alter table if exists ratings_daily
    add column if not exists analyst_revision_score numeric,
    add column if not exists analyst_revision_percentile numeric,
    add column if not exists analyst_revision_grade text;
