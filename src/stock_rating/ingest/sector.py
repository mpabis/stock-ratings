"""Sector and industry data ingestion.

Alpha Vantage COMPANY_OVERVIEW is the primary source - it already contains
Sector and Industry fields in the same payload used for analyst consensus.
Finnhub company profile (finnhub.io/api/v1/stock/profile2) is the fallback.

The AV fetcher reuses the existing helper from ingest/analyst.py to avoid
duplicating retry/backoff logic. Finnhub uses a minimal inline fetch.
"""

from dataclasses import dataclass
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from stock_rating.ingest.analyst import (
    AlphaVantageAnalystResponseError,
    fetch_alpha_vantage_company_overview,
    normalize_symbol_for_finnhub,
)


@dataclass(frozen=True)
class SectorInfo:
    symbol: str
    sector: str | None
    industry: str | None
    sector_source: str


def _clean_str(value: object) -> str | None:
    if value in {None, "", "None", "N/A", "-"}:
        return None
    return str(value).strip() or None


def parse_sector_from_alpha_vantage(symbol: str, payload: dict[str, object]) -> SectorInfo:
    """Extract sector and industry from an AV COMPANY_OVERVIEW payload."""
    return SectorInfo(
        symbol=symbol,
        sector=_clean_str(payload.get("Sector")),
        industry=_clean_str(payload.get("Industry")),
        sector_source="alpha_vantage_overview",
    )


def fetch_sector_from_alpha_vantage(
    symbol: str,
    api_key: str,
    fetch_fn=fetch_alpha_vantage_company_overview,
) -> SectorInfo | None:
    """Fetch sector/industry from AV COMPANY_OVERVIEW. Returns None on error or empty data."""
    try:
        payload = fetch_fn(symbol, api_key)
        info = parse_sector_from_alpha_vantage(symbol, payload)
        if info.sector or info.industry:
            return info
        return None
    except (AlphaVantageAnalystResponseError, Exception):
        return None


def build_finnhub_profile_url(symbol: str, api_key: str) -> str:
    query = urlencode({"symbol": normalize_symbol_for_finnhub(symbol), "token": api_key})
    return f"https://finnhub.io/api/v1/stock/profile2?{query}"


def fetch_sector_from_finnhub(
    symbol: str,
    api_key: str,
    urlopen_fn=urlopen,
    max_attempts: int = 3,
    base_backoff_seconds: float = 0.5,
    sleep_fn=time.sleep,
) -> SectorInfo | None:
    """Fetch sector/industry from Finnhub company profile. Returns None on error or empty data.

    Finnhub profile2 returns `finnhubIndustry` for the industry/sector classification.
    There is no separate sector field, so sector and industry are set to the same value.
    """
    url = build_finnhub_profile_url(symbol, api_key)
    payload = None
    attempts = max(1, max_attempts)

    for attempt in range(1, attempts + 1):
        try:
            with urlopen_fn(url) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except HTTPError as error:
            if error.code == 403:
                return None  # premium endpoint, no access
            if error.code == 429:
                return None  # rate limited, caller decides retry
            if attempt < attempts:
                time.sleep(base_backoff_seconds * (2 ** (attempt - 1)))
                continue
            return None
        except (URLError, TimeoutError, ConnectionError):
            if attempt < attempts:
                time.sleep(base_backoff_seconds * (2 ** (attempt - 1)))
                continue
            return None

    if not isinstance(payload, dict):
        return None

    finnhub_industry = _clean_str(payload.get("finnhubIndustry"))
    if not finnhub_industry:
        return None

    return SectorInfo(
        symbol=symbol,
        sector=finnhub_industry,
        industry=finnhub_industry,
        sector_source="finnhub",
    )


def fetch_sector_with_fallback(
    symbol: str,
    alpha_vantage_api_key: str,
    finnhub_api_key: str = "",
    fetch_av_fn=fetch_sector_from_alpha_vantage,
    fetch_finnhub_fn=fetch_sector_from_finnhub,
) -> SectorInfo | None:
    """Try AV OVERVIEW first (primary); fall back to Finnhub profile if AV yields nothing."""
    if alpha_vantage_api_key:
        info = fetch_av_fn(symbol, alpha_vantage_api_key)
        if info is not None:
            return info

    if finnhub_api_key:
        return fetch_finnhub_fn(symbol, finnhub_api_key)

    return None
