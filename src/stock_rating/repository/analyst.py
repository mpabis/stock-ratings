from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from stock_rating.db import DatabaseConfig, connect_postgres, is_configured


@dataclass(frozen=True)
class AnalystConsensusPoint:
    symbol: str
    date: date
    analyst_target_price: Decimal | None
    strong_buy_count: int | None
    buy_count: int | None
    hold_count: int | None
    sell_count: int | None
    strong_sell_count: int | None
    suggestion_label: str | None
    suggestion_score: Decimal | None
    source: str


def load_latest_analyst_consensus(database_url: str, symbol: str, connect_fn=connect_postgres) -> AnalystConsensusPoint | None:
    if not is_configured(DatabaseConfig(url=database_url)):
        return None

    connection = None
    cursor = None
    try:
        connection = connect_fn(database_url)
        cursor = connection.cursor()
        cursor.execute(
            """
            select
                symbol,
                date,
                analyst_target_price,
                strong_buy_count,
                buy_count,
                hold_count,
                sell_count,
                strong_sell_count,
                suggestion_label,
                suggestion_score,
                source
            from analyst_consensus_daily
            where symbol = %s
            order by date desc, ingested_at desc
            limit 1
            """,
            (symbol,),
        )
        row = cursor.fetchone()
    except Exception:
        return None
    finally:
        try:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()
        except Exception:
            pass

    if row is None:
        return None

    return AnalystConsensusPoint(
        symbol=row[0],
        date=row[1],
        analyst_target_price=row[2],
        strong_buy_count=row[3],
        buy_count=row[4],
        hold_count=row[5],
        sell_count=row[6],
        strong_sell_count=row[7],
        suggestion_label=row[8],
        suggestion_score=row[9],
        source=row[10],
    )


def load_latest_analyst_dates(
    database_url: str,
    symbols: list[str],
    connect_fn=connect_postgres,
) -> dict[str, date]:
    if not symbols or not is_configured(DatabaseConfig(url=database_url)):
        return {}

    connection = None
    cursor = None
    try:
        connection = connect_fn(database_url)
        cursor = connection.cursor()
        cursor.execute(
            """
            select symbol, max(date)
            from analyst_consensus_daily
            where symbol = any(%s)
            group by symbol
            """,
            (symbols,),
        )
        rows = cursor.fetchall()
    except Exception:
        return {}
    finally:
        try:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()
        except Exception:
            pass

    return {row[0]: row[1] for row in rows}


def load_latest_analyst_dates_for_source(
    database_url: str,
    symbols: list[str],
    source: str,
    connect_fn=connect_postgres,
) -> dict[str, date]:
    if not symbols or not is_configured(DatabaseConfig(url=database_url)):
        return {}

    connection = None
    cursor = None
    try:
        connection = connect_fn(database_url)
        cursor = connection.cursor()
        cursor.execute(
            """
            select symbol, max(date)
            from analyst_consensus_daily
            where symbol = any(%s)
              and source = %s
            group by symbol
            """,
            (symbols, source),
        )
        rows = cursor.fetchall()
    except Exception:
        return {}
    finally:
        try:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()
        except Exception:
            pass

    return {row[0]: row[1] for row in rows}
