from dataclasses import dataclass
import os

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "")
    alpha_vantage_api_key: str = os.getenv("ALPHA_VANTAGE_API_KEY", "")
    alpha_vantage_max_requests_per_run: int = int(os.getenv("ALPHA_VANTAGE_MAX_REQUESTS_PER_RUN", "20"))
    alpha_vantage_min_interval_seconds: float = float(os.getenv("ALPHA_VANTAGE_MIN_INTERVAL_SECONDS", "1.2"))
    twelve_data_api_key: str = os.getenv("TWELVE_DATA_API_KEY", "")
    stooq_api_key: str = os.getenv("STOOQ_API_KEY", "")
    sec_user_agent: str = os.getenv("SEC_USER_AGENT", "stock-rating/0.1 research@localhost")
    fred_api_key: str = os.getenv("FRED_API_KEY", "")
    symbol_limit: int = int(os.getenv("STOCK_RATING_SYMBOL_LIMIT", "100"))
    symbol_seed_path: str = os.getenv("STOCK_RATING_SYMBOL_SEED_PATH", "")
    plan_output_dir: str = os.getenv("STOCK_RATING_PLAN_OUTPUT_DIR", "")


def get_settings() -> Settings:
    return Settings()
