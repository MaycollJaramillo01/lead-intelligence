"""Manual seeds: URLs, domains or names the operator already knows.

The smallest possible source, and the one that proves the contract holds for something that is not
an API: same adapter interface, same provenance, same checkpoints. A seed is a *reference*, not a
verified business - it enters the list as a candidate like any other and is resolved later.

Seeds live on the campaign (`selection.config_json["seeds"]`) so they are part of its definition,
and a run may add more through its input without editing the campaign.
"""

from collections.abc import Mapping, Sequence
from typing import Final

from research_engine.discovery.normalize import clean, dedup_key, domain_of, website_of
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
    ConfigField,
    ConfigFieldKind,
    DiscoveryErrorCode,
    JsonObject,
    SourceKind,
    SourceMetadata,
)

KEY: Final = "manual.seeds"
VERSION: Final = "1.0.0"
MAX_SEEDS: Final = 5_000
#: "Name, City ST" - the one shape worth recognising; anything else stays a plain name.
_PLACE_SEPARATOR: Final = ","


def _seed_list(source: object) -> list[str]:
    if source is None:
        return []
    if isinstance(source, str):
        return [line for line in (clean(part) or "" for part in source.splitlines()) if line]
    if isinstance(source, Sequence):
        return [clean(item) or "" for item in source if clean(item)]
    raise DiscoveryError(
        DiscoveryErrorCode.INVALID_QUERY, "seeds must be a list of strings or one line per seed"
    )


def collect_seeds(request: SourceRequest) -> list[str]:
    """Campaign seeds first, then this run's extra ones; duplicates keep their first position."""
    seen: dict[str, None] = {}
    for holder in (request.selection.config_json.get("seeds"), request.run_input.get("seeds")):
        for seed in _seed_list(holder):
            seen.setdefault(seed, None)
    if len(seen) > MAX_SEEDS:
        raise DiscoveryError(
            DiscoveryErrorCode.INVALID_QUERY, f"a run accepts at most {MAX_SEEDS} seeds"
        )
    return list(seen)


def candidate_from_seed(seed: str, index: int) -> Candidate:
    """A URL or domain becomes a web reference; anything else is a name, with an optional place."""
    domain = domain_of(seed)
    locality = region = None
    if domain is not None:
        name, website = domain, website_of(seed)
    else:
        website = None
        name, _, place = seed.partition(_PLACE_SEPARATOR)
        name = name.strip() or seed
        parts = place.split()
        if parts and len(parts[-1]) == 2 and parts[-1].isalpha():
            region, locality = parts[-1].upper(), " ".join(parts[:-1]) or None
        elif place.strip():
            locality = place.strip()
    payload: JsonObject = {"seed": seed, "position": index}
    return Candidate(
        external_id=f"seed:{index}",
        source_url=website,
        payload_json=payload,
        dedup_key=dedup_key(name=name, locality=locality, region=region, domain=domain),
        name_raw=name[:300],
        locality=locality,
        region=region,
        postal_code=None,
        country_code=None,
        domain=domain,
        website_url=website,
        phone_raw=None,
        category_raw=None,
    )


class ManualSeedsSource:
    """The operator's own list, paged like any other source so a long list can be paused."""

    metadata = SourceMetadata(
        key=KEY,
        name="Manual seeds (URLs, domains, names)",
        kind=SourceKind.MANUAL_SEED,
        version=VERSION,
        capabilities=[Capability.PAGINATION, Capability.CHECKPOINT, Capability.ESTIMATE],
        terms_url=None,
        terms_summary=(
            "Entries the operator types or pastes. Nothing is fetched and nothing is verified: a "
            "seed is a reference to look into, not a confirmed business."
        ),
        requires_credentials=False,
        automation_allowed=True,
        default_requests_per_minute=600,
        default_daily_request_budget=None,
        default_page_size=200,
        config_fields=[
            ConfigField(
                key="seeds",
                label="Seeds",
                kind=ConfigFieldKind.TEXT,
                required=True,
                help="One URL, domain or business name per line. 'Name, City ST' is understood.",
            )
        ],
    )

    async def validate_config(self, config: Mapping[str, object]) -> list[str]:
        try:
            seeds = _seed_list(config.get("seeds"))
        except DiscoveryError as exc:
            return [exc.message]
        return [] if seeds else ["no seeds yet: add at least one URL, domain or name"]

    async def estimate(self, request: SourceRequest, context: SourceContext) -> Estimate:
        return Estimate(value=len(collect_seeds(request)), basis="seeds on the list")

    async def discover(
        self, request: SourceRequest, cursor: str | None, context: SourceContext
    ) -> DiscoveryPage:
        seeds = collect_seeds(request)
        offset = _offset(cursor)
        window = seeds[offset : offset + request.page_size]
        excluded = {term.casefold() for term in request.criteria.exclusions.names}
        candidates: list[Candidate] = []
        for index, seed in enumerate(window, start=offset):
            candidate = candidate_from_seed(seed, index)
            name = candidate.name_raw.casefold()
            if not any(term in name for term in excluded):
                candidates.append(candidate)
        consumed = offset + len(window)
        return DiscoveryPage(
            candidates=candidates,
            next_cursor=str(consumed) if consumed < len(seeds) else None,
            request_url=f"seeds:{offset}-{consumed}",
            http_status=None,
            body=None,
            content_type=None,
        )

    def checkpoint(self, cursor: str | None) -> JsonObject:
        return {"seed_offset": _offset(cursor)}


def _offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        offset = int(cursor)
    except ValueError as exc:
        raise DiscoveryError(
            DiscoveryErrorCode.INVALID_QUERY, f"invalid cursor {cursor!r}"
        ) from exc
    if offset < 0:
        raise DiscoveryError(DiscoveryErrorCode.INVALID_QUERY, f"invalid cursor {cursor!r}")
    return offset


SOURCE = ManualSeedsSource()
