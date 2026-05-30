from stock_rating.db import DatabaseConfig, connect_postgres, is_configured
from stock_rating.ingest.prices import DailyPriceBar


def load_recent_price_bars(
    database_url: str,
    symbol: str,
    limit: int = 130,
    connect_fn=connect_postgres,
) -> list[DailyPriceBar]:
    if not is_configured(DatabaseConfig(url=database_url)):
        return []

    try:
        connection = connect_fn(database_url)
        cursor = connection.cursor()
        cursor.execute(
            """
            with ranked_prices as (
                select
                    symbol,
                    date,
                    open,
                    high,
                    low,
                    close,
                    adjusted_close,
                    volume,
                    source,
                    row_number() over (
                        partition by symbol, date
                        order by ingested_at desc, source asc
                    ) as price_rank
                from price_daily
                where symbol = %s
            )
            select
                symbol,
                date,
                open,
                high,
                low,
                close,
                adjusted_close,
                volume,
                source
            from ranked_prices
            where price_rank = 1
            order by date desc
            limit %s
            """,
            (symbol, limit),
        )
        rows = cursor.fetchall()
    except Exception:
        return []
    finally:
        try:
            cursor.close()
            connection.close()
        except Exception:
            pass

    return [
        DailyPriceBar(
            symbol=row[0],
            date=row[1],
            open=row[2],
            high=row[3],
            low=row[4],
            close=row[5],
            adjusted_close=row[6] or row[5],
            volume=int(row[7] or 0),
            source=row[8],
        )
        for row in rows
    ]
