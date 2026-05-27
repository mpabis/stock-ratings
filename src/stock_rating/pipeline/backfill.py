import time

from stock_rating.config import get_settings
from stock_rating.ingest.prices import fetch_alpha_vantage_daily_adjusted, fetch_stooq_daily, persist_price_bars
from stock_rating.repository.symbols import load_symbol_seeds, update_symbol_last_price_refresh_at


def backfill_symbols(
    database_url: str,
    symbols: list[str],
    fetch_bars_fn,
    persist_fn=persist_price_bars,
    mark_refreshed_fn=update_symbol_last_price_refresh_at,
    pause_seconds: float = 0.0,
    sleep_fn=time.sleep,
) -> tuple[int, int]:
    succeeded = 0
    failed = 0

    for index, symbol in enumerate(symbols):
        try:
            bars = fetch_bars_fn(symbol)
            persisted = persist_fn(database_url, bars)
            if database_url and not persisted:
                raise RuntimeError(f"Failed to persist backfill bars for {symbol}")
            if persisted:
                mark_refreshed_fn(database_url, symbol)
            succeeded += 1
        except Exception as error:
            failed += 1
            print(f"Backfill failed for {symbol}: {error}")

        if pause_seconds > 0 and index < len(symbols) - 1:
            sleep_fn(pause_seconds)

    return succeeded, failed


def main() -> None:
    settings = get_settings()
    seeds = load_symbol_seeds(settings.database_url, settings.symbol_seed_path or None)
    symbols = [seed.symbol for seed in seeds[: settings.symbol_limit]]
    if settings.stooq_api_key:
        provider = "stooq"
        fetch_bars_fn = lambda symbol: fetch_stooq_daily(symbol, settings.stooq_api_key)
    elif settings.alpha_vantage_api_key:
        provider = "alpha_vantage_compact"
        fetch_bars_fn = lambda symbol: fetch_alpha_vantage_daily_adjusted(symbol, settings.alpha_vantage_api_key)
    else:
        print("Backfill provider unavailable: configure STOOQ_API_KEY or ALPHA_VANTAGE_API_KEY")
        return

    succeeded, failed = backfill_symbols(
        database_url=settings.database_url,
        symbols=symbols,
        fetch_bars_fn=fetch_bars_fn,
        pause_seconds=settings.alpha_vantage_min_interval_seconds,
    )

    print(f"Backfill provider: {provider}")
    print(f"Backfill symbols attempted: {len(symbols)}")
    print(f"Backfill succeeded: {succeeded}")
    print(f"Backfill failed: {failed}")


if __name__ == "__main__":
    main()