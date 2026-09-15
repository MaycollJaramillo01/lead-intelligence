"""The discovery engine: adapters, criteria, pagination, checkpoints, limits, failures and control.

Every source here answers from a recorded response or a local file, so the suite never needs the
network. `test_discovery_live.py` is the one that talks to the real API, and only on request.
"""

import asyncio
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

from research_engine import repository
from research_engine.discovery import registry
from research_engine.discovery.engine import (
    DiscoveryService,
    DiscoveryStartError,
    EngineSettings,
)
from research_engine.discovery.normalize import dedup_key, domain_of
from research_engine.discovery.sources import file_import, usaspending
from research_engine.discovery.sources.base import (
    DiscoveryError,
    DiscoverySource,
    SourceContext,
    SourceRequest,
)
from research_engine.domain import (
    CampaignListQuery,
    CampaignSaveRequest,
    CandidateListQuery,
    Capability,
    DiscoveryControlRequest,
    DiscoveryCriteria,
    DiscoveryErrorCode,
    DiscoveryLimits,
    DiscoveryRun,
    DiscoveryRunListQuery,
    DiscoveryStartRequest,
    DiscoveryStrategy,
    GeoScope,
    ImportFormat,
    ImportPreviewRequest,
    JobStatus,
    ProblemSeverity,
    SourceHealth,
    SourceKind,
    SourceRunStatus,
    SourceSelection,
    empty_criteria,
    utc_now,
)

pytestmark = pytest.mark.anyio

RECORDED = Path(__file__).parent / "recorded"
SETTLE_TIMEOUT_S = 20.0
USASPENDING_KEY = usaspending.KEY
IMPORT_CSV = (
    "Business Name,City,State,ZIP,Website,Phone,Category\n"
    "Panaderia El Sol,Austin,TX,78701,https://panaderia-el-sol.example,(512) 555-0101,Bakery\n"
    "Techo Seguro Roofing,Miami,FL,33130,techo-seguro.example,(305) 555-0102,Roofing\n"
    "Panaderia El Sol,Austin,TX,78701,https://panaderia-el-sol.example,(512) 555-0101,Bakery\n"
)


# ---- helpers -------------------------------------------------------------------------------------


@dataclass
class RecordedApi(httpx.AsyncBaseTransport):
    """Replays recorded USAspending pages and can fail, stall or rate-limit on demand."""

    pages: list[dict[str, object]]
    requested: list[int] = field(default_factory=list)
    #: page number -> how many times it should answer 429 before the recorded page
    rate_limit: dict[int, int] = field(default_factory=dict)
    fail_with: dict[int, int] = field(default_factory=dict)
    gate: asyncio.Event | None = None
    gate_page: int = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        page = int(body.get("page", 1))
        self.requested.append(page)
        if self.gate is not None and page == self.gate_page:
            await self.gate.wait()
        if self.rate_limit.get(page):
            self.rate_limit[page] -= 1
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"detail": "slow down"})
        if self.fail_with.get(page):
            return httpx.Response(self.fail_with[page], json={"detail": "nope"})
        if page > len(self.pages):
            return httpx.Response(200, json={"results": [], "page_metadata": {"hasNext": False}})
        return httpx.Response(200, json=self.pages[page - 1])


def recorded_pages() -> list[dict[str, object]]:
    return [
        json.loads((RECORDED / f"usaspending_page{number}.json").read_text(encoding="utf-8"))
        for number in (1, 2, 3)
    ]


def criteria(
    *,
    sources: list[SourceSelection],
    limits: DiscoveryLimits | None = None,
    verticals: list[str] | None = None,
) -> DiscoveryCriteria:
    return empty_criteria().model_copy(
        update={
            "geography": [
                GeoScope(
                    country_code="US",
                    region="FL",
                    metro=None,
                    locality="Hialeah",
                    postal_code=None,
                    radius_km=None,
                )
            ],
            "verticals": verticals or ["23"],
            "sources": sources,
            "limits": limits
            or DiscoveryLimits(max_candidates=500, max_requests=50, max_pages_per_source=25),
        }
    )


def selection(key: str, **config: object) -> SourceSelection:
    return SourceSelection(connector_key=key, priority=50, max_records=None, config_json=config)


def service(data_dir: Path, **settings: object) -> DiscoveryService:
    return DiscoveryService(
        EngineSettings(data_dir=data_dir, user_agent="LeadIntelligence/test", **settings),  # type: ignore[arg-type]
        lambda: repository.connect(data_dir),
    )


async def make_campaign(
    engine: DiscoveryService, conn: sqlite3.Connection, spec: DiscoveryCriteria, name: str
) -> str:
    return repository.save_campaign(
        conn,
        CampaignSaveRequest(
            campaign_id=None, project_id=None, name=name, objective=None, criteria=spec
        ),
        actor="test",
    )


_SETTLED = (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.PAUSED)
_WORKING = (SourceRunStatus.QUEUED, SourceRunStatus.RUNNING)


async def settle(engine: DiscoveryService, job_id: str) -> DiscoveryRun:
    """Waits until the run has stopped for good: the job settled and no source still working."""
    async with asyncio.timeout(SETTLE_TIMEOUT_S):
        while True:
            run = await engine.run(job_id)
            working = any(source.status in _WORKING for source in run.sources)
            if run.status in _SETTLED and not working:
                return run
            await asyncio.sleep(0.02)


@pytest.fixture
def import_file(tmp_path: Path) -> Callable[[str, str], dict[str, object]]:
    def make(content: str, filename: str = "leads.csv") -> dict[str, object]:
        return {"filename": filename, "format": None, "content": content, "mapping": None}

    return make


@pytest.fixture
def engine(data_dir: Path) -> DiscoveryService:
    """An engine with no network: the sources used here read files and lists, not the internet."""
    return service(data_dir, offline=True)


# ---- the adapter contract ------------------------------------------------------------------------


def test_every_registered_source_satisfies_the_contract() -> None:
    assert set(registry.SOURCES) == {
        "file.import",
        "manual.seeds",
        usaspending.KEY,
        "overture.places",
        "directory.api_template",
    }
    for key, source in registry.SOURCES.items():
        assert isinstance(source, DiscoverySource), key
        meta = source.metadata
        assert meta.key == key
        assert meta.terms_summary.strip()
        assert Capability.PAGINATION in meta.capabilities or meta.kind is SourceKind.DIRECTORY
        assert meta.default_requests_per_minute > 0


async def test_a_source_that_needs_a_key_refuses_instead_of_inventing_one(
    data_dir: Path, engine: DiscoveryService
) -> None:
    template = registry.SOURCES["directory.api_template"]
    assert await template.validate_config({}) != []
    request = SourceRequest(
        criteria=empty_criteria(),
        selection=selection("directory.api_template"),
        page_size=10,
        run_input={},
    )
    context = SourceContext(data_dir=data_dir, http=None, now=utc_now())
    with pytest.raises(DiscoveryError) as raised:
        await template.discover(request, None, context)
    assert raised.value.code is DiscoveryErrorCode.AUTH_REQUIRED
    assert not raised.value.retryable


async def test_the_catalogue_reports_health_per_source(engine: DiscoveryService) -> None:
    catalog = await engine.catalog(None)
    by_key = {info.metadata.key: info for info in catalog.sources}
    assert by_key["manual.seeds"].health is SourceHealth.NEEDS_CONFIGURATION  # no seeds yet
    assert by_key[usaspending.KEY].health is SourceHealth.READY
    assert by_key["directory.api_template"].health is SourceHealth.DISABLED
    assert by_key["directory.api_template"].enabled is False


# ---- criteria ------------------------------------------------------------------------------------


async def test_criteria_validation_separates_blockers_from_warnings(
    engine: DiscoveryService,
) -> None:
    catalog = {info.metadata.key: info for info in (await engine.catalog(None)).sources}
    empty = registry.check_criteria(empty_criteria(), catalog)
    assert any(problem.code == "no_sources" for problem in registry.blocking(empty))

    spec = criteria(sources=[selection(USASPENDING_KEY), selection(USASPENDING_KEY)])
    spec = spec.model_copy(
        update={
            "market_languages": ["es"],
            "categories": ["Bakery"],
            "strategy": DiscoveryStrategy.NARROW,
        }
    )
    problems = registry.check_criteria(spec, catalog)
    codes = {problem.code for problem in problems}
    assert "duplicate_source" in codes
    assert "language_is_a_signal" in codes
    assert "unsupported_field" in codes  # USAspending cannot filter on free-text categories
    warnings = {p.code for p in problems if p.severity is ProblemSeverity.WARNING}
    assert "language_is_a_signal" in warnings
    assert "duplicate_source" not in warnings


async def test_a_campaign_with_blocking_problems_cannot_start(
    conn: sqlite3.Connection, engine: DiscoveryService
) -> None:
    spec = criteria(sources=[selection("manual.seeds")])  # no seeds configured
    campaign_id = await make_campaign(engine, conn, spec, "Unusable")
    with pytest.raises(DiscoveryStartError):
        await engine.start(
            DiscoveryStartRequest(campaign_id=campaign_id, connector_keys=[], inputs={})
        )


# ---- file import ---------------------------------------------------------------------------------


def test_import_preview_maps_columns_and_names_the_rows_it_cannot_use() -> None:
    broken = IMPORT_CSV + ",Dallas,TX,75201,,,\nOnly A Name\n"
    preview = file_import.preview(
        ImportPreviewRequest(format=None, filename="leads.csv", content=broken, mapping=None)
    )
    assert preview.format is ImportFormat.CSV
    assert preview.suggested_mapping.name == "Business Name"
    assert preview.suggested_mapping.postal_code == "ZIP"
    assert preview.total_rows == 5
    assert preview.usable_rows == 4  # the row without a name is not usable
    assert [problem.row for problem in preview.problems] == [4]
    assert preview.rows[3].problem is not None
    assert preview.rows[4].name_raw == "Only A Name"


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("leads.json", '[{"name": "Panaderia El Sol", "city": "Austin", "state": "TX"}]'),
        ("leads.jsonl", '{"name": "Panaderia El Sol", "city": "Austin", "state": "TX"}'),
        ("leads.tsv", "name\tcity\tstate\nPanaderia El Sol\tAustin\tTX"),
    ],
)
def test_every_supported_format_reaches_the_same_candidate(filename: str, content: str) -> None:
    preview = file_import.preview(
        ImportPreviewRequest(format=None, filename=filename, content=content, mapping=None)
    )
    assert preview.usable_rows == 1
    assert preview.rows[0].name_raw == "Panaderia El Sol"


def test_a_malformed_file_is_refused_with_a_reason() -> None:
    with pytest.raises(DiscoveryError) as raised:
        file_import.preview(
            ImportPreviewRequest(
                format=ImportFormat.JSON, filename="leads.json", content="{not json", mapping=None
            )
        )
    assert raised.value.code is DiscoveryErrorCode.INVALID_QUERY


def test_a_ragged_row_keeps_its_extra_values_instead_of_shifting_columns() -> None:
    ragged = "name,city\nPanaderia El Sol,Austin,TX,extra\nSolo,\n"
    columns, rows = file_import.parse(ragged, ImportFormat.CSV)
    assert columns == ["name", "city"]
    assert rows[0]["city"] == "Austin"
    assert "_extra" in rows[0]
    assert rows[1]["name"] == "Solo"
    assert rows[1]["city"] == ""


async def test_importing_a_file_stores_candidates_with_their_provenance(
    conn: sqlite3.Connection,
    data_dir: Path,
    engine: DiscoveryService,
    import_file: Callable[..., dict[str, object]],
) -> None:
    spec = criteria(sources=[selection("file.import")])
    campaign_id = await make_campaign(engine, conn, spec, "Imported list")
    run = await engine.start(
        DiscoveryStartRequest(
            campaign_id=campaign_id,
            connector_keys=[],
            inputs={"file.import": import_file(IMPORT_CSV)},
        )
    )
    run = await settle(engine, run.job_id)
    assert run.status is JobStatus.SUCCEEDED
    assert run.candidate_count == 2
    assert run.duplicate_count == 1  # the third row repeats the first

    page = repository.list_candidates(
        conn,
        CandidateListQuery(
            project_id=None,
            campaign_id=campaign_id,
            source_run_id=None,
            search=None,
            include_duplicates=False,
            limit=50,
            cursor=None,
        ),
    )
    assert {item.name_raw for item in page.items} == {"Panaderia El Sol", "Techo Seguro Roofing"}
    assert {item.domain for item in page.items} == {
        "panaderia-el-sol.example",
        "techo-seguro.example",
    }
    detail = repository.get_candidate_detail(conn, page.items[0].id)
    assert detail.raw_record.payload_json["City"] in ("Austin", "Miami")
    assert detail.acquired_page is not None
    assert detail.acquired_page.artifact_path is not None
    stored = data_dir / detail.acquired_page.artifact_path
    assert stored.read_text(encoding="utf-8") == IMPORT_CSV
    assert detail.source_query is not None
    assert detail.source_run.campaign_id == campaign_id


async def test_search_and_duplicate_filters_work_on_candidates(
    conn: sqlite3.Connection,
    engine: DiscoveryService,
    import_file: Callable[..., dict[str, object]],
) -> None:
    spec = criteria(sources=[selection("file.import")])
    campaign_id = await make_campaign(engine, conn, spec, "Searchable")
    run = await engine.start(
        DiscoveryStartRequest(
            campaign_id=campaign_id,
            connector_keys=[],
            inputs={"file.import": import_file(IMPORT_CSV)},
        )
    )
    await settle(engine, run.job_id)

    def query(**changes: object) -> CandidateListQuery:
        base = {
            "project_id": None,
            "campaign_id": campaign_id,
            "source_run_id": None,
            "search": None,
            "include_duplicates": False,
            "limit": 50,
            "cursor": None,
        }
        return CandidateListQuery.model_validate(base | changes)

    with_duplicates = repository.list_candidates(conn, query(include_duplicates=True))
    assert with_duplicates.total == 3
    searched = repository.list_candidates(conn, query(search="panadería"))
    assert searched.total == 1
    assert searched.items[0].name_raw == "Panaderia El Sol"


# ---- manual seeds --------------------------------------------------------------------------------


async def test_seeds_become_candidates_with_their_own_provenance(
    conn: sqlite3.Connection, engine: DiscoveryService
) -> None:
    spec = criteria(
        sources=[
            selection(
                "manual.seeds",
                seeds=["https://techo-seguro.example/contact", "Panaderia El Sol, Austin TX"],
            )
        ]
    )
    campaign_id = await make_campaign(engine, conn, spec, "Seeded")
    run = await settle(
        engine,
        (
            await engine.start(
                DiscoveryStartRequest(campaign_id=campaign_id, connector_keys=[], inputs={})
            )
        ).job_id,
    )
    assert run.status is JobStatus.SUCCEEDED
    assert run.candidate_count == 2
    page = repository.list_candidates(
        conn,
        CandidateListQuery(
            project_id=None,
            campaign_id=campaign_id,
            source_run_id=None,
            search=None,
            include_duplicates=True,
            limit=50,
            cursor=None,
        ),
    )
    by_name = {item.name_raw: item for item in page.items}
    assert by_name["techo-seguro.example"].domain == "techo-seguro.example"
    assert by_name["Panaderia El Sol"].region == "TX"
    assert by_name["Panaderia El Sol"].locality == "Austin"


# ---- pagination, checkpoints and resume ----------------------------------------------------------


async def start_usaspending(
    conn: sqlite3.Connection,
    data_dir: Path,
    api: RecordedApi,
    name: str,
    *,
    limits: DiscoveryLimits | None = None,
    max_records: int | None = None,
) -> tuple[DiscoveryService, str]:
    engine = service(data_dir, transport=api)
    spec = criteria(
        sources=[
            SourceSelection(
                connector_key=USASPENDING_KEY,
                priority=50,
                max_records=max_records,
                config_json={},
            )
        ],
        limits=limits,
    )
    campaign_id = await make_campaign(engine, conn, spec, name)
    run = await engine.start(
        DiscoveryStartRequest(campaign_id=campaign_id, connector_keys=[], inputs={})
    )
    return engine, run.job_id


async def test_pagination_follows_the_cursor_to_the_end(
    conn: sqlite3.Connection, data_dir: Path
) -> None:
    api = RecordedApi(pages=recorded_pages())
    engine, job_id = await start_usaspending(conn, data_dir, api, "Hialeah builders")
    run = await settle(engine, job_id)
    await engine.shutdown()
    assert run.status is JobStatus.SUCCEEDED
    assert api.requested == [1, 2, 3]  # every page once, and it stopped when hasNext went false
    source = run.sources[0]
    assert source.pages_done == 3
    assert source.records_seen == 13
    assert source.candidate_count == 7
    assert source.duplicate_count == 6
    assert source.cursor is None
    assert source.status is SourceRunStatus.SUCCEEDED
    assert run.progress_total == 7
    assert run.progress_done == 7


async def test_a_paused_run_resumes_from_its_checkpoint_without_repeating_a_page(
    conn: sqlite3.Connection, data_dir: Path
) -> None:
    gate = asyncio.Event()
    api = RecordedApi(pages=recorded_pages(), gate=gate, gate_page=2)
    engine, job_id = await start_usaspending(conn, data_dir, api, "Paused halfway")
    async with asyncio.timeout(10):
        while api.requested != [1, 2]:  # noqa: ASYNC110 - the engine has no hook to wait on
            await asyncio.sleep(0.02)
    await engine.control(DiscoveryControlRequest(job_id=job_id, action="pause"))
    gate.set()
    await engine.idle(job_id)  # the page already in flight is finished and stored, then it stops
    paused = await settle(engine, job_id)
    assert paused.status is JobStatus.PAUSED
    assert paused.sources[0].status is SourceRunStatus.PAUSED
    assert paused.sources[0].cursor == "3"
    await engine.shutdown()

    # a new service, as after a restart: the run continues where the checkpoint left it
    resumed_engine = service(data_dir, transport=api)
    assert await resumed_engine.recover() == 0  # it was paused, not left running
    await resumed_engine.control(DiscoveryControlRequest(job_id=job_id, action="resume"))
    run = await settle(resumed_engine, job_id)
    await resumed_engine.shutdown()
    assert run.status is JobStatus.SUCCEEDED
    assert api.requested == [1, 2, 3], "a resumed run must not ask for a page it already stored"
    assert run.candidate_count == 7


async def test_a_run_interrupted_by_a_restart_is_paused_and_resumable(
    conn: sqlite3.Connection, data_dir: Path
) -> None:
    gate = asyncio.Event()
    api = RecordedApi(pages=recorded_pages(), gate=gate, gate_page=2)
    engine, job_id = await start_usaspending(conn, data_dir, api, "Killed mid-run")
    async with asyncio.timeout(10):
        while api.requested != [1, 2]:  # noqa: ASYNC110 - the engine has no hook to wait on
            await asyncio.sleep(0.02)
    await engine.shutdown()  # the worker stops with the job still marked running
    gate.set()

    restarted = service(data_dir, transport=api)
    assert await restarted.recover() >= 0
    run = await restarted.run(job_id)
    assert run.status is JobStatus.PAUSED
    assert any(event.event == "run_interrupted" for event in run.events)
    await restarted.control(DiscoveryControlRequest(job_id=job_id, action="resume"))
    finished = await settle(restarted, job_id)
    await restarted.shutdown()
    assert finished.status is JobStatus.SUCCEEDED
    assert finished.candidate_count == 7


async def test_cancelling_stops_the_run_and_leaves_what_it_already_found(
    conn: sqlite3.Connection, data_dir: Path
) -> None:
    gate = asyncio.Event()
    api = RecordedApi(pages=recorded_pages(), gate=gate, gate_page=2)
    engine, job_id = await start_usaspending(conn, data_dir, api, "Cancelled")
    async with asyncio.timeout(10):
        while api.requested != [1, 2]:  # noqa: ASYNC110 - the engine has no hook to wait on
            await asyncio.sleep(0.02)
    await engine.control(DiscoveryControlRequest(job_id=job_id, action="cancel"))
    gate.set()
    run = await settle(engine, job_id)
    await engine.shutdown()
    assert run.status is JobStatus.CANCELLED
    assert run.sources[0].status is SourceRunStatus.CANCELLED
    assert run.candidate_count >= 1, "candidates found before the cancellation are kept"
    assert api.requested == [1, 2], "no page is requested after a cancellation"


# ---- failures ------------------------------------------------------------------------------------


async def test_a_rate_limited_page_is_retried_and_then_succeeds(
    conn: sqlite3.Connection, data_dir: Path
) -> None:
    api = RecordedApi(pages=recorded_pages(), rate_limit={2: 2})
    engine, job_id = await start_usaspending(conn, data_dir, api, "Throttled")
    run = await settle(engine, job_id)
    await engine.shutdown()
    assert run.status is JobStatus.SUCCEEDED
    assert api.requested == [1, 2, 2, 2, 3], "the throttled page is retried, the others are not"
    retried = [event for event in run.events if event.error_code == "rate_limited"]
    assert retried
    assert all(event.retryable for event in retried)


async def test_a_refusal_is_not_retried_and_fails_that_source(
    conn: sqlite3.Connection, data_dir: Path
) -> None:
    api = RecordedApi(pages=recorded_pages(), fail_with={2: 403})
    engine, job_id = await start_usaspending(conn, data_dir, api, "Blocked")
    run = await settle(engine, job_id)
    await engine.shutdown()
    assert run.status is JobStatus.FAILED
    assert api.requested == [1, 2], "a refusal is final: no retry"
    source = run.sources[0]
    assert source.status is SourceRunStatus.FAILED
    assert source.error_code == "blocked"
    assert source.candidate_count >= 1, "the first page is kept"


async def test_an_unusable_query_is_reported_as_such(
    conn: sqlite3.Connection, data_dir: Path
) -> None:
    api = RecordedApi(pages=recorded_pages(), fail_with={1: 400})
    engine, job_id = await start_usaspending(conn, data_dir, api, "Rejected")
    run = await settle(engine, job_id)
    await engine.shutdown()
    assert run.sources[0].error_code == "invalid_query"
    assert api.requested == [1]


async def test_a_source_that_needs_the_network_refuses_when_the_run_is_offline(
    conn: sqlite3.Connection, data_dir: Path
) -> None:
    engine = service(data_dir, offline=True)
    spec = criteria(sources=[selection(USASPENDING_KEY)])
    campaign_id = await make_campaign(engine, conn, spec, "Offline")
    run = await engine.start(
        DiscoveryStartRequest(campaign_id=campaign_id, connector_keys=[], inputs={})
    )
    settled = await settle(engine, run.job_id)
    await engine.shutdown()
    assert settled.sources[0].error_code == "source_unavailable"


# ---- limits --------------------------------------------------------------------------------------


async def test_the_page_limit_of_a_source_stops_it(
    conn: sqlite3.Connection, data_dir: Path
) -> None:
    api = RecordedApi(pages=recorded_pages())
    engine, job_id = await start_usaspending(
        conn,
        data_dir,
        api,
        "One page only",
        limits=DiscoveryLimits(max_candidates=500, max_requests=50, max_pages_per_source=1),
    )
    run = await settle(engine, job_id)
    await engine.shutdown()
    assert api.requested == [1]
    assert run.status is JobStatus.SUCCEEDED
    assert any(event.event == "limit_reached" for event in run.events)


async def test_the_request_budget_of_a_run_stops_it(
    conn: sqlite3.Connection, data_dir: Path
) -> None:
    api = RecordedApi(pages=recorded_pages())
    engine, job_id = await start_usaspending(
        conn,
        data_dir,
        api,
        "Two requests",
        limits=DiscoveryLimits(max_candidates=500, max_requests=2, max_pages_per_source=25),
    )
    run = await settle(engine, job_id)
    await engine.shutdown()
    assert api.requested == [1, 2]
    assert {event.event for event in run.events} >= {"limit_reached"}


async def test_the_record_limit_of_a_source_stops_it(
    conn: sqlite3.Connection, data_dir: Path
) -> None:
    api = RecordedApi(pages=recorded_pages())
    engine, job_id = await start_usaspending(conn, data_dir, api, "Five records", max_records=5)
    run = await settle(engine, job_id)
    await engine.shutdown()
    assert api.requested == [1], "the first page already reaches the record limit"
    assert run.sources[0].records_seen == 5


# ---- provenance and lineage ----------------------------------------------------------------------


async def test_raw_records_are_immutable_and_every_candidate_points_at_one(
    conn: sqlite3.Connection, data_dir: Path
) -> None:
    api = RecordedApi(pages=recorded_pages())
    engine, job_id = await start_usaspending(conn, data_dir, api, "Lineage")
    await settle(engine, job_id)
    await engine.shutdown()

    def check(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT c.id, c.raw_record_id, r.payload_json, p.url, q.cursor "
            "FROM discovery_candidates c "
            "JOIN raw_records r ON r.id = c.raw_record_id "
            "JOIN acquired_pages p ON p.id = r.acquired_page_id "
            "JOIN source_queries q ON q.id = p.source_query_id"
        ).fetchall()
        assert len(rows) == 13
        assert all("Recipient Name" in row["payload_json"] for row in rows)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE raw_records SET external_id = 'x'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE discovery_candidates SET name_raw = 'x'")

    check(conn)


async def test_candidates_of_one_campaign_never_mix_with_another(
    conn: sqlite3.Connection,
    engine: DiscoveryService,
    import_file: Callable[..., dict[str, object]],
) -> None:
    spec = criteria(sources=[selection("file.import")])
    first = await make_campaign(engine, conn, spec, "First campaign")
    second = await make_campaign(engine, conn, spec, "Second campaign")
    for campaign_id in (first, second):
        run = await engine.start(
            DiscoveryStartRequest(
                campaign_id=campaign_id,
                connector_keys=[],
                inputs={"file.import": import_file(IMPORT_CSV)},
            )
        )
        await settle(engine, run.job_id)

    def query(campaign_id: str) -> CandidateListQuery:
        return CandidateListQuery(
            project_id=None,
            campaign_id=campaign_id,
            source_run_id=None,
            search=None,
            include_duplicates=True,
            limit=50,
            cursor=None,
        )

    for campaign_id in (first, second):
        page = repository.list_candidates(conn, query(campaign_id))
        assert page.total == 3
        assert {item.campaign_id for item in page.items} == {campaign_id}
    # the same business in two campaigns is not a duplicate: each campaign keeps its own lineage
    both = repository.list_candidates(
        conn,
        CandidateListQuery(
            project_id=None,
            campaign_id=None,
            source_run_id=None,
            search=None,
            include_duplicates=False,
            limit=50,
            cursor=None,
        ),
    )
    assert both.total == 4


# ---- listings ------------------------------------------------------------------------------------


async def test_campaigns_and_runs_are_listed_with_their_counts(
    conn: sqlite3.Connection,
    engine: DiscoveryService,
    import_file: Callable[..., dict[str, object]],
) -> None:
    spec = criteria(sources=[selection("file.import")])
    campaign_id = await make_campaign(engine, conn, spec, "Listed")
    run = await engine.start(
        DiscoveryStartRequest(
            campaign_id=campaign_id,
            connector_keys=[],
            inputs={"file.import": import_file(IMPORT_CSV)},
        )
    )
    await settle(engine, run.job_id)

    campaigns = repository.list_campaigns(
        conn,
        CampaignListQuery(project_id=None, limit=10, cursor=None),
    )
    assert campaigns.total == 1
    item = campaigns.items[0]
    assert item.run_count == 1
    assert item.candidate_count == 2
    assert item.last_run_status is JobStatus.SUCCEEDED
    assert item.criteria.sources[0].connector_key == "file.import"

    runs = repository.list_discovery_runs(
        conn,
        DiscoveryRunListQuery(project_id=None, campaign_id=campaign_id, limit=10, cursor=None),
    )
    assert runs.total == 1
    assert runs.items[0].campaign_name == "Listed"


# ---- normalisation -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://www.Example.com/path", "example.com"),
        ("example.co.uk", "example.co.uk"),
        ("  ", None),
        ("not a domain", None),
        ("mailto:someone@example.com", None),
    ],
)
def test_domain_extraction(value: str, expected: str | None) -> None:
    assert domain_of(value) == expected


def test_the_dedup_key_prefers_a_stable_id_then_a_domain_then_the_name() -> None:
    assert dedup_key(name="A", external_id="abc").startswith("id:")
    assert dedup_key(name="A", domain="a.example").startswith("domain:")
    assert dedup_key(name="Panadería El Sol", locality="Austin", region="TX") == dedup_key(
        name="PANADERIA EL SOL", locality="austin", region="TX"
    )
    assert dedup_key(name="A", locality="Austin") != dedup_key(name="A", locality="Miami")


def test_a_recorded_award_becomes_a_candidate_without_inventing_anything() -> None:
    page = recorded_pages()[0]
    award = page["results"][0]  # type: ignore[index]
    candidate = usaspending.candidate_from_award(award)
    assert candidate is not None
    assert candidate.region == "FL"
    assert candidate.country_code == "US"
    assert candidate.domain is None
    assert candidate.website_url is None
    assert candidate.payload_json == award
    assert candidate.external_id == award["recipient_id"]
