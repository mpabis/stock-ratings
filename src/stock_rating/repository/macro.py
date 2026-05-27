from stock_rating.db import DatabaseConfig, connect_postgres, is_configured
from stock_rating.ingest.fred_macro import MacroObservation


def load_latest_macro_observations(
    database_url: str,
    series_ids: tuple[str, ...],
    connect_fn=connect_postgres,
) -> dict[str, MacroObservation]:
    if not is_configured(DatabaseConfig(url=database_url)) or not series_ids:
        return {}

    connection = None
    cursor = None
    try:
        connection = connect_fn(database_url)
        cursor = connection.cursor()
        cursor.execute(
            """
            select distinct on (series_id)
                series_id,
                date,
                value,
                source
            from macro_series_daily
            where series_id = any(%s)
            order by series_id, date desc
            """,
            (list(series_ids),),
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

    return {
        row[0]: MacroObservation(
            series_id=row[0],
            date=row[1],
            value=row[2],
            source=row[3],
        )
        for row in rows
    }