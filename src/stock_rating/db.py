from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class DatabaseConfig:
    url: str


def is_configured(config: DatabaseConfig) -> bool:
    return bool(config.url)


def connect_postgres(url: str) -> Any:
    import psycopg

    return psycopg.connect(url, prepare_threshold=None)


@dataclass(frozen=True)
class MaskedDatabaseUrl:
    scheme: str
    username: str
    host: str
    port: int | None
    database: str
    is_valid: bool = True


def mask_database_url(url: str) -> MaskedDatabaseUrl:
    try:
        parsed = urlparse(url)
        database = parsed.path.lstrip("/") if parsed.path else ""
        return MaskedDatabaseUrl(
            scheme=parsed.scheme,
            username=parsed.username or "",
            host=parsed.hostname or "",
            port=parsed.port,
            database=database,
            is_valid=True,
        )
    except ValueError:
        sanitized = url.replace("[", "").replace("]", "")
        parsed = urlparse(sanitized)
        database = parsed.path.lstrip("/") if parsed.path else ""
        return MaskedDatabaseUrl(
            scheme=parsed.scheme,
            username=parsed.username or "",
            host=parsed.hostname or "",
            port=parsed.port,
            database=database,
            is_valid=False,
        )


def format_masked_database_url(masked: MaskedDatabaseUrl) -> str:
    port = f":{masked.port}" if masked.port else ""
    database = f"/{masked.database}" if masked.database else ""
    username = masked.username or "<unknown-user>"
    host = masked.host or "<unknown-host>"
    scheme = masked.scheme or "postgresql"
    return f"{scheme}://{username}:***@{host}{port}{database}"
