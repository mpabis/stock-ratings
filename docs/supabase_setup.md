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
STOCK_RATING_FUNDAMENTAL_SYMBOL_LIMIT=10
STOCK_RATING_ANALYST_SYMBOL_LIMIT=0
```

## 4. Initialize the database

Run the migration command. It applies the idempotent base schema first, then any pending files in `sql/migrations/`.

```powershell
python -m stock_rating.pipeline.migrate
```

If `psql` is preferred, apply [sql/schema.sql](../sql/schema.sql) and then every file in [sql/migrations](../sql/migrations) in filename order.

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

This loads the tracked universe from [data/symbols.csv](../data/symbols.csv).

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
