from pathlib import Path

from stock_rating.config import get_settings
from stock_rating.db import DatabaseConfig, connect_postgres, is_configured


def migrations_directory() -> Path:
    return Path(__file__).resolve().parents[3] / "sql" / "migrations"


def list_migration_files(directory: Path) -> list[Path]:
    return sorted((path for path in directory.glob("*.sql") if path.is_file()), key=lambda path: path.name)


def pending_migration_files(all_files: list[Path], applied_names: set[str]) -> list[Path]:
    return [path for path in all_files if path.name not in applied_names]


def main() -> None:
    settings = get_settings()
    config = DatabaseConfig(url=settings.database_url)

    if not is_configured(config):
        print("DATABASE_URL is not configured")
        return

    directory = migrations_directory()
    if not directory.exists():
        print(f"Migrations directory not found: {directory}")
        return

    all_files = list_migration_files(directory)
    if not all_files:
        print("No migration files found")
        return

    connection = connect_postgres(config.url)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                create table if not exists schema_migrations (
                    name text primary key,
                    applied_at timestamptz not null default now()
                )
                """
            )
            connection.commit()

            cursor.execute("select name from schema_migrations")
            applied_names = {row[0] for row in cursor.fetchall()}

        pending_files = pending_migration_files(all_files, applied_names)
        if not pending_files:
            print("No pending migrations")
            return

        for migration_file in pending_files:
            sql = migration_file.read_text(encoding="utf-8")
            with connection.cursor() as cursor:
                cursor.execute(sql)
                cursor.execute(
                    "insert into schema_migrations (name) values (%s)",
                    (migration_file.name,),
                )
            connection.commit()
            print(f"Applied {migration_file.name}")

        print(f"Applied {len(pending_files)} migration(s)")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()