"""Entity resolution against the store: records and observations, pairs, clusters, canonical values,
review decisions, reruns, jobs and scale. Synthetic test data only (see testdata.py)."""

import sqlite3
import time
from pathlib import Path

import pytest
from resolution_corpus import generated

from research_engine import datasets, jobs, repository
from research_engine.domain import (
    Business,
    BusinessListQuery,
    BusinessPage,
    BusinessSort,
    BusinessStatus,
    CanonicalChoiceRequest,
    CanonicalField,
    ContactType,
    DiscoveryControlRequest,
    JobStatus,
    MatchAction,
    MatchActionRequest,
    MatchListQuery,
    MatchPage,
    MatchRisk,
    MatchStatus,
    ResolutionStartRequest,
    RunControlAction,
    new_id,
    utc_now,
)
from research_engine.resolution import engine, store
from research_engine.resolution.normalize import NORMALIZER_VERSION
from research_engine.testdata import (
    SYNTHETIC_RESOLUTION_ROWS,
    SyntheticContext,
    insert_candidates,
    seed_crawl,
    seed_discovery,
    synthetic_connector,
    synthetic_project,
    synthetic_run,
)

pytestmark = pytest.mark.anyio


def resolve(conn: sqlite3.Connection, project_id: str) -> str:
    created, _ = store.plan_runs(conn, project_id, actor="test")
    assert engine.run_resolution(conn, created[0], lambda: False)
    return created[0]


def businesses(
    conn: sqlite3.Connection, project_id: str, search: str | None = None
) -> BusinessPage:
    query = BusinessListQuery(
        project_id=project_id,
        search=search,
        status=None,
        latino_status=None,
        latino_min_confidence=None,
        latino_source_kind=None,
        latino_review=None,
        sort=BusinessSort.NAME,
        limit=500,
        cursor=None,
    )
    return repository.list_businesses(conn, query)


def matches(conn: sqlite3.Connection, project_id: str, **filters: object) -> MatchPage:
    values: dict[str, object] = {
        "project_id": project_id,
        "status": None,
        "risk": None,
        "min_score": None,
        "connector_key": None,
        "limit": 500,
        "cursor": None,
    }
    return store.list_matches(conn, MatchListQuery.model_validate(values | filters))


def plumber(conn: sqlite3.Connection, project_id: str) -> str:
    page = businesses(conn, project_id)
    return next(item.id for item in page.items if "Plumbing 0100" in item.display_name)


def root_of(conn: sqlite3.Connection, business_id: str) -> str:
    business = repository.get(conn, Business, business_id)
    return business.merged_into_id or business.id


def act(conn: sqlite3.Connection, match_id: str, action: MatchAction) -> None:
    request = MatchActionRequest(match_id=match_id, action=action, reason=None)
    store.act_on_match(conn, request, actor="reviewer")


def candidate_rows(entities: int, seed: int) -> list[dict[str, str]]:
    """The generated corpus as the rows an import would hold."""
    rows: list[dict[str, str]] = []
    for item in generated(entities, seed=seed):
        record = item.record
        address = record.addresses[0]
        row = {
            "name": record.names[0],
            "city": address.locality or "",
            "state": address.region or "",
        }
        optional = {
            "address": address.street,
            "zip": address.postal_code,
            "website": record.websites[0] if record.websites else None,
            "phone": record.phones[0] if record.phones else None,
            "category": record.categories[0] if record.categories else None,
        }
        rows.append(row | {key: value for key, value in optional.items() if value})
    return rows


# ---- records, observations and the canonical business ------------------------------------------


def test_candidates_become_records_with_observations_and_canonical_businesses(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    ids = insert_candidates(conn, synthetic, SYNTHETIC_RESOLUTION_ROWS)
    raw_sql = "SELECT id, payload_sha256, payload_json FROM raw_records ORDER BY id"
    raw_before = [tuple(row) for row in conn.execute(raw_sql)]
    job_id = resolve(conn, synthetic.project_id)

    (records,) = conn.execute("SELECT count(*) FROM entity_records").fetchone()
    assert records == len(ids)
    assert [tuple(row) for row in conn.execute(raw_sql)] == raw_before  # raw data untouched

    phone = conn.execute(
        "SELECT c.value_raw, c.value_normalized, c.validation_status, x.extractor, "
        "x.extractor_version FROM contact_points c JOIN extractions x ON x.id = c.extraction_id "
        "WHERE c.value_raw = '512.555.0111'"
    ).fetchone()
    assert tuple(phone) == (
        "512.555.0111",
        "+15125550111",
        "structurally_valid",
        "resolution.candidate",
        NORMALIZER_VERSION,
    )
    site = conn.execute(
        "SELECT url_raw, url_normalized, domain FROM websites WHERE url_raw LIKE 'http://www.%'"
    ).fetchone()
    assert tuple(site) == (
        "http://www.synthetic-plumbing-0100.example/?utm_source=seed",
        "http://synthetic-plumbing-0100.example",
        "synthetic-plumbing-0100.example",
    )
    location = conn.execute(
        "SELECT street, unit, locality, region, postal_code FROM business_locations "
        "WHERE address_raw LIKE '100 Example Avenue%'"
    ).fetchone()
    assert tuple(location) == ("100 Example Ave", "Ste 4", "Austin", "TX", "78701")

    page = businesses(conn, synthetic.project_id)
    assert page.total == len(ids) - 1  # the two plumbing records are one business
    listed = next(item for item in page.items if item.id == plumber(conn, synthetic.project_id))
    assert (listed.member_count, listed.conflict_count >= 1) == (2, True)

    detail = repository.get_business_detail(conn, listed.id)
    assert len(detail.members) == 2
    phones = {
        contact.value.value_normalized
        for contact in detail.contacts
        if contact.value.contact_type is ContactType.PHONE
    }
    assert phones == {"+15125550110", "+15125550111"}  # the conflict stays visible
    chosen = {value.field: value for value in detail.canonical}
    assert chosen[CanonicalField.PHONE].alternative_count == 2
    assert chosen[CanonicalField.NAME].method.value == "policy"
    (merge,) = detail.merges
    assert merge.unmerged_at is None
    assert merge.match_id is not None
    assert merge.explanation_json["reasons"]

    run = store.get_resolution_run(conn, job_id)
    assert run.status is JobStatus.SUCCEEDED
    stats = run.stats
    assert (stats.ingested, stats.clusters, stats.auto_merged, stats.review) == (6, 1, 1, 1)
    (merges,) = conn.execute("SELECT count(*) FROM audit_log WHERE action = 'merge'").fetchone()
    assert merges == 1


def test_a_second_run_changes_nothing(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    insert_candidates(conn, synthetic, SYNTHETIC_RESOLUTION_ROWS)
    resolve(conn, synthetic.project_id)

    def state() -> list[list[tuple[object, ...]]]:
        return [
            [tuple(row) for row in conn.execute(sql)]
            for sql in (
                "SELECT id, status, merged_into_id, display_name, updated_at FROM businesses "
                "ORDER BY id",
                "SELECT * FROM match_candidates ORDER BY id",
                "SELECT * FROM canonical_values ORDER BY id",
                "SELECT * FROM business_merges ORDER BY id",
                "SELECT count(*) FROM business_names UNION ALL SELECT count(*) FROM contact_points",
            )
        ]

    before = state()
    again = resolve(conn, synthetic.project_id)
    assert state() == before
    stats = store.get_resolution_run(conn, again).stats
    assert (stats.ingested, stats.observations, stats.records) == (0, 0, 6)


# ---- review decisions ---------------------------------------------------------------------------


def test_merge_undo_defer_and_keep_separate_are_recorded_and_reversible(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    insert_candidates(conn, synthetic, SYNTHETIC_RESOLUTION_ROWS)
    resolve(conn, synthetic.project_id)
    (pending,) = matches(conn, synthetic.project_id, status=MatchStatus.PENDING).items
    assert pending.risk is MatchRisk.HIGH
    assert "address_changed" in pending.reasons
    total = businesses(conn, synthetic.project_id).total

    act(conn, pending.id, MatchAction.MERGE)
    assert businesses(conn, synthetic.project_id).total == total - 1
    left, right = pending.left.business_id, pending.right.business_id
    root, member = min(left, right), max(left, right)
    assert (root_of(conn, member), root_of(conn, root)) == (root, root)
    detail = store.get_match_detail(conn, pending.id)
    assert (detail.match.status, detail.match.is_manual) == (MatchStatus.MERGED, True)
    assert (detail.decisions[0].decision, detail.decisions[0].actor) == ("merge", "reviewer")
    assert any(
        (merge.business_id, merge.into_business_id, merge.is_manual) == (member, root, True)
        for merge in detail.merges
    )

    act(conn, pending.id, MatchAction.UNDO)
    assert repository.get(conn, Business, member).status is BusinessStatus.CANDIDATE
    history = conn.execute(
        "SELECT unmerged_at, unmerged_by FROM business_merges WHERE business_id = ?", (member,)
    ).fetchall()
    assert len(history) == 1
    assert history[0]["unmerged_by"] == "reviewer"  # closed, never deleted
    assert store.get_match_detail(conn, pending.id).match.status is MatchStatus.PENDING
    audited = conn.execute(
        "SELECT action FROM audit_log WHERE entity_id = ? ORDER BY occurred_at, id", (member,)
    ).fetchall()
    assert [row["action"] for row in audited][-2:] == ["merge", "unmerge"]

    act(conn, pending.id, MatchAction.DEFER)
    with pytest.raises(store.ResolutionError, match="already deferred"):
        act(conn, pending.id, MatchAction.DEFER)
    act(conn, pending.id, MatchAction.KEEP_SEPARATE)
    assert store.get_match_detail(conn, pending.id).match.status is MatchStatus.SEPARATE
    decisions = store.get_match_detail(conn, pending.id).decisions
    assert [decision.decision for decision in decisions] == [
        "keep_separate",
        "defer",
        "undo",
        "merge",
    ]

    (merged,) = matches(conn, synthetic.project_id, status=MatchStatus.MERGED).items
    with pytest.raises(repository.InvalidTransitionError):
        act(conn, merged.id, MatchAction.DEFER)


def test_people_win_over_later_runs(conn: sqlite3.Connection, synthetic: SyntheticContext) -> None:
    insert_candidates(conn, synthetic, SYNTHETIC_RESOLUTION_ROWS)
    resolve(conn, synthetic.project_id)
    (merged,) = matches(conn, synthetic.project_id, status=MatchStatus.MERGED).items
    act(conn, merged.id, MatchAction.KEEP_SEPARATE)
    left, right = merged.left.business_id, merged.right.business_id
    assert root_of(conn, left) != root_of(conn, right)

    # a third record of the same plumber matches both; it may join only one of them
    third = {
        "name": "SYNTHETIC PLUMBING 0100",
        "address": "100 Example Ave Suite 4",
        "city": "Austin",
        "state": "TX",
        "zip": "78701",
        "website": "synthetic-plumbing-0100.example",
        "phone": "(512) 555-0110",
    }
    insert_candidates(conn, synthetic, [third])
    resolve(conn, synthetic.project_id)
    assert root_of(conn, left) != root_of(conn, right)
    kept = store.get_match_detail(conn, merged.id).match
    assert (kept.status, kept.is_manual) == (MatchStatus.SEPARATE, True)
    refused = matches(conn, synthetic.project_id, status=MatchStatus.PENDING).items
    assert any("cluster_conflict" in item.reasons for item in refused)


def test_a_person_chooses_the_value_a_business_shows(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    insert_candidates(conn, synthetic, SYNTHETIC_RESOLUTION_ROWS)
    resolve(conn, synthetic.project_id)
    business_id = plumber(conn, synthetic.project_id)
    detail = repository.get_business_detail(conn, business_id)
    chosen = {value.field: value for value in detail.canonical}[CanonicalField.PHONE]
    other = next(
        contact.value
        for contact in detail.contacts
        if contact.value.contact_type is ContactType.PHONE
        and contact.value.id != chosen.observation_id
    )
    request = CanonicalChoiceRequest(
        business_id=business_id,
        field=CanonicalField.PHONE,
        observation_id=other.id,
        reason=None,
    )
    store.choose_canonical(conn, request, actor="reviewer")

    def current() -> tuple[str, str, str]:
        values = repository.get_business_detail(conn, business_id).canonical
        phone = next(value for value in values if value.field is CanonicalField.PHONE)
        return (phone.observation_id, phone.method.value, phone.actor)

    assert current() == (other.id, "manual", "reviewer")
    listed = next(item for item in businesses(conn, synthetic.project_id).items
                  if item.id == business_id)  # fmt: skip
    assert listed.primary_phone == other.value_normalized
    resolve(conn, synthetic.project_id)  # a later run keeps the person's choice
    assert current() == (other.id, "manual", "reviewer")
    (superseded,) = conn.execute(
        "SELECT count(*) FROM canonical_values WHERE business_id = ? AND field = 'phone' "
        "AND superseded_at IS NOT NULL",
        (business_id,),
    ).fetchone()
    assert superseded == 1

    stranger = next(item.id for item in businesses(conn, synthetic.project_id).items
                    if item.id != business_id)  # fmt: skip
    foreign = repository.get_business_detail(conn, stranger).names[0].value
    with pytest.raises(repository.NotFoundError):
        store.choose_canonical(
            conn, request.model_copy(update={"field": CanonicalField.NAME,
                                             "observation_id": foreign.id}), actor="reviewer"
        )  # fmt: skip
    member = next(member.business_id for member in detail.members
                  if member.business_id != business_id)  # fmt: skip
    with pytest.raises(store.ResolutionError, match="belongs to another business"):
        store.choose_canonical(
            conn, request.model_copy(update={"business_id": member}), actor="reviewer"
        )


# ---- crawled sites, the queue and search ---------------------------------------------------------


def test_contacts_published_on_the_crawled_site_are_observed(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    seeded = seed_discovery(conn, synthetic)
    context = SyntheticContext(synthetic.project_id, "", seeded.source_run_id, synthetic.now)
    seed_crawl(conn, context, seeded.campaign_id, seeded.candidate_ids[0])
    resolve(conn, synthetic.project_id)
    (business_id,) = conn.execute(
        "SELECT business_id FROM entity_records WHERE candidate_id = ?",
        (seeded.candidate_ids[0],),
    ).fetchone()
    detail = repository.get_business_detail(conn, business_id)
    emails = [c for c in detail.contacts if c.value.contact_type is ContactType.EMAIL]
    assert [email.value.value_normalized for email in emails] == [
        "hello@synthetic-bakery-0001.example"
    ]
    assert emails[0].provenance.extractor == "resolution.site"
    assert "/contact" in (emails[0].value.published_context or "")
    phones = [c for c in detail.contacts if c.value.contact_type is ContactType.PHONE]
    # the home page repeats the listed phone: one observation, from the listing
    assert [phone.value.value_normalized for phone in phones] == ["+15125550101"]


def test_the_review_queue_filters_and_pages(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    insert_candidates(conn, synthetic, SYNTHETIC_RESOLUTION_ROWS)
    seed_discovery(conn, synthetic)
    resolve(conn, synthetic.project_id)
    everything = matches(conn, synthetic.project_id)
    assert everything.total >= 3
    scores = [item.score for item in everything.items]
    assert scores == sorted(scores, reverse=True)

    first = matches(conn, synthetic.project_id, limit=1)
    assert first.next_cursor is not None
    second = matches(conn, synthetic.project_id, limit=1, cursor=first.next_cursor)
    assert second.items[0].id != first.items[0].id

    high = matches(conn, synthetic.project_id, risk=MatchRisk.HIGH).items
    assert high
    assert all(item.risk is MatchRisk.HIGH for item in high)
    assert all(item.score >= 0.9 for item in matches(conn, synthetic.project_id,
                                                      min_score=0.9).items)  # fmt: skip
    assert matches(conn, synthetic.project_id, connector_key="synthetic.fixture").total == (
        everything.total
    )
    assert matches(conn, synthetic.project_id, connector_key="usaspending.recipients").total == 0

    pending = next(item for item in everything.items if "address_changed" in item.reasons)
    detail = store.get_match_detail(conn, pending.id)
    assert {field.field for field in detail.fields} >= {"name", "address", "phone"}
    assert detail.left.business.id == pending.left.business_id
    assert any(reason.direction.value == "rule" for reason in detail.reasons)
    assert detail.blocking_keys


def test_merged_records_are_found_through_their_business(
    conn: sqlite3.Connection, synthetic: SyntheticContext, data_dir: Path
) -> None:
    insert_candidates(conn, synthetic, SYNTHETIC_RESOLUTION_ROWS)
    resolve(conn, synthetic.project_id)
    root = plumber(conn, synthetic.project_id)
    found = businesses(conn, synthetic.project_id, search="Inc")  # only the merged record says Inc
    assert [item.id for item in found.items] == [root]
    snapshot = datasets.snapshot_businesses(
        conn,
        data_dir,
        project_id=synthetic.project_id,
        dataset_name="Synthetic resolution snapshot",
        actor="test",
    )
    assert snapshot.row_count == businesses(conn, synthetic.project_id).total


def test_the_crawler_and_the_resolver_agree_on_the_crawl_connector() -> None:
    from research_engine.crawl.engine import CONNECTOR_KEY

    assert store.CRAWL_CONNECTOR_KEY == CONNECTOR_KEY


# ---- jobs ----------------------------------------------------------------------------------------


def test_an_interrupted_run_resumes_from_its_checkpoint(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    rows = candidate_rows(300, seed=4)
    insert_candidates(conn, synthetic, rows)
    created, _ = store.plan_runs(conn, synthetic.project_id, actor="test")
    calls = 0

    def stop_soon() -> bool:
        nonlocal calls
        calls += 1
        return calls > 2

    assert not engine.run_resolution(conn, created[0], stop_soon)
    state = jobs.latest_state(conn, created[0])
    assert state["stage"] == "ingest"
    assert state["cursor"]
    (partial,) = conn.execute("SELECT count(*) FROM entity_records").fetchone()
    assert 0 < partial < len(rows)

    assert engine.run_resolution(conn, created[0], lambda: False)
    counts = conn.execute(
        "SELECT (SELECT count(*) FROM entity_records), "
        "(SELECT count(DISTINCT candidate_id) FROM entity_records), "
        "(SELECT count(*) FROM extractions WHERE extractor = 'resolution.candidate')"
    ).fetchone()
    assert tuple(counts) == (len(rows), len(rows), len(rows))
    assert store.get_resolution_run(conn, created[0]).status is JobStatus.SUCCEEDED


async def test_the_service_runs_recovers_and_refuses_a_second_open_run(data_dir: Path) -> None:
    conn = repository.connect(data_dir)
    try:
        stamp = utc_now()
        project = synthetic_project(stamp)
        connector = synthetic_connector(stamp)
        run = synthetic_run(project.id, connector.id, stamp)
        repository.insert(conn, project, connector, run)
        context = SyntheticContext(project.id, connector.id, run.id, stamp)
        insert_candidates(conn, context, SYNTHETIC_RESOLUTION_ROWS)
        service = engine.ResolutionService(lambda: repository.connect(data_dir))

        (started,) = (await service.start(ResolutionStartRequest(project_id=project.id))).items
        await service.idle(started.job_id)
        assert store.get_resolution_run(conn, started.job_id).status is JobStatus.SUCCEEDED

        # a run a dead worker left running is paused at startup, then resumed by the operator
        created, _ = store.plan_runs(conn, project.id, actor="test")
        jobs.mark_running(conn, created[0], "test")
        with pytest.raises(store.ResolutionError, match="already open"):
            await service.start(ResolutionStartRequest(project_id=project.id))
        assert await service.recover() == 1
        assert store.get_resolution_run(conn, created[0]).status is JobStatus.PAUSED
        resumed = await service.control(
            DiscoveryControlRequest(job_id=created[0], action=RunControlAction.RESUME)
        )
        assert resumed.status in (JobStatus.RUNNING, JobStatus.SUCCEEDED)  # it may already be done
        await service.idle(created[0])
        assert store.get_resolution_run(conn, created[0]).status is JobStatus.SUCCEEDED

        with pytest.raises(repository.NotFoundError):
            await service.control(
                DiscoveryControlRequest(job_id=new_id(), action=RunControlAction.CANCEL)
            )
    finally:
        conn.close()


def test_a_store_run_over_thousands_of_candidates(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    rows = candidate_rows(1_500, seed=9)
    started = time.perf_counter()
    insert_candidates(conn, synthetic, rows)
    inserted = time.perf_counter() - started
    started = time.perf_counter()
    job_id = resolve(conn, synthetic.project_id)
    elapsed = time.perf_counter() - started
    stats = store.get_resolution_run(conn, job_id).stats
    print(
        f"{len(rows)} candidates: inserted in {inserted:.1f}s, resolved in {elapsed:.1f}s "
        f"({len(rows) / elapsed:.0f} candidates/s); {stats}"
    )
    assert stats.records == len(rows)
    assert stats.ingested == len(rows)
    assert stats.clusters > 0
    assert elapsed < 180
