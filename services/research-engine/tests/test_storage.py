import sqlite3
from contextlib import closing
from pathlib import Path

import duckdb
import pytest

from research_engine import repository, testdata
from research_engine.contracts import ComponentState
from research_engine.domain import utc_now
from research_engine.storage import (
    BACKUP_DIR,
    DUCKDB_FILE,
    SQLITE_FILE,
    MigrationError,
    StoreCorruptError,
    backup_sqlite,
    bundled_migrations,
    check_duckdb,
    check_sqlite,
    data_path,
    list_backups,
    load_migrations,
    migrate_duckdb,
    migrate_sqlite,
    open_sqlite,
    relative_data_path,
    restore_sqlite,
    sqlite_problems,
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
        assert status.schema_version == len(bundled_migrations(status.engine))
        assert status.initialized_at is not None
        assert status.engine_version
    assert (tmp_path / SQLITE_FILE).is_file()
    assert (tmp_path / DUCKDB_FILE).is_file()
    assert not (tmp_path / BACKUP_DIR).exists()  # nothing to protect in a brand-new store


def test_checks_are_idempotent(tmp_path: Path) -> None:
    first = check_sqlite(tmp_path), check_duckdb(tmp_path)
    second = check_sqlite(tmp_path), check_duckdb(tmp_path)
    assert [s.initialized_at for s in first] == [s.initialized_at for s in second]
    with closing(sqlite3.connect(tmp_path / SQLITE_FILE)) as conn:
        applied = conn.execute("SELECT count(*) FROM schema_migrations").fetchone()
        assert applied == (len(bundled_migrations("sqlite")),)
    with duckdb.connect(str(tmp_path / DUCKDB_FILE)) as conn:
        applied = conn.execute("SELECT count(*) FROM schema_migrations").fetchone()
        assert applied == (len(bundled_migrations("duckdb")),)


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


# ---- backup, restore and recovery --------------------------------------------------------------


def _count_businesses(path: Path) -> int:
    with closing(sqlite3.connect(path)) as conn:  # sqlite3's own context manager does not close
        return int(conn.execute("SELECT count(*) FROM businesses").fetchone()[0])


def _context(conn: sqlite3.Connection) -> testdata.SyntheticContext:
    project_id, run_id, connector_id = conn.execute(
        "SELECT project_id, id, connector_id FROM source_runs LIMIT 1"
    ).fetchone()
    return testdata.SyntheticContext(project_id, connector_id, run_id, utc_now())


def _seeded_store(tmp_path: Path, businesses: int) -> Path:
    assert check_sqlite(tmp_path).state is ComponentState.OK
    conn = repository.connect(tmp_path)
    try:
        now = utc_now()
        project = testdata.synthetic_project(now)
        connector = testdata.synthetic_connector(now)
        run = testdata.synthetic_run(project.id, connector.id, now)
        repository.insert(conn, project, connector, run)
        ctx = testdata.SyntheticContext(project.id, connector.id, run.id, now)
        testdata.seed_businesses(conn, ctx, businesses)
    finally:
        conn.close()
    return tmp_path / SQLITE_FILE


def test_backup_and_restore_round_trip(tmp_path: Path) -> None:
    store = _seeded_store(tmp_path, 3)
    conn = open_sqlite(store)
    try:
        backup = backup_sqlite(conn, tmp_path / BACKUP_DIR, "manual")  # online, while open
    finally:
        conn.close()
    assert sqlite_problems(backup) == []
    assert list_backups(tmp_path / BACKUP_DIR, "sqlite") == [backup]

    conn = open_sqlite(store)
    try:  # diverge from the backup with more data
        repository.insert(conn, *testdata.synthetic_business(_context(conn), 50))
    finally:
        conn.close()
    assert _count_businesses(store) == 4

    moved = restore_sqlite(backup, store)
    assert moved is not None
    assert moved.is_file()  # the replaced file is kept, never deleted
    assert _count_businesses(store) == 3
    assert sqlite_problems(store) == []


def test_upgrading_an_existing_store_backs_it_up_first(tmp_path: Path) -> None:
    conn = open_sqlite(tmp_path / SQLITE_FILE)
    migrate_sqlite(conn, bundled_migrations("sqlite")[:1])  # an install from phase 01
    conn.close()
    duck = duckdb.connect(str(tmp_path / DUCKDB_FILE))
    migrate_duckdb(duck, bundled_migrations("duckdb")[:1])
    duck.close()

    assert check_sqlite(tmp_path).state is ComponentState.OK
    assert check_duckdb(tmp_path).state is ComponentState.OK
    (sqlite_backup,) = list_backups(tmp_path / BACKUP_DIR, "sqlite")
    (duckdb_backup,) = list_backups(tmp_path / BACKUP_DIR, "duckdb")
    assert sqlite_backup.name.endswith("-before-v0002.sqlite3")
    with closing(sqlite3.connect(sqlite_backup)) as old:
        assert old.execute("SELECT max(version) FROM schema_migrations").fetchone() == (1,)
    with duckdb.connect(str(duckdb_backup), read_only=True) as old_duck:
        assert old_duck.execute("SELECT max(version) FROM schema_migrations").fetchone() == (1,)


def test_a_failed_migration_keeps_the_previous_version_and_its_backup(tmp_path: Path) -> None:
    directory = _migrations(tmp_path / "m", {"0001_ok.sql": "CREATE TABLE a (x INTEGER);"})
    conn = open_sqlite(tmp_path / "db.sqlite3")
    migrate_sqlite(conn, load_migrations(directory))
    conn.execute("INSERT INTO a VALUES (42)")
    (directory / "0002_broken.sql").write_text("DROP TABLE a;\nCREATE TABLE broken (;", "utf-8")
    with pytest.raises(sqlite3.OperationalError):
        migrate_sqlite(conn, load_migrations(directory), tmp_path / BACKUP_DIR)
    assert conn.execute("SELECT x FROM a").fetchall() == [(42,)]  # DROP rolled back
    assert conn.execute("SELECT max(version) FROM schema_migrations").fetchone() == (1,)
    conn.close()
    (backup,) = list_backups(tmp_path / BACKUP_DIR, "sqlite")
    assert sqlite_problems(backup) == []


def test_a_damaged_store_is_recovered_from_the_newest_backup(tmp_path: Path) -> None:
    store = _seeded_store(tmp_path, 4)
    conn = open_sqlite(store)
    try:
        backup_sqlite(conn, tmp_path / BACKUP_DIR, "manual")
    finally:
        conn.close()
    store.write_bytes(b"this is not a database" * 500)

    status = check_sqlite(tmp_path)
    assert status.state is ComponentState.DEGRADED, status.summary
    assert status.error is not None
    assert "recovered from backup" in status.error
    assert _count_businesses(store) == 4
    assert len(list(tmp_path.glob(f"{SQLITE_FILE}.corrupt-*"))) == 1


def test_a_damaged_store_without_backup_is_reported_and_left_untouched(tmp_path: Path) -> None:
    store = tmp_path / SQLITE_FILE
    garbage = b"this is not a database" * 500
    store.write_bytes(garbage)
    status = check_sqlite(tmp_path)
    assert status.state is ComponentState.ERROR
    assert "no usable backup" in (status.error or "")
    assert store.read_bytes() == garbage
    with pytest.raises(StoreCorruptError):
        restore_sqlite(store, tmp_path / "copy.sqlite3")  # a damaged file is never used as backup


def test_a_damaged_analytics_store_is_quarantined_and_rebuilt(tmp_path: Path) -> None:
    (tmp_path / DUCKDB_FILE).write_bytes(b"not duckdb" * 1000)
    status = check_duckdb(tmp_path)
    assert status.state is ComponentState.DEGRADED, status.summary
    assert "empty analytics store" in (status.error or "")
    assert len(list(tmp_path.glob(f"{DUCKDB_FILE}.corrupt-*"))) == 1
    assert check_duckdb(tmp_path).state is ComponentState.OK


@pytest.mark.parametrize(
    "relative", ["", "../outside", "a/../../b", "C:/absolute", "/root", r"a\b", r"..\x"]
)
def test_data_paths_cannot_escape_the_data_directory(tmp_path: Path, relative: str) -> None:
    with pytest.raises(ValueError, match="path"):
        data_path(tmp_path, relative)


def test_data_paths_are_portable(tmp_path: Path) -> None:
    target = data_path(tmp_path, "datasets/abc/snapshot")
    assert target == (tmp_path / "datasets" / "abc" / "snapshot").resolve()
    assert relative_data_path(tmp_path, target) == "datasets/abc/snapshot"
