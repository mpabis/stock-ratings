from csv import DictReader
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from stock_rating.db import DatabaseConfig, connect_postgres, is_configured


@dataclass(frozen=True)
class SymbolSeed:
    symbol: str
    company_name: str
    exchange: str
    refresh_tier: int
    last_price_date: date
    active: bool


def default_seed_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "symbols.csv"


def _parse_active(raw_value: str) -> bool:
    return raw_value.strip().lower() in {"1", "true", "yes", "y"}


def load_symbol_seeds(
    database_url: str = "",
    seed_path: str | None = None,
    connect_fn=connect_postgres,
) -> list[SymbolSeed]:
    if is_configured(DatabaseConfig(url=database_url)):
        try:
            return load_symbol_seeds_from_database(database_url, connect_fn=connect_fn)
        except Exception:
            return load_symbol_seeds_from_csv(seed_path)

    return load_symbol_seeds_from_csv(seed_path)


def load_symbol_seeds_from_csv(seed_path: str | None = None) -> list[SymbolSeed]:
    path = Path(seed_path) if seed_path else default_seed_path()
    seeds: list[SymbolSeed] = []

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = DictReader(handle)
        for row in reader:
            seeds.append(
                SymbolSeed(
                    symbol=row["symbol"].strip(),
                    company_name=row["company_name"].strip(),
                    exchange=row["exchange"].strip(),
                    refresh_tier=int(row["refresh_tier"]),
                    last_price_date=date.fromisoformat(row["last_price_date"]),
                    active=_parse_active(row.get("active", "true")),
                )
            )

    return [seed for seed in seeds if seed.active]


def load_symbol_seeds_from_database(database_url: str, connect_fn=connect_postgres) -> list[SymbolSeed]:
    connection = connect_fn(database_url)
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            select
                symbol,
                company_name,
                coalesce(exchange, ''),
                refresh_tier,
                last_price_refresh_at,
                active
            from symbols
            where active = true
            order by refresh_tier asc, symbol asc
            """
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()

    return [_seed_from_database_row(row) for row in rows]


def _seed_from_database_row(row: tuple[object, ...]) -> SymbolSeed:
    symbol, company_name, exchange, refresh_tier, last_price_refresh_at, active = row
    return SymbolSeed(
        symbol=str(symbol),
        company_name=str(company_name),
        exchange=str(exchange),
        refresh_tier=int(refresh_tier),
        last_price_date=_normalize_last_price_date(last_price_refresh_at),
        active=bool(active),
    )


def _normalize_last_price_date(last_price_refresh_at: object) -> date:
    if isinstance(last_price_refresh_at, datetime):
        return last_price_refresh_at.date()
    if isinstance(last_price_refresh_at, date):
        return last_price_refresh_at
    return date.today() - timedelta(days=7)


def update_symbol_last_price_refresh_at(
    database_url: str,
    symbol: str,
    refreshed_at: datetime | None = None,
    connect_fn=connect_postgres,
) -> bool:
    if not is_configured(DatabaseConfig(url=database_url)):
        return False

    effective_refreshed_at = refreshed_at or datetime.now(UTC)
    try:
        connection = connect_fn(database_url)
        cursor = connection.cursor()
        cursor.execute(
            """
            update symbols
            set last_price_refresh_at = %s
            where symbol = %s
            """,
            (effective_refreshed_at, symbol),
        )
        connection.commit()
        return True
    except Exception:
        return False
    finally:
        try:
            cursor.close()
            connection.close()
        except Exception:
            pass


def update_symbol_last_fundamental_refresh_at(
    database_url: str,
    symbol: str,
    refreshed_at: datetime | None = None,
    connect_fn=connect_postgres,
) -> bool:
    if not is_configured(DatabaseConfig(url=database_url)):
        return False

    effective_refreshed_at = refreshed_at or datetime.now(UTC)
    try:
        connection = connect_fn(database_url)
        cursor = connection.cursor()
        cursor.execute(
            """
            update symbols
            set last_fundamental_refresh_at = %s
            where symbol = %s
            """,
            (effective_refreshed_at, symbol),
        )
        connection.commit()
        return True
    except Exception:
        return False
    finally:
        try:
            cursor.close()
            connection.close()
        except Exception:
            pass


def upsert_symbol_seeds(
    database_url: str,
    seeds: list[SymbolSeed],
    connect_fn=connect_postgres,
) -> bool:
    if not seeds:
        return False
    if not is_configured(DatabaseConfig(url=database_url)):
        return False

    try:
        connection = connect_fn(database_url)
        cursor = connection.cursor()
        active_symbols = [seed.symbol for seed in seeds]
        cursor.executemany(
            """
            insert into symbols (
                symbol,
                company_name,
                exchange,
                active,
                refresh_tier,
                last_price_refresh_at
            ) values (%s, %s, %s, %s, %s, %s)
            on conflict (symbol) do update set
                company_name = excluded.company_name,
                exchange = excluded.exchange,
                active = excluded.active,
                refresh_tier = excluded.refresh_tier
            """,
            [
                (
                    seed.symbol,
                    seed.company_name,
                    seed.exchange,
                    seed.active,
                    seed.refresh_tier,
                    seed.last_price_date,
                )
                for seed in seeds
            ],
        )
        cursor.execute(
            """
            update symbols
            set active = false
            where symbol <> all(%s)
            """,
            (active_symbols,),
        )
        connection.commit()
        return True
    except Exception as error:
        print(f"Symbol upsert failed: {type(error).__name__}: {error}")
        return False
    finally:
        try:
            cursor.close()
            connection.close()
        except Exception:
            pass
