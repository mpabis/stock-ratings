alter table if exists fundamental_facts
    add column if not exists period_start date,
    add column if not exists period_end date,
    add column if not exists frame text;
