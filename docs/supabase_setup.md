# Supabase Setup

This project expects a normal Postgres connection string in `DATABASE_URL`.

## 1. Create the project

1. Create a new project in Supabase.
2. Wait for the database to finish provisioning.
3. Open the project settings and locate the Postgres connection details.

## 2. Get the connection string

Use the **direct Postgres connection string**, not the API URL.

You want a value that looks like this:

```text
postgresql://postgres:<password>@<host>:5432/postgres
```

Set that as `DATABASE_URL` in your local environment.

## 3. Configure local environment

Create a local `.env` or set environment variables in your shell:

```text
DATABASE_URL=postgresql://postgres:<password>@<host>:5432/postgres
ALPHA_VANTAGE_API_KEY=your_alpha_vantage_key
TWELVE_DATA_API_KEY=your_twelve_data_key
FRED_API_KEY=your_fred_key
STOCK_RATING_SYMBOL_LIMIT=100
```

## 4. Initialize the database

Run the schema first, then the migration.

Using `psql`, the commands are:

```powershell
psql "$env:DATABASE_URL" -f sql/schema.sql
psql "$env:DATABASE_URL" -f sql/migrations/001_add_symbol_refresh_run_outcome_fields.sql
```

If `psql` is not installed locally, you can also paste the SQL into the Supabase SQL Editor in this order:

1. [sql/schema.sql](c:/Users/MartinPabiš/source/repos/playground/stock-ratings/sql/schema.sql)
2. [sql/migrations/001_add_symbol_refresh_run_outcome_fields.sql](c:/Users/MartinPabiš/source/repos/playground/stock-ratings/sql/migrations/001_add_symbol_refresh_run_outcome_fields.sql)

## 4b. Verify the connection safely

Before running bootstrap or pipeline commands, verify the configured `DATABASE_URL`:

```powershell
python -m stock_rating.pipeline.check_db
```

This command masks the password and prints only safe connection details plus the connection result.

## 5. Load the symbol universe

After the schema exists, bootstrap the symbols table:

```powershell
python -m stock_rating.pipeline.bootstrap_symbols
```

This loads the tracked universe from [data/symbols.csv](c:/Users/MartinPabiš/source/repos/playground/stock-ratings/data/symbols.csv).

## 6. Run the daily pipeline

Once the database and API keys are configured:

```powershell
python -m stock_rating.pipeline.daily
```

Successful runs can:

- persist `pipeline_runs`
- persist `symbol_refresh_runs`
- upsert `price_daily`
- update `symbols.last_price_refresh_at`
- persist derived rows into `features_daily`

## 7. Recommended first verification queries

```sql
select count(*) from symbols;
select count(*) from pipeline_runs;
select count(*) from symbol_refresh_runs;
select count(*) from price_daily;
select count(*) from features_daily;
```

## Notes

- Supabase free tier is fine for this MVP.
- Start with direct connections from local development.
- For GitHub Actions later, store the same `DATABASE_URL` as a repository secret.
- Keep API keys out of the repo.