"""USAspending.gov: US businesses that hold federal awards. Public, paginated, no credentials.

Why this source is the one that proves the engine works end to end: it is a real US business
registry, it is public domain by law, it needs no key, it filters by state, city, ZIP and NAICS, and
it paginates properly - so pagination, checkpoints, rate limits, `Retry-After` and resume are
exercised against a real API instead of a mock.

What it is not: a directory of every small business. A recipient is here because it won a federal
award, which is a *signal* (the business exists, it sells B2B/B2G, it is registered), never proof of
anything else. The candidate keeps the award payload so the claim can be checked later.

Endpoint: POST /api/v2/search/spending_by_award/ (documented at api.usaspending.gov). The cursor is
the page number; `page_metadata.hasNext` says whether another page exists. The API publishes no hard
rate limit, so the default here is deliberately gentle and the operator can lower it further.

Tests run against recorded responses (`tests/recorded/usaspending_page*.json`), so the suite never
needs the network; `tests/test_discovery_live.py` talks to the real API only when
LEAD_LIVE_SOURCE_TESTS=1.
"""

from collections.abc import Mapping
from typing import Any, Final

import httpx
from pydantic import JsonValue

from research_engine.discovery.normalize import clean, dedup_key
from research_engine.discovery.sources.base import (
    Candidate,
    DiscoveryError,
    DiscoveryPage,
    Estimate,
    SourceContext,
    SourceRequest,
)
from research_engine.domain import (
    Capability,
    DiscoveryErrorCode,
    JsonObject,
    SourceKind,
    SourceMetadata,
)

KEY: Final = "usaspending.recipients"
VERSION: Final = "1.0.0"
BASE_URL: Final = "https://api.usaspending.gov"
SEARCH_PATH: Final = "/api/v2/search/spending_by_award/"
COUNT_PATH: Final = "/api/v2/search/spending_by_award_count/"
#: Contracts and their variants: the award types whose recipients are ordinary companies.
AWARD_TYPES: Final = ("A", "B", "C", "D")
FIELDS: Final = [
    "Award ID",
    "Recipient Name",
    "Recipient Location",
    "Awarding Agency",
    "Award Amount",
    "naics_description",
    "recipient_id",
    "Start Date",
]
#: The API only accepts award dates from this day on; an older freshness filter is clamped to it.
EARLIEST_DATE: Final = "2007-10-01"
DEFAULT_START: Final = "2020-01-01"


def _page_number(cursor: str | None) -> int:
    if cursor is None:
        return 1
    try:
        page = int(cursor)
    except ValueError as exc:
        raise DiscoveryError(
            DiscoveryErrorCode.INVALID_QUERY, f"invalid cursor {cursor!r}"
        ) from exc
    if page < 1:
        raise DiscoveryError(DiscoveryErrorCode.INVALID_QUERY, f"invalid cursor {cursor!r}")
    return page


def _locations(request: SourceRequest) -> list[JsonValue]:
    """Every geography of the campaign this source can honour. Radius is dropped and reported."""
    locations: list[JsonValue] = []
    for scope in request.criteria.geography:
        location: JsonObject = {
            "country": "USA" if scope.country_code == "US" else scope.country_code
        }
        if scope.region:
            location["state"] = scope.region.upper()
        if scope.locality:
            location["city"] = scope.locality.upper()
        if scope.postal_code:
            location["zip"] = scope.postal_code.replace(" ", "")[:5]
        locations.append(location)
    return locations


def build_filters(request: SourceRequest) -> JsonObject:
    """The campaign's criteria as this API's filter object. Only fields it actually supports."""
    start = request.criteria.freshness_since or DEFAULT_START
    filters: JsonObject = {
        "award_type_codes": [*AWARD_TYPES],
        "time_period": [
            {"start_date": max(start, EARLIEST_DATE), "end_date": "2100-01-01"},
        ],
    }
    if locations := _locations(request):
        filters["recipient_locations"] = locations
    if request.criteria.verticals:
        filters["naics_codes"] = [*request.criteria.verticals]
    return filters


def _body(request: SourceRequest, page: int, limit: int) -> JsonObject:
    return {
        "filters": build_filters(request),
        "fields": list(FIELDS),
        "page": page,
        "limit": limit,
        "sort": "Award ID",
        "order": "asc",
        "subawards": False,
    }


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None  # the HTTP-date form; the engine's own backoff covers it


def _raise_for_status(response: httpx.Response) -> None:
    status = response.status_code
    if status < 400:
        return
    detail = (clean(response.text) or f"HTTP {status}")[:300]
    if status == 429:
        raise DiscoveryError(
            DiscoveryErrorCode.RATE_LIMITED,
            f"USAspending asked us to slow down: {detail}",
            retry_after_s=_retry_after(response),
        )
    if status in (401, 403):
        raise DiscoveryError(
            DiscoveryErrorCode.BLOCKED, f"USAspending refused the request: {detail}"
        )
    if status in (400, 422):
        raise DiscoveryError(
            DiscoveryErrorCode.INVALID_QUERY, f"USAspending rejected the query: {detail}"
        )
    if status >= 500:
        raise DiscoveryError(
            DiscoveryErrorCode.SOURCE_UNAVAILABLE,
            f"USAspending is not answering correctly: {detail}",
            retry_after_s=_retry_after(response),
        )
    raise DiscoveryError(DiscoveryErrorCode.NETWORK_ERROR, f"unexpected HTTP {status}: {detail}")


async def _post(context: SourceContext, path: str, body: JsonObject) -> httpx.Response:
    try:
        response = await context.client().post(f"{BASE_URL}{path}", json=body)
    except httpx.TimeoutException as exc:
        raise DiscoveryError(
            DiscoveryErrorCode.NETWORK_ERROR, f"USAspending timed out: {exc}"
        ) from exc
    except httpx.HTTPError as exc:
        raise DiscoveryError(
            DiscoveryErrorCode.NETWORK_ERROR, f"USAspending could not be reached: {exc}"
        ) from exc
    _raise_for_status(response)
    return response


def _decode(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise DiscoveryError(
            DiscoveryErrorCode.SCHEMA_CHANGED, "USAspending returned something that is not JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise DiscoveryError(
            DiscoveryErrorCode.SCHEMA_CHANGED, "USAspending returned an unexpected shape"
        )
    return payload


def candidate_from_award(award: Mapping[str, Any]) -> Candidate | None:
    """One award row as a candidate business. Rows without a recipient name are skipped."""
    name = clean(award.get("Recipient Name"))
    if name is None:
        return None
    location = award.get("Recipient Location")
    place: Mapping[str, Any] = location if isinstance(location, Mapping) else {}
    locality = clean(place.get("city_name"))
    region = clean(place.get("state_code"))
    recipient_id = clean(award.get("recipient_id"))
    award_id = clean(award.get("Award ID"))
    generated = clean(award.get("generated_internal_id"))
    url = f"https://www.usaspending.gov/award/{generated}" if generated else None
    return Candidate(
        external_id=recipient_id or award_id,
        source_url=url,
        payload_json=dict(award),
        dedup_key=dedup_key(name=name, locality=locality, region=region, external_id=recipient_id),
        name_raw=name[:300],
        locality=locality,
        region=region,
        postal_code=clean(place.get("zip5")),
        country_code="US" if clean(place.get("location_country_code")) == "USA" else None,
        domain=None,  # the API publishes no website; phase 05 looks for one
        website_url=None,
        phone_raw=None,  # the award record carries no phone number
        category_raw=clean(award.get("naics_description")),
    )


def _excluded(candidate: Candidate, request: SourceRequest) -> bool:
    exclusions = request.criteria.exclusions
    name = candidate.name_raw.casefold()
    if any(term.casefold() in name for term in exclusions.names):
        return True
    if candidate.postal_code and candidate.postal_code in exclusions.postal_codes:
        return True
    category = (candidate.category_raw or "").casefold()
    return bool(category) and any(term.casefold() in category for term in exclusions.categories)


class UsaSpendingSource:
    """Award recipients, one API page at a time, with the page number as the cursor."""

    metadata = SourceMetadata(
        key=KEY,
        name="USAspending.gov award recipients",
        kind=SourceKind.OFFICIAL_API,
        version=VERSION,
        capabilities=[
            Capability.PAGINATION,
            Capability.CHECKPOINT,
            Capability.ESTIMATE,
            Capability.GEOGRAPHY_REGION,
            Capability.GEOGRAPHY_LOCALITY,
            Capability.GEOGRAPHY_POSTAL_CODE,
            Capability.VERTICAL_NAICS,
            Capability.FRESHNESS,
            Capability.SIZE_PROXY,
        ],
        terms_url="https://api.usaspending.gov/",
        terms_summary=(
            "Official US government API over public-domain federal spending data. No credentials "
            "and no account are required; the published guidance is simply to query gently, so "
            "this connector ships with a conservative rate limit and a daily budget."
        ),
        requires_credentials=False,
        automation_allowed=True,
        default_requests_per_minute=30,
        default_daily_request_budget=2_000,
        default_page_size=100,
        config_fields=[],
    )

    async def validate_config(self, config: Mapping[str, object]) -> list[str]:
        return []

    async def estimate(self, request: SourceRequest, context: SourceContext) -> Estimate:
        body: JsonObject = {"filters": build_filters(request), "subawards": False}
        payload = _decode(await _post(context, COUNT_PATH, body))
        results = payload.get("results")
        if not isinstance(results, Mapping):
            return Estimate(value=None, basis="USAspending did not return a count for this query")
        total = sum(int(value) for value in results.values() if isinstance(value, int))
        return Estimate(
            value=total,
            basis="awards matching the filters, not distinct businesses: expect fewer candidates",
        )

    async def discover(
        self, request: SourceRequest, cursor: str | None, context: SourceContext
    ) -> DiscoveryPage:
        page = _page_number(cursor)
        limit = min(request.page_size, 100)
        response = await _post(context, SEARCH_PATH, _body(request, page, limit))
        payload = _decode(response)
        results = payload.get("results")
        metadata = payload.get("page_metadata")
        if not isinstance(results, list) or not isinstance(metadata, Mapping):
            raise DiscoveryError(
                DiscoveryErrorCode.SCHEMA_CHANGED,
                "USAspending answered without 'results' or 'page_metadata'",
            )
        candidates: list[Candidate] = []
        for award in results:
            if not isinstance(award, Mapping):
                continue
            candidate = candidate_from_award(award)
            if candidate is not None and not _excluded(candidate, request):
                candidates.append(candidate)
        has_next = bool(metadata.get("hasNext")) and bool(results)
        return DiscoveryPage(
            candidates=candidates,
            next_cursor=str(page + 1) if has_next else None,
            request_url=f"{BASE_URL}{SEARCH_PATH}?page={page}&limit={limit}",
            http_status=response.status_code,
            body=response.content,
            content_type=response.headers.get("content-type"),
        )

    def checkpoint(self, cursor: str | None) -> JsonObject:
        return {"page": _page_number(cursor)}


SOURCE = UsaSpendingSource()
