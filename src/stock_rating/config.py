from dataclasses import dataclass, field
import os

from dotenv import load_dotenv


load_dotenv()


def _env_str(name: str, default: str = "") -> str:
    return os.getenv(name) or default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return int(value)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return float(value)


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _env_str("DATABASE_URL"))
    alpha_vantage_api_key: str = field(default_factory=lambda: _env_str("ALPHA_VANTAGE_API_KEY"))
    alpha_vantage_max_requests_per_run: int = field(
        default_factory=lambda: _env_int("ALPHA_VANTAGE_MAX_REQUESTS_PER_RUN", 20)
    )
    alpha_vantage_min_interval_seconds: float = field(
        default_factory=lambda: _env_float("ALPHA_VANTAGE_MIN_INTERVAL_SECONDS", 1.2)
    )
    twelve_data_api_key: str = field(default_factory=lambda: _env_str("TWELVE_DATA_API_KEY"))
    twelve_data_max_requests_per_run: int = field(
        default_factory=lambda: _env_int("TWELVE_DATA_MAX_REQUESTS_PER_RUN", 12)
    )
    stooq_api_key: str = field(default_factory=lambda: _env_str("STOOQ_API_KEY"))
    stooq_min_interval_seconds: float = field(default_factory=lambda: _env_float("STOOQ_MIN_INTERVAL_SECONDS", 1.0))
    stooq_max_requests_per_run: int = field(default_factory=lambda: _env_int("STOOQ_MAX_REQUESTS_PER_RUN", 40))
    sec_user_agent: str = field(default_factory=lambda: _env_str("SEC_USER_AGENT", "stock-rating/0.1 research@localhost"))
    fred_api_key: str = field(default_factory=lambda: _env_str("FRED_API_KEY"))
    symbol_limit: int = field(default_factory=lambda: _env_int("STOCK_RATING_SYMBOL_LIMIT", 100))
    fundamental_symbol_limit: int = field(default_factory=lambda: _env_int("STOCK_RATING_FUNDAMENTAL_SYMBOL_LIMIT", 10))
    analyst_symbol_limit: int = field(default_factory=lambda: _env_int("STOCK_RATING_ANALYST_SYMBOL_LIMIT", 0))
    finnhub_api_key: str = field(default_factory=lambda: _env_str("FINNHUB_API_KEY"))
    finnhub_analyst_symbol_limit: int = field(
        default_factory=lambda: _env_int("STOCK_RATING_FINNHUB_ANALYST_SYMBOL_LIMIT", 0)
    )
    finnhub_analyst_min_interval_seconds: float = field(
        default_factory=lambda: _env_float("FINNHUB_ANALYST_MIN_INTERVAL_SECONDS", 2.0)
    )
    symbol_seed_path: str = field(default_factory=lambda: _env_str("STOCK_RATING_SYMBOL_SEED_PATH"))
    plan_output_dir: str = field(default_factory=lambda: _env_str("STOCK_RATING_PLAN_OUTPUT_DIR"))


def get_settings() -> Settings:
    return Settings()
