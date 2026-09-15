"""The tests that leave the machine. Off by default; set LEAD_LIVE_SOURCE_TESTS=1 to run them.

Their job is to notice drift: the recorded responses the rest of the suite replays were real once,
and these prove the live sources still answer in the shape the connectors expect. Each sends one
narrow, credential-free request.
"""

import os
from pathlib import Path

import httpx
import pytest

from research_engine.discovery.sources import overture, usaspending
from research_engine.discovery.sources.base import SourceContext, SourceRequest
from research_engine.domain import (
    DiscoveryLimits,
    GeoScope,
    SourceSelection,
    empty_criteria,
    utc_now,
)

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.local_services,
    pytest.mark.skipif(
        os.environ.get("LEAD_LIVE_SOURCE_TESTS") != "1",
        reason="live source tests are opt-in: set LEAD_LIVE_SOURCE_TESTS=1",
    ),
]


def _request() -> SourceRequest:
    criteria = empty_criteria().model_copy(
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
            "verticals": ["23"],
            "freshness_since": "2024-01-01",
            "limits": DiscoveryLimits(max_candidates=5, max_requests=1, max_pages_per_source=1),
        }
    )
    return SourceRequest(
        criteria=criteria,
        selection=SourceSelection(
            connector_key=usaspending.KEY, priority=50, max_records=5, config_json={}
        ),
        page_size=5,
        run_input={},
    )


async def test_the_real_api_still_answers_in_the_recorded_shape() -> None:
    async with httpx.AsyncClient(
        timeout=30.0, headers={"User-Agent": "LeadIntelligence/live-test"}
    ) as client:
        context = SourceContext(data_dir=None, http=client, now=utc_now())
        page = await usaspending.SOURCE.discover(_request(), None, context)
    assert page.http_status == 200
    assert page.candidates, "the query that was recorded should still return something"
    first = page.candidates[0]
    assert first.name_raw
    assert first.region == "FL"
    assert "Recipient Name" in first.payload_json
    assert page.next_cursor in (None, "2")


async def test_overture_still_has_reachable_places(tmp_path: Path) -> None:
    criteria = empty_criteria().model_copy(
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
            "limits": DiscoveryLimits(max_candidates=5, max_requests=1, max_pages_per_source=1),
        }
    )
    request = SourceRequest(
        criteria=criteria,
        selection=SourceSelection(
            connector_key=overture.KEY, priority=50, max_records=5, config_json={}
        ),
        page_size=5,
        run_input={},
    )
    async with httpx.AsyncClient(
        timeout=30.0, headers={"User-Agent": "LeadIntelligence/live-test"}
    ) as client:
        context = SourceContext(data_dir=tmp_path, http=client, now=utc_now())
        page = await overture.SOURCE.discover(request, None, context)
    assert page.candidates, "Houston roofers with a phone or an email should still be there"
    assert all(item.phone_raw or item.payload_json.get("email") for item in page.candidates)
    assert page.next_cursor is None  # five places fit one page
