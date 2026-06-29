"""One-time batch loader: populate sector + industry for all active symbols.

Calls Alpha Vantage COMPANY_OVERVIEW first (primary). If AV returns no sector
data, falls back to Finnhub company profile.

Usage:
    python -m stock_rating.pipeline.bootstrap_sector

Options:
    --dry-run    Print what would be written without touching the DB.
    --skip-filled  Skip symbols that already have a sector populated.

The script is rate-limited to ~1 AV call per second by default (same interval
as the daily pipeline).  Symbols that already have a sector can be skipped with
--skip-filled to avoid burning quota on a re-run.
"""

import argparse
import time

from stock_rating.config import get_settings
from stock_rating.db import connect_postgres, DatabaseConfig, is_configured
from stock_rating.ingest.sector import fetch_sector_with_fallback
from stock_rating.repository.symbols import load_symbol_seeds, upsert_symbol_sector


def load_symbols_missing_sector(database_url: str) -> list[str]:
    """Return symbols that have no sector populated yet."""
    if not is_configured(DatabaseConfig(url=database_url)):
        return []
    try:
        connection = connect_postgres(database_url)
        cursor = connection.cursor()
        cursor.execute(
            """
            select symbol
            from symbols
            where active = true
              and (sector is null or sector = '')
            order by symbol asc
            """
        )
        rows = cursor.fetchall()
        return [row[0] for row in rows]
    except Exception:
        return []
    finally:
        try:
            cursor.close()
            connection.close()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Populate sector/industry for all active symbols.")
    parser.add_argument("--dry-run", action="store_true", help="Print intended writes without touching the DB.")
    parser.add_argument(
        "--skip-filled",
        action="store_true",
        help="Skip symbols that already have a sector value.",
    )
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=1.2,
        help="Pause between API calls (default 1.2s to stay within AV free tier).",
    )
    args = parser.parse_args()

    settings = get_settings()

    if not settings.alpha_vantage_api_key and not settings.finnhub_api_key:
        print("No API keys configured (ALPHA_VANTAGE_API_KEY or FINNHUB_API_KEY required).")
        return

    seeds = load_symbol_seeds(settings.database_url, settings.symbol_seed_path or None)
    all_symbols = [seed.symbol for seed in seeds]

    if args.skip_filled and settings.database_url:
        missing = set(load_symbols_missing_sector(settings.database_url))
        symbols_to_process = [s for s in all_symbols if s in missing]
        print(f"Symbols without sector: {len(symbols_to_process)} of {len(all_symbols)} total.")
    else:
        symbols_to_process = all_symbols
        print(f"Processing all {len(symbols_to_process)} active symbols.")

    succeeded = 0
    skipped = 0
    failed = 0

    for index, symbol in enumerate(symbols_to_process):
        info = fetch_sector_with_fallback(
            symbol,
            alpha_vantage_api_key=settings.alpha_vantage_api_key,
            finnhub_api_key=settings.finnhub_api_key,
        )

        if info is None:
            print(f"[{index + 1}/{len(symbols_to_process)}] {symbol}: no sector data found")
            skipped += 1
        elif args.dry_run:
            print(
                f"[{index + 1}/{len(symbols_to_process)}] {symbol}: "
                f"sector={info.sector!r} industry={info.industry!r} "
                f"source={info.sector_source} (dry-run, not written)"
            )
            succeeded += 1
        else:
            ok = upsert_symbol_sector(
                settings.database_url,
                symbol=info.symbol,
                sector=info.sector,
                industry=info.industry,
                sector_source=info.sector_source,
            )
            if ok:
                print(
                    f"[{index + 1}/{len(symbols_to_process)}] {symbol}: "
                    f"sector={info.sector!r} industry={info.industry!r} ({info.sector_source})"
                )
                succeeded += 1
            else:
                print(f"[{index + 1}/{len(symbols_to_process)}] {symbol}: DB write failed")
                failed += 1

        if index < len(symbols_to_process) - 1 and args.pause_seconds > 0:
            time.sleep(args.pause_seconds)

    print(f"\nDone. succeeded={succeeded} skipped={skipped} failed={failed}")


if __name__ == "__main__":
    main()
