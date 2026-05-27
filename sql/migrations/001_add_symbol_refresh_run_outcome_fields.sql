alter table if exists symbol_refresh_runs
    add column if not exists fetched_bar_count integer,
    add column if not exists provider_error_code text;
