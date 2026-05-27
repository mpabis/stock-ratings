from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import json
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from stock_rating.db import DatabaseConfig, connect_postgres, is_configured


FRED_SERIES_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
CORE_FRED_SERIES = ("DGS10", "DGS2")


@dataclass(frozen=True)
class MacroObservation:
	series_id: str
	date: date
	value: Decimal
	source: str = "fred"


class FredMacroResponseError(RuntimeError):
	pass


def build_fred_series_observations_url(series_id: str, api_key: str) -> str:
	return f"{FRED_SERIES_BASE_URL}?{urlencode({'series_id': series_id, 'api_key': api_key, 'file_type': 'json'})}"


def fetch_fred_series_observations(series_id: str, api_key: str, urlopen_fn=urlopen) -> dict[str, object]:
	request = Request(build_fred_series_observations_url(series_id, api_key))
	try:
		with urlopen_fn(request) as response:
			return json.loads(response.read().decode("utf-8"))
	except HTTPError as error:
		raise FredMacroResponseError(f"FRED request failed with HTTP {error.code}") from error


def parse_fred_series_observations(series_id: str, payload: dict[str, object]) -> list[MacroObservation]:
	observations = payload.get("observations", []) if isinstance(payload, dict) else []
	if not isinstance(observations, list):
		return []

	parsed: list[MacroObservation] = []
	for observation in observations:
		if not isinstance(observation, dict):
			continue
		raw_value = observation.get("value")
		raw_date = observation.get("date")
		if raw_value in {None, "."} or not raw_date:
			continue
		parsed.append(
			MacroObservation(
				series_id=series_id,
				date=date.fromisoformat(str(raw_date)),
				value=Decimal(str(raw_value)),
			)
		)

	return parsed


def persist_macro_observations(database_url: str, observations: list[MacroObservation], connect_fn=connect_postgres) -> bool:
	if not observations:
		return False
	if not is_configured(DatabaseConfig(url=database_url)):
		return False

	connection = None
	cursor = None
	try:
		connection = connect_fn(database_url)
		cursor = connection.cursor()
		cursor.executemany(
			"""
			insert into macro_series_daily (
				series_id,
				date,
				value,
				source
			) values (%s, %s, %s, %s)
			on conflict (series_id, date, source) do update set
				value = excluded.value
			""",
			[
				(
					observation.series_id,
					observation.date,
					observation.value,
					observation.source,
				)
				for observation in observations
			],
		)
		connection.commit()
		return True
	except Exception:
		return False
	finally:
		try:
			if cursor is not None:
				cursor.close()
			if connection is not None:
				connection.close()
		except Exception:
			pass
