"""Local stores: SQLite (operational state) and DuckDB (analytics).

Both are versioned by forward-only SQL migrations shipped in `research_engine/migrations/<engine>`.
Applied migrations are recorded with a checksum; editing an applied migration is an error.

Safety net (docs/data-model.md, "Backup and recovery"):
- an existing store is backed up before pending migrations are applied;
- a failed migration rolls back its transaction and leaves the store on the previous version;
- a corrupt store is quarantined (renamed, never deleted) and replaced by the newest backup that
  passes an integrity check. SQLite without a usable backup stays untouched and reports an error;
  DuckDB, whose rows are derived and also kept as Parquet, starts empty instead.
"""

import hashlib
import logging
import re
import shutil
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path, PurePosixPath
from typing import Literal

import duckdb

from research_engine.contracts import ComponentState, DatabaseStatus
from research_engine.logs import utc_now_iso

logger = logging.getLogger(__name__)

Engine = Literal["sqlite", "duckdb"]
SQLITE_FILE = "lead.sqlite3"
DUCKDB_FILE = "analytics.duckdb"
BACKUP_DIR = "backups"
KEEP_BACKUPS = 10
_MIGRATION_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
_REASON = re.compile(r"^[a-z0-9-]{1,40}$")
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


class StoreCorruptError(RuntimeError):
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


# ---- paths -------------------------------------------------------------------------------------


def data_path(data_dir: Path, relative: str) -> Path:
    """Resolves a stored relative path (forward slashes) and refuses anything outside `data_dir`."""
    posix = PurePosixPath(relative)
    root = data_dir.resolve()
    candidate = root.joinpath(*posix.parts).resolve()
    if (
        not relative
        or "\\" in relative
        or ":" in relative
        or posix.is_absolute()
        or ".." in posix.parts
    ):
        raise ValueError(f"not a portable relative path: {relative!r}")
    if not candidate.is_relative_to(root):
        raise ValueError(f"path escapes the data directory: {relative!r}")
    return candidate


def relative_data_path(data_dir: Path, path: Path) -> str:
    """The portable form stored in the database: relative to `data_dir`, forward slashes."""
    return path.resolve().relative_to(data_dir.resolve()).as_posix()


# ---- backups -----------------------------------------------------------------------------------


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _backup_target(backup_dir: Path, prefix: str, suffix: str, reason: str) -> Path:
    if not _REASON.match(reason):
        raise ValueError(f"backup reason must be a short slug, got {reason!r}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir / f"{prefix}-{_stamp()}-{reason}{suffix}"


def _prune(backup_dir: Path, pattern: str) -> None:
    # ponytail: count-based retention; add age/size limits when backups get large
    for old in sorted(backup_dir.glob(pattern), reverse=True)[KEEP_BACKUPS:]:
        old.unlink()
        logger.info("old backup removed", extra={"backup": old.name})


def list_backups(backup_dir: Path, engine: Engine) -> list[Path]:
    """Newest first (the timestamp in the name sorts chronologically)."""
    pattern = "lead-*.sqlite3" if engine == "sqlite" else "analytics-*.duckdb"
    return sorted(backup_dir.glob(pattern), reverse=True) if backup_dir.is_dir() else []


def quarantine(path: Path, sidecars: tuple[str, ...]) -> Path | None:
    """Moves a store and its journal files aside. Nothing is ever deleted."""
    if not path.exists():
        return None
    moved = path.with_name(f"{path.name}.corrupt-{_stamp()}")
    path.replace(moved)
    for sidecar in sidecars:
        extra = path.with_name(path.name + sidecar)
        if extra.exists():
            extra.replace(moved.with_name(moved.name + sidecar))
    logger.warning("store quarantined", extra={"store": path.name, "moved_to": moved.name})
    return moved


def sqlite_problems(path: Path, *, quick: bool = False) -> list[str]:
    """Empty when the file passes `integrity_check` and `foreign_key_check`.

    `quick` runs only `quick_check` (linear, skips index and foreign key verification): cheap
    enough for every health check. Backups always get the full verification.
    """
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return [str(exc)]
    try:
        rows = conn.execute(f"PRAGMA {'quick_check' if quick else 'integrity_check'}").fetchall()
        problems = [] if rows == [("ok",)] else [str(row[0]) for row in rows]
        if not quick:
            problems += [
                f"foreign key violation: {table} row {rowid} -> {parent}"
                for table, rowid, parent, _ in conn.execute("PRAGMA foreign_key_check")
            ]
        return problems
    except sqlite3.DatabaseError as exc:
        return [str(exc)]
    finally:
        conn.close()


def backup_sqlite(conn: sqlite3.Connection, backup_dir: Path, reason: str) -> Path:
    """Online backup through SQLite's backup API, verified before it counts as a backup."""
    target = _backup_target(backup_dir, "lead", ".sqlite3", reason)
    partial = target.with_name(target.name + ".partial")
    destination = sqlite3.connect(partial)
    try:
        conn.backup(destination)
    finally:
        destination.close()
    if problems := sqlite_problems(partial):
        partial.unlink()
        raise StoreCorruptError(f"backup failed verification: {problems[0]}")
    partial.replace(target)
    _prune(backup_dir, "lead-*.sqlite3")
    logger.info("sqlite backup written", extra={"backup": target.name, "reason": reason})
    return target


def restore_sqlite(backup: Path, target: Path) -> Path | None:
    """Replaces `target` with a verified backup; returns where the previous file was moved."""
    if problems := sqlite_problems(backup):
        raise StoreCorruptError(f"backup {backup.name} is not usable: {problems[0]}")
    moved = quarantine(target, ("-wal", "-shm"))
    source = sqlite3.connect(f"{backup.resolve().as_uri()}?mode=ro", uri=True)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    logger.warning("sqlite store restored", extra={"backup": backup.name})
    return moved


def backup_duckdb(conn: duckdb.DuckDBPyConnection, backup_dir: Path, reason: str) -> Path:
    target = _backup_target(backup_dir, "analytics", ".duckdb", reason)
    partial = target.with_name(target.name + ".partial")
    row = conn.execute("SELECT current_database()").fetchone()
    name = str(row[0]).replace('"', '""') if row else "memory"
    conn.execute(f"ATTACH '{_sql_literal(partial)}' AS lead_backup")
    try:
        conn.execute(f'COPY FROM DATABASE "{name}" TO lead_backup')
    finally:
        conn.execute("DETACH lead_backup")
    partial.replace(target)
    _prune(backup_dir, "analytics-*.duckdb")
    logger.info("duckdb backup written", extra={"backup": target.name, "reason": reason})
    return target


def restore_duckdb(backup: Path, target: Path) -> Path | None:
    probe = duckdb.connect(str(backup), read_only=True)
    try:
        probe.execute("SELECT count(*) FROM schema_migrations").fetchone()
    finally:
        probe.close()
    moved = quarantine(target, (".wal",))
    shutil.copyfile(backup, target)
    logger.warning("duckdb store restored", extra={"backup": backup.name})
    return moved


def _sql_literal(path: Path) -> str:
    return str(path).replace("'", "''")


# ---- migrations --------------------------------------------------------------------------------


def open_sqlite(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, autocommit=True, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    # The worker opens a short-lived connection per request: large indexes (a Lead Explorer run of a
    # million leads) are read through the OS page cache, and a sort stays in memory instead of
    # spilling to temporary files (an unindexed sort at offset 500 000: 11 s spilled, 1.6 s here).
    # ponytail: a sort over a run of N leads may hold about 100 bytes per lead in memory; move
    # temp_store back to files if runs of tens of millions of leads appear.
    conn.execute("PRAGMA mmap_size=268435456")
    conn.execute("PRAGMA cache_size=-65536")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def migrate_sqlite(
    conn: sqlite3.Connection, migrations: list[Migration], backup_dir: Path | None = None
) -> int:
    """Applies pending migrations, one transaction each. Returns the schema version."""
    conn.execute(_BOOKKEEPING)
    applied = dict(conn.execute("SELECT version, checksum FROM schema_migrations").fetchall())
    pending = _pending(applied, migrations)
    if pending and applied and backup_dir is not None:
        backup_sqlite(conn, backup_dir, f"before-v{pending[0].version:04d}")
    for migration in pending:
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


def migrate_duckdb(
    conn: duckdb.DuckDBPyConnection, migrations: list[Migration], backup_dir: Path | None = None
) -> int:
    """Applies pending migrations, one transaction each. Returns the schema version."""
    conn.execute(_BOOKKEEPING)
    applied = dict(conn.execute("SELECT version, checksum FROM schema_migrations").fetchall())
    pending = _pending(applied, migrations)
    if pending and applied and backup_dir is not None:
        backup_duckdb(conn, backup_dir, f"before-v{pending[0].version:04d}")
    for migration in pending:
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


# ---- health checks -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Inspection:
    engine_version: str
    schema_version: int
    initialized_at: str
    warning: str | None = None


def _recover_sqlite(path: Path, problems: list[str]) -> str:
    for backup in list_backups(path.parent / BACKUP_DIR, "sqlite"):
        try:
            moved = restore_sqlite(backup, path)
        except StoreCorruptError:
            continue
        return (
            f"recovered from backup {backup.name}; the damaged file was kept as "
            f"{moved.name if moved else 'n/a'} ({problems[0]})"
        )
    raise StoreCorruptError(
        f"{path.name} is damaged ({problems[0]}) and no usable backup exists; the file was left "
        "untouched (see docs/operations.md, 'Recover a damaged database')"
    )


def _inspect_sqlite(path: Path) -> _Inspection:
    warning = None
    if path.exists() and path.stat().st_size and (problems := sqlite_problems(path, quick=True)):
        logger.error("sqlite store is damaged", extra={"problems": problems[:5]})
        warning = _recover_sqlite(path, problems)
    conn = open_sqlite(path)
    try:
        schema_version = migrate_sqlite(
            conn, bundled_migrations("sqlite"), path.parent / BACKUP_DIR
        )
        now = utc_now_iso()
        conn.execute(_MARK_INITIALIZED, (now, now))
        (initialized_at,) = conn.execute(
            "SELECT value FROM store_metadata WHERE key = 'initialized_at'"
        ).fetchone()
        return _Inspection(sqlite3.sqlite_version, schema_version, str(initialized_at), warning)
    finally:
        conn.close()


def _connect_duckdb(path: Path) -> tuple[duckdb.DuckDBPyConnection, str | None]:
    try:
        return duckdb.connect(str(path)), None
    except duckdb.IOException as exc:
        if "not a valid DuckDB database file" not in str(exc):
            raise  # e.g. another process holds the lock: never touch the file
        logger.error("duckdb store is damaged", extra={"reason": str(exc)[:300]})
    for backup in list_backups(path.parent / BACKUP_DIR, "duckdb"):
        try:
            moved = restore_duckdb(backup, path)
        except duckdb.Error:
            continue
        note = f"recovered from backup {backup.name}"
        break
    else:
        moved = quarantine(path, (".wal",))
        note = "started an empty analytics store (snapshots remain available as Parquet)"
    kept = f"; the damaged file was kept as {moved.name}" if moved else ""
    return duckdb.connect(str(path)), note + kept


def _inspect_duckdb(path: Path) -> _Inspection:
    conn, warning = _connect_duckdb(path)
    try:
        schema_version = migrate_duckdb(
            conn, bundled_migrations("duckdb"), path.parent / BACKUP_DIR
        )
        now = utc_now_iso()
        conn.execute(_MARK_INITIALIZED, [now, now])
        row = conn.execute(
            "SELECT value FROM store_metadata WHERE key = 'initialized_at'"
        ).fetchone()
        (engine_version,) = conn.execute("SELECT version()").fetchone() or ("unknown",)
        if row is None:
            raise duckdb.Error("store_metadata.initialized_at missing after initialisation")
        return _Inspection(str(engine_version), schema_version, str(row[0]), warning)
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
        found = inspect(path)
    except (OSError, sqlite3.Error, duckdb.Error, MigrationError, StoreCorruptError) as exc:
        logger.exception("%s store check failed", engine, extra={"path": str(path)})
        return status(
            ComponentState.ERROR, f"{engine} store unusable: {exc}", path=str(path), error=str(exc)
        )
    summary = f"{engine} {found.engine_version} · schema v{found.schema_version} · query ok"
    return status(
        ComponentState.DEGRADED if found.warning else ComponentState.OK,
        f"{summary} · {found.warning}" if found.warning else summary,
        path=str(path),
        engine_version=found.engine_version,
        schema_version=found.schema_version,
        initialized_at=found.initialized_at,
        error=found.warning,
    )


def check_sqlite(data_dir: Path | None) -> DatabaseStatus:
    """Opens (creating if needed) and migrates the SQLite store, then runs real queries."""
    return _check("sqlite", data_dir, SQLITE_FILE, _inspect_sqlite)


def check_duckdb(data_dir: Path | None) -> DatabaseStatus:
    """Opens (creating if needed) and migrates the DuckDB store, then runs real queries."""
    return _check("duckdb", data_dir, DUCKDB_FILE, _inspect_duckdb)
