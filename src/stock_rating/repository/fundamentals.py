from stock_rating.db import DatabaseConfig, connect_postgres, is_configured
from stock_rating.ingest.sec_companyfacts import FundamentalFact


def load_latest_fundamental_facts(database_url: str, symbol: str, connect_fn=connect_postgres) -> list[FundamentalFact]:
    if not is_configured(DatabaseConfig(url=database_url)):
        return []

    connection = None
    cursor = None
    try:
        connection = connect_fn(database_url)
        cursor = connection.cursor()
        cursor.execute(
            """
            select
                cik,
                symbol,
                fiscal_period,
                fiscal_year,
                form,
                metric,
                value,
                unit,
                filed_at,
                source
            from fundamental_facts
            where symbol = %s
            order by fiscal_year desc, filed_at desc nulls last, metric asc
            """,
            (symbol,),
        )
        rows = cursor.fetchall()
    except Exception:
        return []
    finally:
        try:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()
        except Exception:
            pass

    facts: dict[str, FundamentalFact] = {}
    for row in rows:
        fact = FundamentalFact(
            cik=row[0],
            symbol=row[1],
            fiscal_period=row[2],
            fiscal_year=row[3],
            form=row[4],
            metric=row[5],
            value=row[6],
            unit=row[7],
            filed_at=row[8],
            source=row[9],
        )
        facts.setdefault(fact.metric, fact)

    return list(facts.values())