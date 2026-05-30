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
        cursor.execute(_latest_fundamental_query(include_period_metadata=True), (symbol,))
        rows = cursor.fetchall()
    except Exception:
        try:
            if connection is not None:
                connection.rollback()
            if cursor is not None:
                cursor.execute(_latest_fundamental_query(include_period_metadata=False), (symbol,))
                rows = cursor.fetchall()
            else:
                return []
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

    facts: list[FundamentalFact] = []
    for row in rows:
        has_period_metadata = len(row) >= 13
        period_start = row[8] if has_period_metadata else None
        period_end = row[9] if has_period_metadata else None
        frame = row[10] if has_period_metadata else None
        filed_at = row[11] if has_period_metadata else row[8]
        source = row[12] if has_period_metadata else row[9]
        fact = FundamentalFact(
            cik=row[0],
            symbol=row[1],
            fiscal_period=row[2],
            fiscal_year=row[3],
            form=row[4],
            metric=row[5],
            value=row[6],
            unit=row[7],
            filed_at=filed_at,
            period_start=period_start,
            period_end=period_end,
            frame=frame,
            source=source,
        )
        facts.append(fact)

    return facts


def _latest_fundamental_query(include_period_metadata: bool) -> str:
    if include_period_metadata:
        period_columns = """
                period_start,
                period_end,
                frame,
        """
    else:
        period_columns = ""

    return f"""
            select
                cik,
                symbol,
                fiscal_period,
                fiscal_year,
                form,
                metric,
                value,
                unit,
                {period_columns}
                filed_at,
                source
            from fundamental_facts
            where symbol = %s
            order by metric asc, fiscal_year desc, period_end desc nulls last, filed_at desc nulls last
            """
