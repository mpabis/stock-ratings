create table if not exists symbols (
    symbol text primary key,
    company_name text not null,
    exchange text,
    cik text,
    sector text,
    industry text,
    active boolean not null default true,
    refresh_tier integer not null default 2,
    last_price_refresh_at timestamptz,
    last_fundamental_refresh_at timestamptz
);

create table if not exists price_daily (
    symbol text not null references symbols(symbol),
    date date not null,
    open numeric,
    high numeric,
    low numeric,
    close numeric,
    adjusted_close numeric,
    volume bigint,
    source text not null,
    ingested_at timestamptz not null default now(),
    primary key (symbol, date, source)
);

create table if not exists fundamental_facts (
    cik text not null,
    symbol text not null references symbols(symbol),
    fiscal_period text,
    fiscal_year integer,
    form text,
    metric text not null,
    value numeric,
    unit text,
    filed_at timestamptz,
    source text not null,
    primary key (symbol, fiscal_year, fiscal_period, metric, source)
);

create table if not exists features_daily (
    symbol text not null references symbols(symbol),
    date date not null,
    feature_name text not null,
    feature_value numeric,
    source_version text not null,
    primary key (symbol, date, feature_name, source_version)
);

create table if not exists ratings_daily (
    symbol text not null references symbols(symbol),
    date date not null,
    rating_score integer not null,
    rating_label text not null,
    valuation_score numeric,
    quality_score numeric,
    growth_score numeric,
    momentum_score numeric,
    risk_score numeric,
    explanation_json jsonb not null,
    model_version text not null,
    created_at timestamptz not null default now(),
    freshness_status text not null,
    freshest_input_date date,
    primary key (symbol, date, model_version)
);

create table if not exists pipeline_runs (
    run_id text primary key,
    started_at timestamptz not null,
    finished_at timestamptz,
    status text not null,
    error_message text,
    git_sha text
);

create table if not exists symbol_refresh_runs (
    run_id text not null references pipeline_runs(run_id),
    symbol text not null references symbols(symbol),
    data_type text not null,
    provider text not null,
    status text not null,
    attempted_at timestamptz not null,
    completed_at timestamptz,
    error_message text,
    fetched_bar_count integer,
    provider_error_code text,
    primary key (run_id, symbol, data_type, provider, attempted_at)
);
