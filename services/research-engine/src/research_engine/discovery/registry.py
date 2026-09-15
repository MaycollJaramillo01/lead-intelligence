"""The source registry: which adapters exist, what they may do, and whether they could run now.

One place decides three things, so no other module has to guess:

- the catalogue the UI shows (capabilities, terms, limits, health);
- the `source_connectors` rows the provenance chain hangs off, kept in step with the code;
- whether a campaign's criteria can actually run against the sources it selected.

Enablement is a rule, not a list: a source that forbids automated access, or that needs credentials
it does not have, stays disabled until the operator configures it. Nothing is enabled by default
just because it exists.
"""

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from research_engine.discovery.sources import (
    api_template,
    file_import,
    overture,
    seeds,
    usaspending,
)
from research_engine.discovery.sources.base import DiscoverySource
from research_engine.domain import (
    MAX_SOURCES,
    Capability,
    CriteriaProblem,
    DiscoveryCriteria,
    DiscoveryStrategy,
    JsonObject,
    ProblemSeverity,
    SourceConnector,
    SourceConnectorConfig,
    SourceHealth,
    SourceInfo,
    SourceMetadata,
    format_ts,
    new_id,
    utc_now,
)
from research_engine.repository import atomic, from_row, insert

#: Every adapter this build ships, in the order the catalogue shows them.
SOURCES: Final[dict[str, DiscoverySource]] = {
    source.metadata.key: source
    for source in (
        file_import.SOURCE,
        seeds.SOURCE,
        usaspending.SOURCE,
        overture.SOURCE,
        api_template.SOURCE,
    )
}

DEFAULT_MAX_CONCURRENCY: Final = 1
#: Which criteria field each capability covers, for the "this source ignores X" report.
_FIELD_CAPABILITY: Final[dict[str, Capability]] = {
    "geography.region": Capability.GEOGRAPHY_REGION,
    "geography.locality": Capability.GEOGRAPHY_LOCALITY,
    "geography.postal_code": Capability.GEOGRAPHY_POSTAL_CODE,
    "geography.radius_km": Capability.GEOGRAPHY_RADIUS,
    "verticals": Capability.VERTICAL_NAICS,
    "categories": Capability.CATEGORY_TERMS,
    "keywords": Capability.KEYWORD_SEARCH,
    "freshness_since": Capability.FRESHNESS,
    "size_proxies": Capability.SIZE_PROXY,
}


def source_for(key: str) -> DiscoverySource:
    source = SOURCES.get(key)
    if source is None:
        raise KeyError(f"unknown source connector '{key}'")
    return source


@dataclass(frozen=True, slots=True)
class EffectiveConfig:
    """A connector's configuration and limits after the project overrides the global defaults."""

    config_id: str | None
    config_json: JsonObject
    requests_per_minute: int
    daily_request_budget: int | None
    max_concurrency: int


def sync_connectors(conn: sqlite3.Connection) -> dict[str, SourceConnector]:
    """Makes `source_connectors` match the adapters this build ships. Returns them by key.

    A connector row is never deleted: raw records point at it, so a source that disappears from the
    code is only disabled. Enablement follows the rule above, unless an operator has changed it.
    """
    now = utc_now()
    stored = {
        row["key"]: from_row(SourceConnector, row)
        for row in conn.execute(
            f"SELECT {', '.join(SourceConnector.model_fields)} FROM source_connectors"  # noqa: S608
        )
    }
    with atomic(conn):
        for key, source in SOURCES.items():
            meta = source.metadata
            default_enabled = meta.automation_allowed and not meta.requires_credentials
            current = stored.get(key)
            if current is None:
                connector = SourceConnector(
                    id=new_id(),
                    key=key,
                    name=meta.name,
                    kind=meta.kind,
                    connector_version=meta.version,
                    terms_url=meta.terms_url,
                    requires_credentials=meta.requires_credentials,
                    enabled=default_enabled,
                    created_at=now,
                    updated_at=now,
                )
                insert(conn, connector)
                stored[key] = connector
                continue
            enabled = current.enabled and meta.automation_allowed
            if (
                current.name,
                current.kind,
                current.connector_version,
                current.terms_url,
                current.requires_credentials,
                current.enabled,
            ) == (
                meta.name,
                meta.kind,
                meta.version,
                meta.terms_url,
                meta.requires_credentials,
                enabled,
            ):
                continue
            conn.execute(
                "UPDATE source_connectors SET name = ?, kind = ?, connector_version = ?, "
                "terms_url = ?, requires_credentials = ?, enabled = ?, updated_at = ? WHERE id = ?",
                (
                    meta.name,
                    meta.kind.value,
                    meta.version,
                    meta.terms_url,
                    int(meta.requires_credentials),
                    int(enabled),
                    format_ts(now),
                    current.id,
                ),
            )
            stored[key] = current.model_copy(
                update={
                    "name": meta.name,
                    "kind": meta.kind,
                    "connector_version": meta.version,
                    "terms_url": meta.terms_url,
                    "requires_credentials": meta.requires_credentials,
                    "enabled": enabled,
                }
            )
    return {key: stored[key] for key in SOURCES}


def effective_config(
    conn: sqlite3.Connection, connector: SourceConnector, project_id: str | None
) -> EffectiveConfig:
    """The project's current configuration row, falling back to the global one, then to defaults."""
    meta = SOURCES[connector.key].metadata
    columns = ", ".join(SourceConnectorConfig.model_fields)
    rows = conn.execute(
        f"SELECT {columns} FROM source_connector_configs "  # noqa: S608 - names from the model
        "WHERE connector_id = ? AND superseded_at IS NULL "
        "AND (project_id IS ? OR project_id IS NULL) ORDER BY project_id IS NULL",
        (connector.id, project_id),
    ).fetchall()
    row = next((from_row(SourceConnectorConfig, r) for r in rows), None)
    return EffectiveConfig(
        config_id=row.id if row else None,
        config_json=row.config_json if row else {},
        requests_per_minute=(row.requests_per_minute if row else None)
        or meta.default_requests_per_minute,
        daily_request_budget=(row.daily_request_budget if row else None)
        or meta.default_daily_request_budget,
        max_concurrency=(row.max_concurrency if row else None) or DEFAULT_MAX_CONCURRENCY,
    )


async def health_of(
    connector: SourceConnector, config: Mapping[str, object]
) -> tuple[SourceHealth, str | None]:
    """Could this source run right now, and if not, why not - in one sentence the operator reads.

    `config` is whatever the source would actually see: the connector's own configuration on the
    Sources screen, and that merged with the campaign's settings when a campaign is being checked.
    A source that only misses a per-campaign setting is therefore ready as soon as a campaign
    supplies it.
    """
    meta = SOURCES[connector.key].metadata
    if not meta.automation_allowed:
        return SourceHealth.DISABLED, "This source does not allow automated access."
    if not connector.enabled:
        return SourceHealth.DISABLED, "Disabled in this workspace."
    problems = await SOURCES[connector.key].validate_config(config)
    if problems:
        return SourceHealth.NEEDS_CONFIGURATION, problems[0][:400]
    return SourceHealth.READY, None


def unsupported_fields(criteria: DiscoveryCriteria, meta: SourceMetadata) -> list[str]:
    """Criteria this source cannot honour, so the builder says so instead of dropping them."""
    used = {
        "geography.region": any(scope.region for scope in criteria.geography),
        "geography.locality": any(scope.locality for scope in criteria.geography),
        "geography.postal_code": any(scope.postal_code for scope in criteria.geography),
        "geography.radius_km": any(scope.radius_km for scope in criteria.geography),
        "verticals": bool(criteria.verticals),
        "categories": bool(criteria.categories),
        "keywords": bool(criteria.keywords),
        "freshness_since": criteria.freshness_since is not None,
        "size_proxies": bool(criteria.size_proxies),
    }
    capabilities = set(meta.capabilities)
    return [
        field
        for field, is_used in used.items()
        if is_used and _FIELD_CAPABILITY[field] not in capabilities
    ]


def _problem(severity: ProblemSeverity, code: str, path: str, message: str) -> CriteriaProblem:
    return CriteriaProblem(severity=severity, code=code, path=path, message=message)


def check_criteria(
    criteria: DiscoveryCriteria,
    catalog: Mapping[str, SourceInfo],
    readiness: Mapping[str, tuple[SourceHealth, str | None]] | None = None,
) -> list[CriteriaProblem]:
    """Everything that stops this campaign from running, plus what deserves a second look.

    Errors block a run; warnings never do. The campaign itself can always be saved as a draft.
    `readiness` is the health of each source *with this campaign's own settings applied*; without
    it the catalogue's connector-level health is used.
    """
    problems: list[CriteriaProblem] = []
    error, warning = ProblemSeverity.ERROR, ProblemSeverity.WARNING
    if not criteria.sources:
        problems.append(
            _problem(error, "no_sources", "sources", "Select at least one source to search.")
        )
    if len(criteria.sources) > MAX_SOURCES:
        problems.append(
            _problem(
                error,
                "too_many_sources",
                "sources",
                f"A campaign can use at most {MAX_SOURCES} sources.",
            )
        )
    seen: set[str] = set()
    for index, selection in enumerate(criteria.sources):
        path = f"sources[{index}]"
        key = selection.connector_key
        if key in seen:
            problems.append(
                _problem(error, "duplicate_source", path, f"'{key}' is selected twice.")
            )
            continue
        seen.add(key)
        info = catalog.get(key)
        if info is None:
            problems.append(
                _problem(error, "unknown_source", path, f"This build has no source named '{key}'.")
            )
            continue
        health, health_detail = (readiness or {}).get(key, (info.health, info.health_detail))
        if health is not SourceHealth.READY:
            detail = health_detail or "It cannot run as configured."
            problems.append(
                _problem(error, "source_not_ready", path, f"{info.metadata.name}: {detail}")
            )
        for field in unsupported_fields(criteria, info.metadata):
            problems.append(
                _problem(
                    warning,
                    "unsupported_field",
                    path,
                    f"{info.metadata.name} ignores '{field}': it cannot filter on it.",
                )
            )
    if not criteria.geography:
        problems.append(
            _problem(
                warning,
                "no_geography",
                "geography",
                "No place is set, so sources search as widely as they allow.",
            )
        )
    if criteria.strategy is DiscoveryStrategy.NARROW and not (
        criteria.verticals or criteria.categories or criteria.keywords
    ):
        problems.append(
            _problem(
                warning,
                "narrow_without_terms",
                "strategy",
                "A narrow search with no vertical, category or keyword behaves like a broad one.",
            )
        )
    if criteria.market_languages:
        problems.append(
            _problem(
                warning,
                "language_is_a_signal",
                "market_languages",
                "Market language is an affinity signal, never evidence of who owns a business.",
            )
        )
    if criteria.freshness_since and criteria.freshness_since > format_ts(utc_now())[:10]:
        problems.append(
            _problem(
                error, "freshness_in_the_future", "freshness_since", "That date is in the future."
            )
        )
    return problems


def blocking(problems: list[CriteriaProblem]) -> list[CriteriaProblem]:
    return [problem for problem in problems if problem.severity is ProblemSeverity.ERROR]
