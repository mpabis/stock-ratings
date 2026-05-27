from dataclasses import dataclass
from datetime import datetime, UTC
from decimal import Decimal
import json
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from stock_rating.db import DatabaseConfig, connect_postgres, is_configured


SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_TICKER_MAPPING_URL = "https://www.sec.gov/files/company_tickers.json"

CORE_SEC_METRIC_CONCEPTS = {
	"revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
	"net_income": ("NetIncomeLoss",),
	"operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
	"assets": ("Assets",),
	"liabilities": ("Liabilities",),
}


@dataclass(frozen=True)
class SecTickerMapping:
	symbol: str
	cik: str
	company_name: str
	exchange: str = ""


@dataclass(frozen=True)
class FundamentalFact:
	cik: str
	symbol: str
	fiscal_period: str
	fiscal_year: int
	form: str
	metric: str
	value: Decimal
	unit: str
	filed_at: datetime | None
	source: str = "sec_edgar"


class SecCompanyFactsResponseError(RuntimeError):
	pass


def normalize_symbol_for_sec(symbol: str) -> str:
	normalized = symbol.split(":", 1)[-1]
	normalized = normalized.replace(".", "-")
	return normalized.upper()


def build_sec_company_facts_url(cik: str) -> str:
	return SEC_COMPANY_FACTS_URL.format(cik=str(cik).zfill(10))


def fetch_sec_ticker_mapping(user_agent: str, urlopen_fn=urlopen) -> dict[str, SecTickerMapping]:
	payload = _fetch_json(SEC_TICKER_MAPPING_URL, user_agent, urlopen_fn=urlopen_fn)
	return parse_sec_ticker_mapping(payload)


def fetch_sec_company_facts(cik: str, user_agent: str, urlopen_fn=urlopen) -> dict[str, object]:
	return _fetch_json(build_sec_company_facts_url(cik), user_agent, urlopen_fn=urlopen_fn)


def parse_sec_ticker_mapping(payload: object) -> dict[str, SecTickerMapping]:
	if isinstance(payload, dict):
		raw_rows = payload.values()
	elif isinstance(payload, list):
		raw_rows = payload
	else:
		return {}

	mappings: dict[str, SecTickerMapping] = {}
	for row in raw_rows:
		if not isinstance(row, dict):
			continue
		raw_symbol = str(row.get("ticker", "")).strip()
		cik_value = row.get("cik_str", row.get("cik"))
		if not raw_symbol or cik_value in {None, ""}:
			continue
		normalized_symbol = normalize_symbol_for_sec(raw_symbol)
		mappings[normalized_symbol] = SecTickerMapping(
			symbol=normalized_symbol,
			cik=str(cik_value).zfill(10),
			company_name=str(row.get("title", "")).strip() or normalized_symbol,
		)

	return mappings


def parse_company_facts(symbol: str, cik: str, payload: dict[str, object]) -> list[FundamentalFact]:
	facts = payload.get("facts", {}) if isinstance(payload, dict) else {}
	gaap_facts = facts.get("us-gaap", {}) if isinstance(facts, dict) else {}
	if not isinstance(gaap_facts, dict):
		return []

	parsed_facts: list[FundamentalFact] = []
	for metric, concepts in CORE_SEC_METRIC_CONCEPTS.items():
		observation = _latest_observation(gaap_facts, concepts)
		if observation is None:
			continue
		parsed_facts.append(
			FundamentalFact(
				cik=str(cik).zfill(10),
				symbol=symbol,
				fiscal_period=str(observation.get("fp", "FY")),
				fiscal_year=int(observation["fy"]),
				form=str(observation.get("form", "")),
				metric=metric,
				value=Decimal(str(observation["val"])),
				unit=str(observation.get("unit", "USD")),
				filed_at=_parse_sec_datetime(observation.get("filed")),
			)
		)

	return parsed_facts


def persist_fundamental_facts(database_url: str, facts: list[FundamentalFact], connect_fn=connect_postgres) -> bool:
	if not facts:
		return False
	if not is_configured(DatabaseConfig(url=database_url)):
		return False

	try:
		connection = connect_fn(database_url)
		cursor = connection.cursor()
		cursor.executemany(
			"""
			insert into fundamental_facts (
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
			) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
			on conflict (symbol, fiscal_year, fiscal_period, metric, source) do update set
				cik = excluded.cik,
				form = excluded.form,
				value = excluded.value,
				unit = excluded.unit,
				filed_at = excluded.filed_at
			""",
			[
				(
					fact.cik,
					fact.symbol,
					fact.fiscal_period,
					fact.fiscal_year,
					fact.form,
					fact.metric,
					fact.value,
					fact.unit,
					fact.filed_at,
					fact.source,
				)
				for fact in facts
			],
		)
		connection.commit()
		return True
	except Exception:
		return False
	finally:
		try:
			cursor.close()
			connection.close()
		except Exception:
			pass


def _fetch_json(url: str, user_agent: str, urlopen_fn=urlopen) -> dict[str, object]:
	request = Request(url, headers={"User-Agent": user_agent})
	try:
		with urlopen_fn(request) as response:
			return json.loads(response.read().decode("utf-8"))
	except HTTPError as error:
		if error.code == 403:
			raise SecCompanyFactsResponseError(
				"SEC EDGAR rejected the request with HTTP 403. Set SEC_USER_AGENT to a descriptive value with contact information."
			) from error
		raise SecCompanyFactsResponseError(f"SEC EDGAR request failed with HTTP {error.code}") from error


def _latest_observation(gaap_facts: dict[str, object], concepts: tuple[str, ...]) -> dict[str, object] | None:
	candidates: list[dict[str, object]] = []
	for concept in concepts:
		concept_payload = gaap_facts.get(concept)
		if not isinstance(concept_payload, dict):
			continue
		units = concept_payload.get("units", {})
		if not isinstance(units, dict):
			continue
		for unit, observations in units.items():
			if not isinstance(observations, list):
				continue
			for observation in observations:
				if not isinstance(observation, dict):
					continue
				value = observation.get("val")
				fiscal_year = observation.get("fy")
				if value is None or fiscal_year in {None, ""}:
					continue
				if not isinstance(value, (int, float, str)):
					continue
				candidates.append({**observation, "unit": unit})

	if not candidates:
		return None

	return max(candidates, key=_observation_sort_key)


def _observation_sort_key(observation: dict[str, object]) -> tuple[datetime, int, int]:
	filed_at = _parse_sec_datetime(observation.get("filed")) or datetime(1970, 1, 1, tzinfo=UTC)
	fiscal_year = int(observation.get("fy", 0) or 0)
	form_priority = 1 if str(observation.get("form", "")).startswith("10-K") or str(observation.get("form", "")).startswith("20-F") else 0
	return (filed_at, fiscal_year, form_priority)


def _parse_sec_datetime(value: object) -> datetime | None:
	if not value:
		return None
	text = str(value)
	if len(text) == 10:
		return datetime.fromisoformat(text).replace(tzinfo=UTC)
	return datetime.fromisoformat(text.replace("Z", "+00:00"))
