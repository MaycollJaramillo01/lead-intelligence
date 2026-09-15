"""Overture places: the campaign's filters, pages read from one local copy, and contacts that flow.

A small Parquet file in the Overture shape stands in for the release on S3, and a mock transport
answers the release catalogue and Nominatim, so nothing here leaves the machine
(`test_discovery_live.py` checks the real release on request).
"""

import asyncio
import sqlite3
from pathlib import Path

import duckdb
import httpx
import pytest

from research_engine import repository
from research_engine.discovery.engine import DiscoveryService, EngineSettings
from research_engine.discovery.sources import overture
from research_engine.domain import (
    CampaignSaveRequest,
    ContactPoint,
    ContactType,
    DiscoveryCandidate,
    DiscoveryLimits,
    DiscoveryRun,
    DiscoveryStartRequest,
    GeoScope,
    JobStatus,
    RawRecord,
    SourceRunStatus,
    SourceSelection,
    empty_criteria,
    new_id,
    utc_now,
)
from research_engine.resolution.store import candidate_observations

pytestmark = pytest.mark.anyio

RELEASE = "2026-08-19.0"
#: What Nominatim answered for Houston, TX on 2026-09-15.
HOUSTON = {"lat": "29.76", "lon": "-95.37", "boundingbox": ["29.53", "30.11", "-95.91", "-95.01"]}
#: 600 reachable roofers in Houston, then one place per reason the filter must leave it out:
#: 600 has no phone or email, 601 is closed, 602 is a bakery and 603 is in Miami.
PLACES_SQL = """
SELECT
    'place-' || lpad(i::VARCHAR, 4, '0') AS id,
    {'primary': CASE WHEN i = 602 THEN 'Panaderia El Sol' ELSE 'Techo Seguro ' || i::VARCHAR END}
        AS names,
    CASE WHEN i = 600 THEN []::VARCHAR[]
         ELSE ['+1832704' || lpad(i::VARCHAR, 4, '0')] END AS phones,
    CASE WHEN i = 0 THEN ['ventas@techoseguro.com'] ELSE []::VARCHAR[] END AS emails,
    CASE WHEN i = 0 THEN ['https://techoseguro.com/'] ELSE []::VARCHAR[] END AS websites,
    []::VARCHAR[] AS socials,
    [{'freeform': i::VARCHAR || ' Main St',
      'locality': CASE WHEN i = 603 THEN 'Miami' ELSE 'Houston' END,
      'postcode': '77006',
      'region': CASE WHEN i = 603 THEN 'FL' ELSE 'TX' END,
      'country': 'US'}] AS addresses,
    CASE WHEN i = 602 THEN 'food_and_beverage_store' ELSE 'home_service' END AS basic_category,
    {'primary': CASE WHEN i = 602 THEN 'bakery' ELSE 'roofing' END,
     'hierarchy': ['home_service', CASE WHEN i = 602 THEN 'bakery' ELSE 'roofing' END]}
        AS taxonomy,
    0.9::DOUBLE AS confidence,
    CASE WHEN i = 601 THEN 'permanently_closed' ELSE 'open' END AS operating_status,
    {'xmin': (CASE WHEN i = 603 THEN -80.19 ELSE -95.37 END)::DOUBLE,
     'xmax': (CASE WHEN i = 603 THEN -80.19 ELSE -95.37 END)::DOUBLE,
     'ymin': (CASE WHEN i = 603 THEN 25.76 ELSE 29.76 END)::DOUBLE,
     'ymax': (CASE WHEN i = 603 THEN 25.76 ELSE 29.76 END)::DOUBLE} AS bbox,
    [{'dataset': 'meta', 'license': 'CDLA-Permissive-2.0'}] AS sources
FROM range(604) AS places(i)
"""


class FakeWeb(httpx.AsyncBaseTransport):
    """The release catalogue and Nominatim. Records which hosts were asked."""

    def __init__(self) -> None:
        self.hosts: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.hosts.append(request.url.host)
        if request.url.host == "stac.overturemaps.org":
            return httpx.Response(200, json={"latest": RELEASE})
        assert request.url.params["city"] == "Houston"
        assert request.url.params["state"] == "TX"
        return httpx.Response(200, json=[HOUSTON])


async def settle(engine: DiscoveryService, job_id: str) -> DiscoveryRun:
    async with asyncio.timeout(30):
        while True:
            run = await engine.run(job_id)
            working = any(
                source.status in (SourceRunStatus.QUEUED, SourceRunStatus.RUNNING)
                for source in run.sources
            )
            if run.status not in (JobStatus.QUEUED, JobStatus.RUNNING) and not working:
                return run
            await asyncio.sleep(0.02)


async def test_reachable_places_become_candidates_whose_phone_and_email_reach_contacts(
    conn: sqlite3.Connection, data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    duck = duckdb.connect()
    duck.execute(f"COPY ({PLACES_SQL}) TO '{release_dir / f'{RELEASE}.parquet'}' (FORMAT parquet)")
    duck.close()
    monkeypatch.setattr(overture, "PLACES", str(release_dir / "{release}.parquet"))
    web = FakeWeb()
    engine = DiscoveryService(
        EngineSettings(data_dir=data_dir, user_agent="LeadIntelligence/test", transport=web),
        lambda: repository.connect(data_dir),
    )
    spec = empty_criteria().model_copy(
        update={
            "geography": [
                GeoScope(
                    country_code="US",
                    region="TX",
                    metro=None,
                    locality="Houston",
                    postal_code=None,
                    radius_km=None,
                )
            ],
            "categories": ["roofing"],
            "sources": [
                SourceSelection(
                    connector_key=overture.KEY, priority=50, max_records=None, config_json={}
                )
            ],
            "limits": DiscoveryLimits(
                max_candidates=1000, max_requests=50, max_pages_per_source=25
            ),
        }
    )
    campaign_id = repository.save_campaign(
        conn,
        CampaignSaveRequest(
            campaign_id=None, project_id=None, name="Houston roofers", objective=None, criteria=spec
        ),
        actor="test",
    )
    started = await engine.start(
        DiscoveryStartRequest(campaign_id=campaign_id, connector_keys=[], inputs={})
    )
    run = await settle(engine, started.job_id)
    await engine.shutdown()

    assert run.status is JobStatus.SUCCEEDED
    source = run.sources[0]
    assert source.candidate_count == 600
    assert source.pages_done == 2  # 500 and 100 rows, both windows of the one local copy
    assert web.hosts == ["stac.overturemaps.org", "nominatim.openstreetmap.org"]

    row = conn.execute(
        "SELECT id FROM discovery_candidates WHERE external_id = 'place-0000'"
    ).fetchone()
    assert row is not None
    candidate = repository.get(conn, DiscoveryCandidate, row[0])
    assert candidate.phone_raw == "+18327040000"
    assert candidate.website_url == "https://techoseguro.com/"
    payload = repository.get(conn, RawRecord, candidate.raw_record_id).payload_json
    records = candidate_observations(
        candidate, payload, "public_dataset", new_id(), new_id(), utc_now(), primary=True
    )
    contacts = {
        record.contact_type: record.value_normalized
        for record in records
        if isinstance(record, ContactPoint)
    }
    assert contacts == {
        ContactType.PHONE: "+18327040000",
        ContactType.EMAIL: "ventas@techoseguro.com",
    }
