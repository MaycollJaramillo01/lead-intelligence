"""The contract every discovery source implements. One adapter per source, never a monolith.

A source turns a campaign's criteria into candidate references. It does not crawl, does not judge
and does not create businesses: it answers "here is a page of references, here is the cursor for the
next one". The engine (`discovery/engine.py`) owns transactions, limits, retries, checkpoints and
provenance, so an adapter stays small enough to read in one sitting.

Adding a source (docs/discovery.md):

1. write a module here with a `SOURCE` instance of a class satisfying `DiscoverySource`;
2. declare honest metadata: kind, capabilities, terms, credentials, default rate limits;
3. register it in `discovery/registry.py`;
4. raise `DiscoveryError` with the right `DiscoveryErrorCode` - the retry policy reads it.

Rules that are not negotiable:

- no CAPTCHA solving, no authentication the operator has not granted, no rotating identities to get
  around a block, no private areas. A source that forbids automation declares
  `automation_allowed=False` and the registry keeps it disabled;
- a source that needs credentials declares `requires_credentials=True` and fails with
  `auth_required` until it is configured. It never invents a key;
- every candidate carries the payload exactly as received, so the raw record stays the truth.

The shapes the UI also sees (`SourceMetadata`, `Capability`, `DiscoveryErrorCode`) live in
`domain.py` with the rest of the data contract; what stays here is the behaviour.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Protocol, runtime_checkable

import httpx
from pydantic import Field

from research_engine.contracts import Contract
from research_engine.domain import (
    MAX_PAGE_SIZE,
    DiscoveryCriteria,
    DiscoveryErrorCode,
    JsonObject,
    Label,
    SourceMetadata,
    SourceSelection,
)

#: Transient by nature: the same request can succeed later without the operator changing anything.
RETRYABLE_CODES: frozenset[DiscoveryErrorCode] = frozenset(
    {
        DiscoveryErrorCode.RATE_LIMITED,
        DiscoveryErrorCode.NETWORK_ERROR,
        DiscoveryErrorCode.SOURCE_UNAVAILABLE,
    }
)


class DiscoveryError(Exception):
    """A structured failure of one source. `retry_after_s` is what the source asked us to wait."""

    def __init__(
        self,
        code: DiscoveryErrorCode,
        message: str,
        *,
        retry_after_s: float | None = None,
        detail: JsonObject | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after_s = retry_after_s
        self.detail = detail

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_CODES


class SourceRequest(Contract):
    """One campaign's criteria as they reach a single source, plus that source's own selection."""

    criteria: DiscoveryCriteria
    selection: SourceSelection
    page_size: Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE)]
    #: Per-run input a campaign cannot hold: an imported file's location, a seed list.
    run_input: JsonObject


class Candidate(Contract):
    """One discovered reference. `payload_json` is what the source returned, untouched."""

    external_id: str | None
    source_url: str | None
    payload_json: JsonObject
    #: Cheap identity guess used only to spot an obvious repeat; never an entity resolution.
    dedup_key: Annotated[str, Field(min_length=1, max_length=300)]
    name_raw: Label
    locality: str | None
    region: str | None
    postal_code: str | None
    country_code: str | None
    domain: str | None
    website_url: str | None
    phone_raw: str | None
    category_raw: str | None


class DiscoveryPage(Contract):
    """One page of results. `next_cursor` is None when the source has nothing more to give."""

    candidates: list[Candidate]
    next_cursor: str | None
    #: What was requested, recorded in `source_queries` and `acquired_pages`.
    request_url: str | None
    http_status: Annotated[int, Field(ge=100, le=599)] | None
    #: The response body when there was one: hashed and sized for `acquired_pages`.
    body: bytes | None
    content_type: str | None


class Estimate(Contract):
    """Always an estimate. Sources that cannot count return `value=None` and say why."""

    value: Annotated[int, Field(ge=0)] | None
    basis: Annotated[str, Field(min_length=1, max_length=300)]


@dataclass(frozen=True, slots=True)
class SourceContext:
    """What the engine lends an adapter for one call. Never stored, never serialised.

    `http` is None when this run may not reach the network (offline tests): a source that needs it
    says `source_unavailable` rather than opening a client of its own.
    """

    data_dir: Path | None
    http: httpx.AsyncClient | None
    now: datetime

    def client(self) -> httpx.AsyncClient:
        if self.http is None:
            raise DiscoveryError(
                DiscoveryErrorCode.SOURCE_UNAVAILABLE,
                "this run has no network access, so a remote source cannot be used",
            )
        return self.http

    def resolve(self, relative: str) -> Path:
        """Resolves a stored relative path inside the data directory, never outside it."""
        from research_engine.storage import data_path

        if self.data_dir is None:
            raise DiscoveryError(
                DiscoveryErrorCode.SOURCE_UNAVAILABLE, "the data directory is not configured"
            )
        try:
            return data_path(self.data_dir, relative)
        except ValueError as exc:
            raise DiscoveryError(DiscoveryErrorCode.INVALID_QUERY, str(exc)) from exc


@runtime_checkable
class DiscoverySource(Protocol):
    """The adapter contract. Implementations are stateless: the engine owns the cursor."""

    #: What this source is and what it may do. Constant for a connector version.
    metadata: SourceMetadata

    async def validate_config(self, config: Mapping[str, object]) -> list[str]:
        """The problems that stop this source from running, empty when it is ready."""
        ...

    async def estimate(self, request: SourceRequest, context: SourceContext) -> Estimate:
        """A marked estimate of how many records the request would yield. Never a promise."""
        ...

    async def discover(
        self, request: SourceRequest, cursor: str | None, context: SourceContext
    ) -> DiscoveryPage:
        """One page from `cursor`. Raises `DiscoveryError` for anything that goes wrong."""
        ...

    def checkpoint(self, cursor: str | None) -> JsonObject:
        """Adapter state worth persisting next to the cursor so a resume repeats no page."""
        ...
