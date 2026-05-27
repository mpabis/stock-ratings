from stock_rating.config import get_settings
from stock_rating.repository.symbols import load_symbol_seeds_from_csv, upsert_symbol_seeds


def main() -> None:
    settings = get_settings()
    seeds = load_symbol_seeds_from_csv(settings.symbol_seed_path or None)
    persisted = upsert_symbol_seeds(settings.database_url, seeds)

    print(f"Loaded symbols from CSV: {len(seeds)}")
    print(f"Database persistence: {'enabled' if persisted else 'skipped'}")
    if settings.database_url and not persisted:
        print("Likely next step: run sql/schema.sql and pending migrations against the configured database")


if __name__ == "__main__":
    main()
