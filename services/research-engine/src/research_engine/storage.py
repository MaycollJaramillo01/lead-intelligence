"""Local stores: SQLite (operational state) and DuckDB (analytics).

Both are versioned by forward-only SQL migrations shipped in `research_engine/migrations/<engine>`.
Applied migrations are recorded with a checksum; editing an applied migration is an error.
"""

import hashlib
import logging
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Literal

import duckdb

from research_engine.contracts import ComponentState, DatabaseStatus
from research_engine.logs import utc_now_iso

logger = logging.getLogger(__name__)

Engine = Literal["sqlite", "duckdb"]
SQLITE_FILE = "lead.sqlite3"
DUCKDB_FILE = "analytics.duckdb"
_MIGRATION_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
_BOOKKEEPING = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
)"""
_MARK_INITIALIZED = (
    "INSERT INTO store_metadata (key, value, updated_at) VALUES ('initialized_at', ?, ?) "
    "ON CONFLICT (key) DO NOTHING"
)


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum: str


def load_migrations(directory: Traversable) -> list[Migration]:
    found: list[Migration] = []
    for entry in directory.iterdir():
        if not entry.name.endswith(".sql"):
            continue
        match = _MIGRATION_NAME.match(entry.name)
        if match is None:
            raise MigrationError(f"invalid migration file name: {entry.name}")
        # normalise line endings so checksums match across checkouts
        raw = entry.read_bytes().replace(b"\r\n", b"\n")
        found.append(
            Migration(int(match[1]), match[2], raw.decode("utf-8"), hashlib.sha256(raw).hexdigest())
        )
    found.sort(key=lambda m: m.version)
    if [m.version for m in found] != list(range(1, len(found) + 1)):
        raise MigrationError(f"migrations in {directory} must be numbered 0001.. without gaps")
    return found


def bundled_migrations(engine: Engine) -> list[Migration]:
    return load_migrations(files("research_engine") / "migrations" / engine)


def _pending(applied: dict[int, str], migrations: list[Migration]) -> list[Migration]:
    known = {m.version: m for m in migrations}
    unknown = sorted(set(applied) - set(known))
    if unknown:
        raise MigrationError(
            f"database contains migrations {unknown} unknown to this build (newer app version?)"
        )
    for version, checksum in applied.items():
        if known[version].checksum != checksum:
            raise MigrationError(
                f"migration {version:04d}_{known[version].name} changed after it was applied"
            )
    return [m for m in migrations if m.version not in applied]


def open_sqlite(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, autocommit=True, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def migrate_sqlite(conn: sqlite3.Connection, migrations: list[Migration]) -> int:
    """Applies pending migrations, one transaction each. Returns the schema version."""
    conn.execute(_BOOKKEEPING)
    applied = dict(conn.execute("SELECT version, checksum FROM schema_migrations").fetchall())
    for migration in _pending(applied, migrations):
        conn.execute("BEGIN IMMEDIATE")
        try:
            # re-check under the write lock: another connection may have applied it meanwhile
            if conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?", (migration.version,)
            ).fetchone():
                conn.execute("COMMIT")
                continue
            conn.executescript(migration.sql)
            conn.execute(
                "INSERT INTO schema_migrations VALUES (?, ?, ?, ?)",
                (migration.version, migration.name, migration.checksum, utc_now_iso()),
            )
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        logger.info(
            "sqlite migration applied",
            extra={"migration": f"{migration.version:04d}_{migration.name}"},
        )
    return len(migrations)


def migrate_duckdb(conn: duckdb.DuckDBPyConnection, migrations: list[Migration]) -> int:
    """Applies pending migrations, one transaction each. Returns the schema version."""
    conn.execute(_BOOKKEEPING)
    applied = dict(conn.execute("SELECT version, checksum FROM schema_migrations").fetchall())
    for migration in _pending(applied, migrations):
        conn.begin()
        try:
            conn.execute(migration.sql)
            conn.execute(
                "INSERT INTO schema_migrations VALUES (?, ?, ?, ?)",
                [migration.version, migration.name, migration.checksum, utc_now_iso()],
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        logger.info(
            "duckdb migration applied",
            extra={"migration": f"{migration.version:04d}_{migration.name}"},
        )
    return len(migrations)


# (engine_version, schema_version, initialized_at)
_Inspection = tuple[str, int, str]


def _inspect_sqlite(path: Path) -> _Inspection:
    conn = open_sqlite(path)
    try:
        schema_version = migrate_sqlite(conn, bundled_migrations("sqlite"))
        now = utc_now_iso()
        conn.execute(_MARK_INITIALIZED, (now, now))
        (initialized_at,) = conn.execute(
            "SELECT value FROM store_metadata WHERE key = 'initialized_at'"
        ).fetchone()
        (integrity,) = conn.execute("PRAGMA quick_check").fetchone()
        if integrity != "ok":
            raise sqlite3.DatabaseError(f"quick_check reported: {integrity}")
        return sqlite3.sqlite_version, schema_version, str(initialized_at)
    finally:
        conn.close()


def _inspect_duckdb(path: Path) -> _Inspection:
    conn = duckdb.connect(str(path))
    try:
        schema_version = migrate_duckdb(conn, bundled_migrations("duckdb"))
        now = utc_now_iso()
        conn.execute(_MARK_INITIALIZED, [now, now])
        row = conn.execute(
            "SELECT value FROM store_metadata WHERE key = 'initialized_at'"
        ).fetchone()
        (engine_version,) = conn.execute("SELECT version()").fetchone() or ("unknown",)
        if row is None:
            raise duckdb.Error("store_metadata.initialized_at missing after initialisation")
        return str(engine_version), schema_version, str(row[0])
    finally:
        conn.close()


def _check(
    engine: Engine, data_dir: Path | None, filename: str, inspect: Callable[[Path], _Inspection]
) -> DatabaseStatus:
    started = time.perf_counter()

    def status(state: ComponentState, summary: str, **fields: object) -> DatabaseStatus:
        values: dict[str, object] = {
            "path": None,
            "engine_version": None,
            "schema_version": None,
            "initialized_at": None,
            "error": None,
        }
        values.update(fields)
        return DatabaseStatus.model_validate(
            {
                "state": state,
                "summary": summary,
                "engine": engine,
                "checked_at": datetime.now(UTC),
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                **values,
            }
        )

    if data_dir is None:
        return status(
            ComponentState.UNAVAILABLE,
            "Data directory not configured (LEAD_DATA_DIR is empty)",
            error="LEAD_DATA_DIR is not set",
        )
    path = data_dir / filename
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        engine_version, schema_version, initialized_at = inspect(path)
    except (OSError, sqlite3.Error, duckdb.Error, MigrationError) as exc:
        logger.exception("%s store check failed", engine, extra={"path": str(path)})
        return status(
            ComponentState.ERROR, f"{engine} store unusable: {exc}", path=str(path), error=str(exc)
        )
    return status(
        ComponentState.OK,
        f"{engine} {engine_version} · schema v{schema_version} · query ok",
        path=str(path),
        engine_version=engine_version,
        schema_version=schema_version,
        initialized_at=initialized_at,
    )


def check_sqlite(data_dir: Path | None) -> DatabaseStatus:
    """Opens (creating if needed) and migrates the SQLite store, then runs real queries."""
    return _check("sqlite", data_dir, SQLITE_FILE, _inspect_sqlite)


def check_duckdb(data_dir: Path | None) -> DatabaseStatus:
    """Opens (creating if needed) and migrates the DuckDB store, then runs real queries."""
    return _check("duckdb", data_dir, DUCKDB_FILE, _inspect_duckdb)
