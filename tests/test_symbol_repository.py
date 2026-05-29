from datetime import UTC, datetime
from pathlib import Path

from stock_rating.repository.runs import build_pipeline_run_record, persist_run_records, write_plan_artifact
from stock_rating.repository.symbols import load_symbol_seeds, upsert_symbol_seeds


def test_load_symbol_seeds_from_csv() -> None:
    seeds = load_symbol_seeds()

    assert len(seeds) >= 5
    assert seeds[0].symbol == "AAPL"
    assert all(seed.active for seed in seeds)


class _FakeSymbolCursor:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.executed: list[str] = []
        self.closed = False

    def execute(self, query: str) -> None:
        self.executed.append(query)

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows

    def close(self) -> None:
        self.closed = True


class _FakeSymbolConnection:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.cursor_instance = _FakeSymbolCursor(rows)
        self.closed = False

    def cursor(self) -> _FakeSymbolCursor:
        return self.cursor_instance

    def close(self) -> None:
        self.closed = True


class _FakeUpsertCursor:
    def __init__(self) -> None:
        self.executemany_calls: list[tuple[str, list[tuple[object, ...]]]] = []
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []
        self.closed = False

    def executemany(self, query: str, params_seq: list[tuple[object, ...]]) -> None:
        self.executemany_calls.append((query, params_seq))

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        self.execute_calls.append((query, params))

    def close(self) -> None:
        self.closed = True


class _FakeUpsertConnection:
    def __init__(self) -> None:
        self.cursor_instance = _FakeUpsertCursor()
        self.committed = False
        self.closed = False

    def cursor(self) -> _FakeUpsertCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


def test_load_symbol_seeds_prefers_database_when_configured() -> None:
    fake_connection = _FakeSymbolConnection(
        rows=[
            (
                "QQQ",
                "Invesco QQQ Trust",
                "NASDAQ",
                1,
                datetime(2026, 5, 26, 10, 0, tzinfo=UTC),
                datetime(2026, 5, 20, 10, 0, tzinfo=UTC),
                True,
            ),
            ("SPY", "SPDR S&P 500 ETF", "NYSEARCA", 2, None, None, True),
        ]
    )

    seeds = load_symbol_seeds(
        database_url="postgresql://example",
        connect_fn=lambda _: fake_connection,
    )

    assert [seed.symbol for seed in seeds] == ["QQQ", "SPY"]
    assert seeds[0].last_price_date.isoformat() == "2026-05-26"
    assert seeds[0].last_fundamental_date.isoformat() == "2026-05-20"
    assert seeds[1].refresh_tier == 2
    assert fake_connection.cursor_instance.closed is True
    assert fake_connection.closed is True


def test_upsert_symbol_seeds_inserts_rows() -> None:
    seeds = load_symbol_seeds()
    fake_connection = _FakeUpsertConnection()

    persisted = upsert_symbol_seeds(
        database_url="postgresql://example",
        seeds=seeds[:2],
        connect_fn=lambda _: fake_connection,
    )

    assert persisted is True
    assert len(fake_connection.cursor_instance.executemany_calls) == 1
    assert len(fake_connection.cursor_instance.execute_calls) == 1
    assert fake_connection.cursor_instance.execute_calls[0][1] == (([seed.symbol for seed in seeds[:2]]),)
    assert fake_connection.committed is True
    assert fake_connection.cursor_instance.closed is True
    assert fake_connection.closed is True


def test_write_plan_artifact_creates_json_file(tmp_path: Path) -> None:
    run_record = build_pipeline_run_record(
        run_id="ddda45d6-d8fa-47c6-8aae-91ab5f50752b",
        started_at=datetime(2026, 5, 27, 22, 30, tzinfo=UTC),
        finished_at=datetime(2026, 5, 27, 22, 31, tzinfo=UTC),
        status="planned",
    )

    artifact_path = write_plan_artifact(str(tmp_path), run_record, [])

    assert artifact_path.exists()
    assert artifact_path.suffix == ".json"


class _FakeCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.executemany_calls: list[tuple[str, list[tuple[object, ...]]]] = []
        self.closed = False

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        self.executed.append((query, params))

    def executemany(self, query: str, params_seq: list[tuple[object, ...]]) -> None:
        self.executemany_calls.append((query, params_seq))

    def close(self) -> None:
        self.closed = True


class _FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = _FakeCursor()
        self.committed = False
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


def test_persist_run_records_inserts_pipeline_and_symbol_runs() -> None:
    run_record = build_pipeline_run_record(
        run_id="ddda45d6-d8fa-47c6-8aae-91ab5f50752b",
        started_at=datetime(2026, 5, 27, 22, 30, tzinfo=UTC),
        finished_at=datetime(2026, 5, 27, 22, 31, tzinfo=UTC),
        status="planned",
    )
    fake_connection = _FakeConnection()

    persisted = persist_run_records(
        database_url="postgresql://example",
        pipeline_run=run_record,
        symbol_runs=[],
        connect_fn=lambda _: fake_connection,
    )

    assert persisted is True
    assert len(fake_connection.cursor_instance.executed) == 1
    assert fake_connection.committed is True
    assert fake_connection.cursor_instance.closed is True
    assert fake_connection.closed is True


def test_persist_run_records_skips_when_database_not_configured() -> None:
    run_record = build_pipeline_run_record(
        run_id="ddda45d6-d8fa-47c6-8aae-91ab5f50752b",
        started_at=datetime(2026, 5, 27, 22, 30, tzinfo=UTC),
        finished_at=datetime(2026, 5, 27, 22, 31, tzinfo=UTC),
        status="planned",
    )

    persisted = persist_run_records(database_url="", pipeline_run=run_record, symbol_runs=[])

    assert persisted is False
