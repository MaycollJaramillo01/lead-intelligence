"""Repositories: transactions, audited changes, keyset pagination, search, provenance, lineage,
contract round-trips, SQLite locking and a performance check on a synthetic dataset."""

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from pydantic import BaseModel

from research_engine import repository
from research_engine.domain import (
    AuditEntry,
    Business,
    BusinessDetail,
    BusinessListQuery,
    BusinessName,
    BusinessPage,
    BusinessSort,
    BusinessStatus,
    DatasetListQuery,
    DatasetPage,
    LeadStage,
    SyntheticSeedResult,
    new_id,
    sort_key,
)
from research_engine.export_contracts import DEFAULT_OUT
from research_engine.storage import SQLITE_FILE
from research_engine.testdata import SyntheticContext, seed_businesses, synthetic_business


def _query(**changes: object) -> BusinessListQuery:
    base = {
        "project_id": None,
        "search": None,
        "status": None,
        "latino_status": None,
        "latino_min_confidence": None,
        "latino_source_kind": None,
        "latino_review": None,
        "sort": BusinessSort.NAME,
        "limit": 50,
        "cursor": None,
    }
    return BusinessListQuery.model_validate(base | changes)


def _all_pages(conn: sqlite3.Connection, query: BusinessListQuery) -> list[str]:
    ids: list[str] = []
    while True:
        page = repository.list_businesses(conn, query)
        ids += [item.id for item in page.items]
        if page.next_cursor is None:
            return ids
        query = query.model_copy(update={"cursor": page.next_cursor})


def _audit(conn: sqlite3.Connection, entity_id: str) -> list[AuditEntry]:
    rows = conn.execute(
        "SELECT * FROM audit_log WHERE entity_id = ? ORDER BY id", (entity_id,)
    ).fetchall()
    return [repository.from_row(AuditEntry, row) for row in rows]


def test_records_round_trip_through_the_store(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    records = synthetic_business(synthetic, 7)
    repository.insert(conn, *records)
    for record in records:
        assert repository.get(conn, type(record), record.id) == record  # type: ignore[attr-defined]


def test_a_failed_operation_rolls_back_every_statement(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    good = synthetic_business(synthetic, 1)
    duplicate = synthetic_business(synthetic, 2)
    duplicate[0] = good[0]  # reuses a raw record id: fails after the first bundle was written

    def write_both() -> None:
        with repository.atomic(conn):
            repository.insert(conn, *good)
            repository.insert(conn, *duplicate)

    with pytest.raises(sqlite3.IntegrityError):
        write_both()
    for table in ("raw_records", "businesses", "business_names", "evidence_items"):
        count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608
        assert count == 0, table

    def write_duplicate() -> None:
        with repository.atomic(conn):
            repository.insert(conn, *duplicate)

    with repository.atomic(conn):  # a failing savepoint does not undo the outer transaction
        repository.insert(conn, *good)
        with pytest.raises(sqlite3.IntegrityError):
            write_duplicate()
    assert conn.execute("SELECT count(*) FROM businesses").fetchone()[0] == 1


def test_status_changes_follow_the_machine_and_are_audited(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    (business_id,) = seed_businesses(conn, synthetic, 1)
    repository.change_status(conn, Business, business_id, BusinessStatus.ACTIVE, actor="analyst")
    repository.change_status(conn, Business, business_id, LeadStage.ATTEMPTED, actor="analyst")
    with pytest.raises(repository.InvalidTransitionError):
        repository.change_status(conn, Business, business_id, LeadStage.WON, actor="analyst")
    business = repository.get(conn, Business, business_id)
    assert (business.status, business.lead_stage) == (BusinessStatus.ACTIVE, LeadStage.ATTEMPTED)
    trail = _audit(conn, business_id)
    assert [entry.detail_json for entry in trail] == [
        {"field": "status", "from": "candidate", "to": "active", "reason": None},
        {"field": "lead_stage", "from": "not_contacted", "to": "attempted", "reason": None},
    ]
    assert {entry.actor for entry in trail} == {"analyst"}


def test_soft_delete_is_recoverable_and_audited(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    first, second = seed_businesses(conn, synthetic, 2)
    with pytest.raises(ValueError, match="reason"):
        repository.soft_delete(conn, Business, first, actor="analyst", reason=" ")
    repository.soft_delete(conn, Business, first, actor="analyst", reason="duplicate listing")
    assert _all_pages(conn, _query()) == [second]
    with pytest.raises(repository.NotFoundError):
        repository.get_business_detail(conn, first)
    with pytest.raises(repository.NotFoundError):  # already deleted
        repository.soft_delete(conn, Business, first, actor="analyst", reason="again")

    repository.restore(conn, Business, first, actor="analyst")
    assert sorted(_all_pages(conn, _query())) == sorted([first, second])
    actions = [(entry.action, entry.detail_json) for entry in _audit(conn, first)]
    assert actions == [("soft_delete", {"reason": "duplicate listing"}), ("restore", None)]


def test_keyset_pages_visit_every_row_exactly_once(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    seed_businesses(conn, synthetic, 130)
    by_name = _all_pages(conn, _query(limit=17))
    expected = [
        row[0] for row in conn.execute("SELECT id FROM businesses ORDER BY name_sort_key, id")
    ]
    assert by_name == expected
    by_last_seen = _all_pages(conn, _query(limit=40, sort=BusinessSort.LAST_SEEN))
    assert sorted(by_last_seen) == sorted(expected)

    first = repository.list_businesses(conn, _query(limit=50))
    assert (len(first.items), first.total) == (50, 130)
    # rows inserted between two requests never make existing rows repeat or go missing
    seed_businesses(conn, synthetic, 10, start=500)
    rest = _all_pages(conn, _query(limit=50, cursor=first.next_cursor))
    seen = [item.id for item in first.items] + rest
    assert len(seen) == len(set(seen))
    assert set(expected) <= set(seen)


def test_search_matches_names_and_places_without_accents(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    seed_businesses(conn, synthetic, 12)
    records = synthetic_business(synthetic, 99)
    business = next(r for r in records if isinstance(r, Business))
    renamed = business.model_copy(
        update={"display_name": "Panadería Synthetic", "name_sort_key": sort_key("Panadería")}
    )
    repository.insert(conn, *[renamed if r is business else r for r in records])

    def found(search: str) -> set[str]:
        return {
            item.display_name
            for item in repository.list_businesses(conn, _query(search=search)).items
        }

    assert found("panaderia") == {"Panadería Synthetic"}
    assert found("PANADER") == {"Panadería Synthetic"}  # prefix, case-insensitive
    assert found("Landscaping 0099") == {"Panadería Synthetic"}  # name as the source listed it
    assert len(found("miami")) == 2  # place, from the location observation
    assert found('"; DROP TABLE businesses; --') == set()  # FTS syntax never reaches the index
    assert repository.list_businesses(conn, _query(search="!!!")).total == 13  # no words, no filter


def test_status_and_project_filters(conn: sqlite3.Connection, synthetic: SyntheticContext) -> None:
    ids = seed_businesses(conn, synthetic, 5)
    repository.change_status(conn, Business, ids[0], BusinessStatus.ACTIVE, actor="test")
    assert _all_pages(conn, _query(status=BusinessStatus.ACTIVE)) == [ids[0]]
    assert len(_all_pages(conn, _query(project_id=synthetic.project_id))) == 5
    assert _all_pages(conn, _query(project_id=new_id())) == []


@pytest.mark.parametrize(
    "cursor",
    ["garbage", "e30", repository.encode_cursor("last_seen", "x", new_id())],
)
def test_foreign_or_tampered_cursors_are_rejected(conn: sqlite3.Connection, cursor: str) -> None:
    with pytest.raises(repository.InvalidCursorError):
        repository.list_businesses(conn, _query(cursor=cursor))


def test_business_detail_carries_provenance_for_every_value(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    (business_id,) = seed_businesses(conn, synthetic, 1)
    detail = repository.get_business_detail(conn, business_id)
    assert detail.is_synthetic
    groups = (
        detail.names,
        detail.locations,
        detail.websites,
        detail.contacts,
        detail.classifications,
        detail.evidence,
    )
    for group in groups:
        (observed,) = group
        assert observed.provenance.connector_key == "synthetic.fixture"
        assert observed.provenance.source_run_id == synthetic.source_run_id
        assert observed.provenance.extraction_id == observed.value.extraction_id
    assert detail.evidence[0].value.snippet_sha256 is not None


def test_lineage_reaches_the_raw_record_of_a_normalized_value(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    (business_id,) = seed_businesses(conn, synthetic, 1)
    row = conn.execute("SELECT * FROM business_names WHERE business_id = ?", (business_id,))
    name = repository.from_row(BusinessName, row.fetchone())
    assert name.name_normalized != name.name_raw  # normalised value, raw value kept next to it

    lineage = repository.trace_lineage(conn, name.extraction_id)
    assert lineage.raw_record.payload_json["name"] == name.name_raw
    assert lineage.raw_record.payload_json["test_data"] is True
    assert lineage.source_run.id == synthetic.source_run_id
    assert lineage.connector.key == "synthetic.fixture"
    assert lineage.extraction.extractor_version == "1.0.0"


FIXTURE_MODELS: dict[str, type[BaseModel]] = {
    "business_list_query.json": BusinessListQuery,
    "business_page.json": BusinessPage,
    "business_detail.json": BusinessDetail,
    "dataset_list_query.json": DatasetListQuery,
    "dataset_page.json": DatasetPage,
    "synthetic_seed_result.json": SyntheticSeedResult,
}


@pytest.mark.parametrize(("name", "model"), FIXTURE_MODELS.items())
def test_data_contract_fixtures_round_trip(name: str, model: type[BaseModel]) -> None:
    original = json.loads((DEFAULT_OUT / "fixtures" / name).read_text(encoding="utf-8"))
    assert json.loads(model.model_validate_json(json.dumps(original)).model_dump_json()) == original


def test_sqlite_locking_blocks_writers_but_not_readers(
    data_dir: Path, conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    seed_businesses(conn, synthetic, 1)
    writer = repository.connect(data_dir)
    impatient = sqlite3.connect(data_dir / SQLITE_FILE, timeout=0.1, autocommit=True)
    reader = repository.connect(data_dir)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE businesses SET display_name = 'Synthetic renamed'")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            impatient.execute("BEGIN IMMEDIATE")
        # WAL: readers keep seeing the last committed state while the write is open
        assert reader.execute("SELECT display_name FROM businesses").fetchone()[0] != (
            "Synthetic renamed"
        )
        writer.execute("COMMIT")
        impatient.execute("BEGIN IMMEDIATE")
        impatient.execute("ROLLBACK")
        assert reader.execute("SELECT display_name FROM businesses").fetchone()[0] == (
            "Synthetic renamed"
        )
    finally:
        for connection in (writer, impatient, reader):
            connection.close()


def test_concurrent_writers_all_commit_through_the_busy_timeout(
    data_dir: Path, synthetic: SyntheticContext
) -> None:
    errors: list[BaseException] = []

    def write(worker: int) -> None:
        connection = repository.connect(data_dir)
        try:
            for batch in range(5):
                seed_businesses(connection, synthetic, 5, start=1000 * worker + 10 * batch)
        except BaseException as exc:  # noqa: BLE001 - reported by the assertion below
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=write, args=(worker,)) for worker in range(1, 7)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    check = repository.connect(data_dir)
    try:
        assert check.execute("SELECT count(*) FROM businesses").fetchone()[0] == 150
        assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        check.close()


def test_performance_on_a_synthetic_dataset(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    count = 20_000  # 180,000 rows with the full-text index maintained by triggers
    started = time.perf_counter()
    seed_businesses(conn, synthetic, count)
    seeding = time.perf_counter() - started

    timings: dict[str, float] = {}

    def timed(label: str, query: BusinessListQuery) -> BusinessPage:
        started = time.perf_counter()
        page = repository.list_businesses(conn, query)
        timings[label] = (time.perf_counter() - started) * 1000
        return page

    first = timed("first page", _query(limit=100))
    deep = first
    for _ in range(50):  # walk 5,000 rows deep
        deep = repository.list_businesses(conn, _query(limit=100, cursor=deep.next_cursor))
    timed("page 51", _query(limit=100, cursor=deep.next_cursor))
    searched = timed("search", _query(limit=100, search="bakery austin"))
    timed("last seen", _query(limit=100, sort=BusinessSort.LAST_SEEN))
    started = time.perf_counter()
    repository.get_business_detail(conn, first.items[0].id)
    timings["detail"] = (time.perf_counter() - started) * 1000

    print(
        f"seeded {count} businesses in {seeding:.1f} s; "
        + ", ".join(f"{label} {ms:.1f} ms" for label, ms in timings.items())
    )
    assert first.total == count
    assert searched.total > 0
    assert all("Bakery" in item.display_name for item in searched.items)
    assert count / seeding > 300, f"write throughput {count / seeding:.0f} businesses/s"
    assert max(timings.values()) < 250, timings
