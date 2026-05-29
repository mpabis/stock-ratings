from pathlib import Path

from stock_rating.pipeline.migrate import list_migration_files, pending_migration_files


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