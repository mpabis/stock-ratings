from pathlib import Path

from stock_rating.pipeline.migrate import apply_base_schema, list_migration_files, pending_migration_files


class _FakeSchemaCursor:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, query: str) -> None:
        self.executed.append(query)

    def __enter__(self) -> "_FakeSchemaCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeSchemaConnection:
    def __init__(self) -> None:
        self.cursor_instance = _FakeSchemaCursor()
        self.committed = False

    def cursor(self) -> _FakeSchemaCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True


def test_list_migration_files_sorts_by_name(tmp_path: Path) -> None:
    (tmp_path / "010_last.sql").write_text("select 1;", encoding="utf-8")
    (tmp_path / "002_mid.sql").write_text("select 1;", encoding="utf-8")
    (tmp_path / "001_first.sql").write_text("select 1;", encoding="utf-8")
    (tmp_path / "README.txt").write_text("ignore", encoding="utf-8")

    files = list_migration_files(tmp_path)

    assert [path.name for path in files] == ["001_first.sql", "002_mid.sql", "010_last.sql"]


def test_pending_migration_files_excludes_applied_names(tmp_path: Path) -> None:
    first = tmp_path / "001_first.sql"
    second = tmp_path / "002_second.sql"
    first.write_text("select 1;", encoding="utf-8")
    second.write_text("select 1;", encoding="utf-8")

    pending = pending_migration_files([first, second], {"001_first.sql"})

    assert [path.name for path in pending] == ["002_second.sql"]


def test_apply_base_schema_executes_schema_file(tmp_path: Path) -> None:
    schema_path = tmp_path / "schema.sql"
    schema_path.write_text("create table example(id integer);", encoding="utf-8")
    connection = _FakeSchemaConnection()

    applied = apply_base_schema(connection, schema_path)

    assert applied is True
    assert connection.cursor_instance.executed == ["create table example(id integer);"]
    assert connection.committed is True
