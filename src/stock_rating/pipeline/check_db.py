from stock_rating.config import get_settings
from stock_rating.db import DatabaseConfig, connect_postgres, format_masked_database_url, is_configured, mask_database_url


def main() -> None:
    settings = get_settings()
    config = DatabaseConfig(url=settings.database_url)

    if not is_configured(config):
        print("DATABASE_URL is not configured")
        return

    masked = mask_database_url(config.url)
    print(f"Configured database: {format_masked_database_url(masked)}")
    if not masked.is_valid:
        print("DATABASE_URL format: invalid")
        print("Likely issue: remove square brackets or other unexpected characters from the host portion")
        return

    try:
        connection = connect_postgres(config.url)
        cursor = connection.cursor()
        cursor.execute("select current_database(), current_user")
        database_name, current_user = cursor.fetchone()
        print("Database connection: ok")
        print(f"Current database: {database_name}")
        print(f"Current user: {current_user}")
    except Exception as error:
        print("Database connection: failed")
        print(f"Error type: {type(error).__name__}")
        print(f"Error message: {error}")
    finally:
        try:
            cursor.close()
            connection.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()