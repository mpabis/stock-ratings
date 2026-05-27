create table if not exists macro_series_daily (
    series_id text not null,
    date date not null,
    value numeric,
    source text not null,
    ingested_at timestamptz not null default now(),
    primary key (series_id, date, source)
);