import sqlite3
from pathlib import Path

import duckdb
import pytest

from research_engine.contracts import ComponentState
from research_engine.storage import (
    DUCKDB_FILE,
    SQLITE_FILE,
    MigrationError,
    bundled_migrations,
    check_duckdb,
    check_sqlite,
    load_migrations,
    migrate_duckdb,
    migrate_sqlite,
    open_sqlite,
)


def _migrations(directory: Path, files: dict[str, str]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, sql in files.items():
        (directory / name).write_text(sql, encoding="utf-8")
    return directory


def test_fresh_stores_are_created_migrated_and_queried(tmp_path: Path) -> None:
    sqlite_status = check_sqlite(tmp_path)
    duckdb_status = check_duckdb(tmp_path)
    for status in (sqlite_status, duckdb_status):
        assert status.state is ComponentState.OK, status.summary
        assert status.schema_version == 1
        assert status.initialized_at is not None
        assert status.engine_version
    assert (tmp_path / SQLITE_FILE).is_file()
    assert (tmp_path / DUCKDB_FILE).is_file()


def test_checks_are_idempotent(tmp_path: Path) -> None:
    first = check_sqlite(tmp_path), check_duckdb(tmp_path)
    second = check_sqlite(tmp_path), check_duckdb(tmp_path)
    assert [s.initialized_at for s in first] == [s.initialized_at for s in second]
    with sqlite3.connect(tmp_path / SQLITE_FILE) as conn:
        assert conn.execute("SELECT count(*) FROM schema_migrations").fetchone() == (1,)
    with duckdb.connect(str(tmp_path / DUCKDB_FILE)) as conn:
        assert conn.execute("SELECT count(*) FROM schema_migrations").fetchone() == (1,)


def test_missing_data_dir_is_reported_not_guessed() -> None:
    status = check_sqlite(None)
    assert status.state is ComponentState.UNAVAILABLE
    assert status.path is None


def test_unusable_location_is_an_error(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a directory", encoding="utf-8")
    status = check_duckdb(blocker)
    assert status.state is ComponentState.ERROR
    assert status.error


def test_failed_sqlite_migration_rolls_back(tmp_path: Path) -> None:
    directory = _migrations(
        tmp_path / "m",
        {
            "0001_ok.sql": "CREATE TABLE a (x INTEGER);",
            "0002_broken.sql": "CREATE TABLE b (x INTEGER);\nCREATE TABLE broken (;",
        },
    )
    conn = open_sqlite(tmp_path / "db.sqlite3")
    with pytest.raises(sqlite3.OperationalError):
        migrate_sqlite(conn, load_migrations(directory))
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "a" in tables
    assert "b" not in tables
    assert conn.execute("SELECT version FROM schema_migrations").fetchall() == [(1,)]
    conn.close()


def test_failed_duckdb_migration_rolls_back(tmp_path: Path) -> None:
    directory = _migrations(
        tmp_path / "m",
        {
            "0001_ok.sql": "CREATE TABLE a (x INTEGER);",
            "0002_broken.sql": "CREATE TABLE b (x INTEGER);\nINSERT INTO missing_table VALUES (1);",
        },
    )
    with duckdb.connect(str(tmp_path / "db.duckdb")) as conn:
        with pytest.raises(duckdb.Error):
            migrate_duckdb(conn, load_migrations(directory))
        tables = {
            row[0] for row in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()
        }
        assert "a" in tables
        assert "b" not in tables
        assert conn.execute("SELECT version FROM schema_migrations").fetchall() == [(1,)]


def test_edited_migration_is_detected(tmp_path: Path) -> None:
    directory = _migrations(tmp_path / "m", {"0001_a.sql": "CREATE TABLE a (x INTEGER);"})
    conn = open_sqlite(tmp_path / "db.sqlite3")
    migrate_sqlite(conn, load_migrations(directory))
    (directory / "0001_a.sql").write_text("CREATE TABLE a (y TEXT);", encoding="utf-8")
    with pytest.raises(MigrationError, match="changed after it was applied"):
        migrate_sqlite(conn, load_migrations(directory))
    conn.close()


def test_database_from_a_newer_build_is_refused(tmp_path: Path) -> None:
    directory = _migrations(
        tmp_path / "m",
        {"0001_a.sql": "CREATE TABLE a (x INTEGER);", "0002_b.sql": "CREATE TABLE b (x INTEGER);"},
    )
    conn = open_sqlite(tmp_path / "db.sqlite3")
    migrate_sqlite(conn, load_migrations(directory))
    (directory / "0002_b.sql").unlink()
    with pytest.raises(MigrationError, match="unknown to this build"):
        migrate_sqlite(conn, load_migrations(directory))
    conn.close()


def test_checksums_ignore_line_endings(tmp_path: Path) -> None:
    lf = _migrations(tmp_path / "lf", {"0001_a.sql": "CREATE TABLE a (x INTEGER);\n"})
    crlf = tmp_path / "crlf"
    crlf.mkdir()
    (crlf / "0001_a.sql").write_bytes(b"CREATE TABLE a (x INTEGER);\r\n")
    assert load_migrations(lf)[0].checksum == load_migrations(crlf)[0].checksum


@pytest.mark.parametrize(
    "files",
    [
        {"0001_a.sql": "", "0003_c.sql": ""},  # gap
        {"0002_b.sql": ""},  # does not start at 0001
        {"1_a.sql": ""},  # bad name
    ],
)
def test_migration_sets_must_be_well_formed(tmp_path: Path, files: dict[str, str]) -> None:
    with pytest.raises(MigrationError):
        load_migrations(_migrations(tmp_path / "m", files))


def test_bundled_migrations_are_numbered_from_one() -> None:
    for engine in ("sqlite", "duckdb"):
        versions = [m.version for m in bundled_migrations(engine)]
        assert versions == list(range(1, len(versions) + 1))
        assert versions
