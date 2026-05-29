create table if not exists analyst_consensus_daily (
    symbol text not null references symbols(symbol),
    date date not null,
    analyst_target_price numeric,
    strong_buy_count integer,
    buy_count integer,
    hold_count integer,
    sell_count integer,
    strong_sell_count integer,
    suggestion_label text,
    suggestion_score numeric,
    source text not null,
    ingested_at timestamptz not null default now(),
    primary key (symbol, date, source)
);
