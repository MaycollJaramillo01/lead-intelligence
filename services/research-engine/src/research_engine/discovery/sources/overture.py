"""Overture Maps places: open business listings that carry phones, websites and emails. No key.

Why this source exists: USAspending recipients come with no phone, email or website, so the contact
engines had nothing to work on. Overture publishes about 74 million places (Meta, Microsoft,
Foursquare and others) as open Parquet. Measured on 2026-09-15 over central Houston: 23 260 places,
87 % with a phone, 48 % with a website and 23 % with an email.

How a run reads it:

1. the release comes from the STAC catalogue (`latest`) and travels in the cursor, so a resumed run
   keeps reading the same release;
2. each place of the campaign becomes a bounding box through OpenStreetMap's Nominatim, one lookup a
   second as its usage policy asks. A radius or a ZIP becomes a square around its centre, and a ZIP
   also filters the postcode;
3. one DuckDB query over the public bucket (anonymous S3, `httpfs`) writes the matching places to
   `overture/<hash>.parquet` in the data directory: places with a phone or an email that are not
   permanently closed, up to the source's record cap;
4. pages are windows of that local file, so paging and resuming never go back to S3.

The payload keeps the lists as read, plus `phone`, `email`, `website` and `address` (the first of
each) and `latitude`/`longitude`: the columns entity resolution reads to create contact points and a
location. The website is what the crawler later visits to find more emails.

Terms: CDLA Permissive 2.0 or Apache 2.0 depending on the contributor; the `sources` column keeps
each record's dataset and license.
"""

import asyncio
import hashlib
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import duckdb
import httpx
import orjson

from research_engine.discovery.normalize import clean, dedup_key, domain_of, website_of
from research_engine.discovery.sources.base import (
    Candidate,
    DiscoveryError,
    DiscoveryPage,
    Estimate,
    SourceContext,
    SourceRequest,
)

# the same exclusion rules as an imported file: names, domains, postal codes and categories
from research_engine.discovery.sources.file_import import _excluded as excluded
from research_engine.domain import (
    Capability,
    DiscoveryCriteria,
    DiscoveryErrorCode,
    GeoScope,
    JsonObject,
    SourceKind,
    SourceMetadata,
)

KEY: Final = "overture.places"
VERSION: Final = "1.1.0"
STAC_URL: Final = "https://stac.overturemaps.org/catalog.json"
NOMINATIM_URL: Final = "https://nominatim.openstreetmap.org/search"
#: The public release. Tests point it at a local Parquet file.
PLACES = "s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*"
S3_REGION: Final = "us-west-2"
CACHE_DIR: Final = "overture"
#: Nominatim's usage policy allows one request a second.
NOMINATIM_INTERVAL_S: Final = 1.1
#: Half the side of the square searched around a ZIP's centre; the postcode filter does the rest.
ZIP_RADIUS_KM: Final = 15.0
KM_PER_DEGREE: Final = 111.32
_RELEASE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}\.\d+$")
_COLUMNS: Final = (
    "id, names.primary AS name, phones[1] AS phone, emails[1] AS email, websites[1] AS website, "
    "addresses[1].freeform AS address, bbox.ymin AS latitude, bbox.xmin AS longitude, phones, "
    "emails, websites, socials, addresses, basic_category, taxonomy.primary AS category, "
    "taxonomy.hierarchy AS category_path, confidence, operating_status, sources"
)
#: A category or service: whole words of the taxonomy, so `spa` finds `day_spa` but never `space`,
#: `spanish_restaurant` or a business whose name merely contains the letters.
_CATEGORY: Final = (
    "regexp_matches(concat_ws(' ', taxonomy.primary, basic_category, "
    "array_to_string(taxonomy.hierarchy, ' ')), ?)"
)
#: A keyword: part of the business name.
_KEYWORD: Final = "names.primary ILIKE ?"


@dataclass(frozen=True, slots=True)
class Box:
    """Where to look, in degrees, plus what a ZIP or a whole-country search must also match."""

    west: float
    south: float
    east: float
    north: float
    postal_code: str | None = None
    country_code: str | None = None


async def _get_json(context: SourceContext, url: str, params: Mapping[str, str], what: str) -> Any:
    try:
        response = await context.client().get(url, params=params)
    except httpx.HTTPError as exc:
        raise DiscoveryError(
            DiscoveryErrorCode.NETWORK_ERROR, f"{what} could not be reached: {exc}"
        ) from exc
    status = response.status_code
    if status == 429:
        raise DiscoveryError(DiscoveryErrorCode.RATE_LIMITED, f"{what} asked us to slow down")
    if status in (401, 403):
        raise DiscoveryError(
            DiscoveryErrorCode.BLOCKED, f"{what} refused the request (HTTP {status})"
        )
    if status >= 500:
        raise DiscoveryError(
            DiscoveryErrorCode.SOURCE_UNAVAILABLE, f"{what} is not answering (HTTP {status})"
        )
    if status >= 400:
        raise DiscoveryError(
            DiscoveryErrorCode.INVALID_QUERY, f"{what} rejected the query (HTTP {status})"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise DiscoveryError(
            DiscoveryErrorCode.SCHEMA_CHANGED, f"{what} returned something that is not JSON"
        ) from exc


async def latest_release(context: SourceContext) -> str:
    catalog = await _get_json(context, STAC_URL, {}, "The Overture release catalogue")
    release = catalog.get("latest") if isinstance(catalog, dict) else None
    if not isinstance(release, str) or not _RELEASE.match(release):
        raise DiscoveryError(
            DiscoveryErrorCode.SCHEMA_CHANGED,
            "the Overture release catalogue no longer names its latest release",
        )
    return release


async def locate(scope: GeoScope, context: SourceContext) -> Box:
    """One campaign place as a bounding box, from one Nominatim lookup."""
    params = {"format": "jsonv2", "limit": "1"}
    if scope.region or scope.locality or scope.postal_code:
        fields = {
            "country": scope.country_code,
            "state": scope.region,
            "city": scope.locality,
            "postalcode": scope.postal_code,
        }
        params |= {key: value for key, value in fields.items() if value}
    elif scope.metro:
        params |= {"q": scope.metro, "countrycodes": scope.country_code.lower()}
    else:
        params["country"] = scope.country_code
    found = await _get_json(context, NOMINATIM_URL, params, "Nominatim")
    if not isinstance(found, list) or not found or not isinstance(found[0], dict):
        named = (scope.postal_code, scope.locality, scope.metro, scope.region, scope.country_code)
        raise DiscoveryError(
            DiscoveryErrorCode.INVALID_QUERY,
            f"Nominatim found no place called '{', '.join(filter(None, named))}'",
        )
    try:
        south, north, west, east = (float(value) for value in found[0]["boundingbox"])
        latitude, longitude = float(found[0]["lat"]), float(found[0]["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DiscoveryError(
            DiscoveryErrorCode.SCHEMA_CHANGED, "Nominatim answered without a bounding box"
        ) from exc
    if radius := scope.radius_km or (ZIP_RADIUS_KM if scope.postal_code else None):
        # ponytail: a square, not a circle; corners reach ~40 % past the radius
        half_lat = radius / KM_PER_DEGREE
        half_lon = radius / (KM_PER_DEGREE * max(0.01, math.cos(math.radians(latitude))))
        south, north = latitude - half_lat, latitude + half_lat
        west, east = longitude - half_lon, longitude + half_lon
    only_country = not (scope.region or scope.locality or scope.postal_code or scope.metro)
    return Box(
        west=west,
        south=south,
        east=east,
        north=north,
        postal_code=scope.postal_code if not scope.radius_km else None,
        country_code=scope.country_code if only_country else None,
    )


def _pattern(term: str) -> str:
    """`hair salon` also matches `hair_salon`: `_` is ILIKE's one-character wildcard."""
    return "%" + "_".join(term.replace("%", " ").split()) + "%"


def category_pattern(term: str) -> str:
    """The term as whole words of the taxonomy: `Hair Salon` becomes `hair_salon`."""
    return r"(^|[ _])" + re.escape("_".join(term.casefold().split())) + r"([ _]|$)"


def build_filter(boxes: list[Box], criteria: DiscoveryCriteria) -> tuple[str, list[object]]:
    """The WHERE clause and its values: in a box, reachable, open, of a type, named as asked."""
    params: list[object] = []
    places: list[str] = []
    for box in boxes:
        clause = "bbox.xmin BETWEEN ? AND ? AND bbox.ymin BETWEEN ? AND ?"
        params += [box.west, box.east, box.south, box.north]
        if box.postal_code:
            clause += " AND addresses[1].postcode LIKE ?"
            params.append(f"{box.postal_code}%")
        if box.country_code:
            clause += " AND addresses[1].country = ?"
            params.append(box.country_code)
        places.append(f"({clause})")
    where = [
        f"({' OR '.join(places)})",
        "(len(phones) > 0 OR len(emails) > 0)",
        "operating_status IS DISTINCT FROM 'permanently_closed'",
    ]
    categories = [*criteria.categories, *criteria.services]
    if categories:
        where.append(f"({' OR '.join(_CATEGORY for _ in categories)})")
        params += [category_pattern(term) for term in categories]
    if criteria.keywords:
        where.append(f"({' OR '.join(_KEYWORD for _ in criteria.keywords)})")
        params += [_pattern(term) for term in criteria.keywords]
    return " AND ".join(where), params


def cache_name(criteria: DiscoveryCriteria, release: str, cap: int) -> str:
    """Same release, places, terms and cap, same file. Short, far from Windows' MAX_PATH (D-091)."""
    spec = {
        "version": VERSION,
        "release": release,
        "geography": [scope.model_dump(mode="json") for scope in criteria.geography],
        "categories": [*criteria.categories, *criteria.services],
        "keywords": criteria.keywords,
        "cap": cap,
    }
    digest = hashlib.sha256(orjson.dumps(spec, option=orjson.OPT_SORT_KEYS)).hexdigest()
    return f"{digest[:16]}.parquet"


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def materialise(target: Path, places: str, where: str, params: list[object], cap: int) -> None:
    """The one remote query of a run: the matching places, written to the data directory."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial")
    duck = duckdb.connect()
    try:
        if places.startswith("s3://"):
            duck.execute("INSTALL httpfs")  # downloaded once per machine by DuckDB itself
            duck.execute("LOAD httpfs")
            duck.execute(f"SET s3_region = {_literal(S3_REGION)}")
        duck.execute(
            f"COPY (SELECT {_COLUMNS} FROM read_parquet({_literal(places)}) "  # noqa: S608 - values are bound
            f"WHERE {where} LIMIT {int(cap)}) TO {_literal(str(partial))} (FORMAT parquet)",
            params,
        )
    except duckdb.IOException as exc:
        raise DiscoveryError(
            DiscoveryErrorCode.NETWORK_ERROR,
            f"the Overture release could not be read: {str(exc)[:300]}",
        ) from exc
    except duckdb.Error as exc:
        raise DiscoveryError(
            DiscoveryErrorCode.SCHEMA_CHANGED,
            f"the Overture release no longer has the expected shape: {str(exc)[:300]}",
        ) from exc
    finally:
        duck.close()
    partial.replace(target)


def window(target: Path, offset: int, size: int) -> tuple[list[dict[str, Any]], int]:
    """Rows `offset` to `offset + size` of the local copy, in a stable order, and its row count."""
    duck = duckdb.connect()
    try:
        counted = duck.execute("SELECT count(*) FROM read_parquet(?)", [str(target)]).fetchone()
        duck.execute(
            "SELECT * FROM read_parquet(?) ORDER BY id LIMIT ? OFFSET ?",
            [str(target), size, offset],
        )
        columns = [column[0] for column in duck.description or []]
        rows = [dict(zip(columns, row, strict=True)) for row in duck.fetchall()]
    except duckdb.Error as exc:
        raise DiscoveryError(
            DiscoveryErrorCode.SOURCE_UNAVAILABLE,
            f"the local copy of the Overture places cannot be read: {str(exc)[:300]}",
        ) from exc
    finally:
        duck.close()
    return rows, int(counted[0]) if counted else 0


def parse_cursor(cursor: str | None) -> tuple[str | None, int]:
    """`<release>:<row offset>`, or nothing before the first page."""
    if cursor is None:
        return None, 0
    release, _, offset = cursor.rpartition(":")
    if not _RELEASE.match(release) or not offset.isdigit():
        raise DiscoveryError(DiscoveryErrorCode.INVALID_QUERY, f"invalid cursor {cursor!r}")
    return release, int(offset)


def candidate_from_place(row: Mapping[str, Any]) -> Candidate | None:
    """One place as a candidate. Rows without an id or a name are skipped."""
    place_id, name = clean(row.get("id")), clean(row.get("name"))
    if place_id is None or name is None:
        return None
    addresses = row.get("addresses")
    first = addresses[0] if isinstance(addresses, list) and addresses else None
    address: Mapping[str, Any] = first if isinstance(first, Mapping) else {}
    locality, region = clean(address.get("locality")), clean(address.get("region"))
    website = website_of(clean(row.get("website")))
    return Candidate(
        external_id=place_id,
        source_url=None,  # a place has no public page of its own
        payload_json=dict(row),
        dedup_key=dedup_key(name=name, locality=locality, region=region, external_id=place_id),
        name_raw=name[:300],
        locality=locality,
        region=region,
        postal_code=clean(address.get("postcode")),
        country_code=(clean(address.get("country")) or "")[:2].upper() or None,
        domain=domain_of(website),
        website_url=website,
        phone_raw=clean(row.get("phone")),
        category_raw=clean(row.get("category")) or clean(row.get("basic_category")),
    )


class OverturePlacesSource:
    """Places of the latest Overture release that can be reached by phone or email."""

    metadata = SourceMetadata(
        key=KEY,
        name="Overture Maps places (phones, websites, emails)",
        kind=SourceKind.PUBLIC_DATASET,
        version=VERSION,
        capabilities=[
            Capability.PAGINATION,
            Capability.CHECKPOINT,
            Capability.GEOGRAPHY_REGION,
            Capability.GEOGRAPHY_LOCALITY,
            Capability.GEOGRAPHY_POSTAL_CODE,
            Capability.GEOGRAPHY_RADIUS,
            Capability.CATEGORY_TERMS,
            Capability.KEYWORD_SEARCH,
        ],
        terms_url="https://docs.overturemaps.org/attribution/",
        terms_summary=(
            "Open data of the Overture Maps Foundation (CDLA Permissive 2.0 or Apache 2.0 by "
            "contributor), read anonymously from its public release. Each place of the campaign is "
            "located with OpenStreetMap's Nominatim, one lookup a second as its policy asks. Only "
            "places with a phone or an email are kept."
        ),
        requires_credentials=False,
        automation_allowed=True,
        # only the first page of a run reaches the network; the others read the local copy
        default_requests_per_minute=600,
        default_daily_request_budget=None,
        default_page_size=500,
        config_fields=[],
    )

    async def validate_config(self, config: Mapping[str, object]) -> list[str]:
        return []

    async def estimate(self, request: SourceRequest, context: SourceContext) -> Estimate:
        return Estimate(
            value=None,
            basis="Overture has no count service; a run reads the matching places once",
        )

    async def discover(
        self, request: SourceRequest, cursor: str | None, context: SourceContext
    ) -> DiscoveryPage:
        release, offset = parse_cursor(cursor)
        if not request.criteria.geography:
            raise DiscoveryError(
                DiscoveryErrorCode.INVALID_QUERY,
                "add a place to the campaign: Overture covers the whole world",
            )
        if context.data_dir is None:
            raise DiscoveryError(
                DiscoveryErrorCode.SOURCE_UNAVAILABLE, "the data directory is not configured"
            )
        release = release or await latest_release(context)
        cap = request.selection.max_records or request.criteria.limits.max_candidates
        target = context.data_dir / CACHE_DIR / cache_name(request.criteria, release, cap)
        places = PLACES.format(release=release)
        if not target.exists():
            boxes: list[Box] = []
            for scope in request.criteria.geography:
                if boxes:
                    await asyncio.sleep(NOMINATIM_INTERVAL_S)
                boxes.append(await locate(scope, context))
            where, params = build_filter(boxes, request.criteria)
            await asyncio.to_thread(materialise, target, places, where, params, cap)
        rows, total = await asyncio.to_thread(window, target, offset, request.page_size)
        candidates = [
            candidate
            for row in rows
            if (candidate := candidate_from_place(row)) is not None
            and not excluded(candidate, request)
        ]
        consumed = offset + len(rows)
        return DiscoveryPage(
            candidates=candidates,
            next_cursor=f"{release}:{consumed}" if rows and consumed < total else None,
            request_url=f"{places}#rows={offset}",
            http_status=None,
            body=None,
            content_type=None,
        )

    def checkpoint(self, cursor: str | None) -> JsonObject:
        release, offset = parse_cursor(cursor)
        return {"release": release, "row_offset": offset}


SOURCE = OverturePlacesSource()
