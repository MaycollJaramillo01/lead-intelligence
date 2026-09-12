import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from research_engine import repository
from research_engine.domain import utc_now
from research_engine.storage import check_duckdb, check_sqlite
from research_engine.testdata import (
    SyntheticContext,
    synthetic_connector,
    synthetic_project,
    synthetic_run,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A data directory with both stores created and migrated, exactly as the worker does."""
    for status in (check_sqlite(tmp_path), check_duckdb(tmp_path)):
        assert status.state == "ok", status.summary
    return tmp_path


@pytest.fixture
def conn(data_dir: Path) -> Iterator[sqlite3.Connection]:
    connection = repository.connect(data_dir)
    yield connection
    connection.close()


@pytest.fixture
def synthetic(conn: sqlite3.Connection) -> SyntheticContext:
    """A synthetic project, connector and source run to hang test businesses on."""
    now = utc_now()
    project = synthetic_project(now)
    connector = synthetic_connector(now)
    run = synthetic_run(project.id, connector.id, now)
    repository.insert(conn, project, connector, run)
    return SyntheticContext(project.id, connector.id, run.id, now)
