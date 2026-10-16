"""Lead Explorer queries (phase 12).

A window of a run's leads: filters (a bounded tree of conditions) and a quick search become one
parameterised `WHERE` over `lead_index`, up to four sort keys one `ORDER BY` with the business id as
the last tie-break, and only `limit` rows are read at `offset`. Live fields join their tables only
when a filter or a sort needs them; the page's stage, research state, tags and suppression are read
for its rows alone. The same query resolves the leads of a bulk action, previews an export and
compares leads side by side.
"""

import sqlite3
from collections import defaultdict
from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta
from typing import Final

import orjson

from research_engine import repository
from research_engine.domain import (
    BULK_MAX_IDS,
    BULK_MAX_MATCHING,
    EXPLORER_FIELDS,
    EXPLORER_FILTER_ONLY,
    EXPLORER_MAX_CHILDREN,
    EXPLORER_MAX_CONDITIONS,
    EXPLORER_MAX_DEPTH,
    EXPLORER_MAX_OFFSET,
    EXPLORER_MAX_SORT,
    MAX_COMPARED,
    MAX_PAGE_SIZE,
    STALE_AFTER_DAYS,
    VIEW_SCHEMA_VERSION,
    ComparedLead,
    ExplorerCatalog,
    ExplorerCondition,
    ExplorerField,
    ExplorerFieldType,
    ExplorerGroup,
    ExplorerOperator,
    ExplorerPage,
    ExplorerQuery,
    ExplorerRow,
    ExplorerSort,
    ExportPreview,
    ExportPreviewRequest,
    LeadCompareQuery,
    LeadComparison,
    LeadIndexRow,
    LeadScorecard,
    LeadStage,
    LeadTier,
    PhoneTwin,
    ProblemType,
    ResearchStatus,
    ScoreRun,
    ScoreRunItem,
    SortDirection,
    ViewColumn,
    ViewDefinition,
    WebsiteAuditStatus,
    format_ts,
    no_filters,
    sort_key,
)
from research_engine.leads import LeadError, index
from research_engine.scoring import store as scoring_store

_F: Final = ExplorerField
_OP: Final = ExplorerOperator
MAX_TAGS_SHOWN: Final = 10
#: A chain's switchboard can sit on many records: a row names the first few.
MAX_TWINS_SHOWN: Final = 5
DEFAULT_COLUMNS: Final = (
    _F.RANK, _F.NAME, _F.TIER, _F.TOTAL_SCORE, _F.CONFIDENCE, _F.RESEARCH_STATUS, _F.LEAD_STAGE,
    _F.OPPORTUNITY_SCORE, _F.DIGITAL_GAP, _F.CONTACTABILITY, _F.MAIN_CHANNEL, _F.LATINO_STATUS,
    _F.TOP_PROBLEM, _F.OFFER, _F.FACTS_AGE_DAYS, _F.LOCALITY, _F.REGION, _F.CATEGORY,
)  # fmt: skip
_COLUMN: Final[dict[ExplorerField, str]] = {
    _F.NAME: "li.name_sort_key", _F.LOCALITY: "li.locality", _F.REGION: "li.region",
    _F.CATEGORY: "li.primary_category", _F.DOMAIN: "li.primary_domain", _F.TIER: "li.tier",
    _F.RANK: "li.rank", _F.TOTAL_SCORE: "li.total_score", _F.CONFIDENCE: "li.confidence",
    _F.DATA_COVERAGE: "li.data_coverage", _F.ICP_MATCH: "li.icp_match",
    _F.OPPORTUNITY_SCORE: "li.opportunity_score", _F.DIGITAL_GAP: "li.digital_gap",
    _F.BUSINESS_HEALTH: "li.business_health", _F.BUYING_CAPACITY: "li.buying_capacity",
    _F.CONTACTABILITY: "li.contactability", _F.COMMERCIAL_INTENT: "li.commercial_intent",
    _F.DATA_CONFIDENCE: "li.data_confidence", _F.LATINO_STATUS: "li.latino_status",
    _F.LATINO_CONFIDENCE: "li.latino_confidence", _F.MAIN_CHANNEL: "li.main_channel",
    _F.CHANNEL_STATUS: "li.channel_status", _F.USABLE_CHANNELS: "li.usable_channels",
    _F.TOP_PROBLEM: "li.top_problem", _F.OFFER: "li.offer_key", _F.AUDIT_STATUS: "li.audit_status",
    _F.CONFLICTS: "li.conflicts",
    _F.PARTIAL: "(li.tier = 'insufficient_data' "
                "OR coalesce(li.audit_status IN ('partial', 'empty'), 0))",
    _F.LEAD_STAGE: "b.lead_stage", _F.RESEARCH_STATUS: "lr.status", _F.ASSIGNEE: "lr.assignee",
}  # fmt: skip
_JOINS: Final[dict[ExplorerField, str]] = {
    _F.LEAD_STAGE: "business",
    _F.RESEARCH_STATUS: "research",
    _F.ASSIGNEE: "research",
}
_COMPARISON: Final = {_OP.LT: "<", _OP.LTE: "<=", _OP.GT: ">", _OP.GTE: ">="}
#: Fields a lead always has: sorted without NULLS LAST, so an index can keep their order.
_NOT_NULL: Final = frozenset(
    {_F.NAME, _F.TIER, _F.CONFIDENCE, _F.DATA_COVERAGE, _F.LATINO_STATUS, _F.USABLE_CHANNELS,
     _F.CONFLICTS, _F.FACTS_AGE_DAYS, _F.LEAD_STAGE, _F.PARTIAL, _F.STALE, _F.SUPPRESSED}
)  # fmt: skip
#: Live conditions are sets read once per query, never a subquery per lead.
_TAGGED: Final = (
    "SELECT et.entity_id FROM entity_tags et JOIN tags t ON t.id = et.tag_id "
    "WHERE et.entity_type = 'business' AND t.deleted_at IS NULL AND lower(t.name) = lower(?)"
)
_SUPPRESSED_ROOTS: Final = (
    "SELECT s.value FROM contact_suppressions s WHERE s.lifted_at IS NULL AND s.kind = 'business' "
    "UNION SELECT m.merged_into_id FROM businesses m JOIN contact_suppressions s ON s.value = m.id "
    "WHERE s.lifted_at IS NULL AND s.kind = 'business' AND m.merged_into_id IS NOT NULL"
)
_SEARCH: Final = (
    "li.business_id IN (SELECT coalesce(m.merged_into_id, m.id) FROM businesses m "
    "WHERE m.seq IN (SELECT rowid FROM business_search WHERE business_search MATCH ?))"
)
#: The businesses a campaign's discovery found: one of their records came from its candidates.
_FOUND_BY: Final = (
    "SELECT coalesce(b.merged_into_id, b.id) FROM entity_records er "
    "JOIN discovery_candidates dc ON dc.id = er.candidate_id "
    "JOIN businesses b ON b.id = er.business_id WHERE dc.campaign_id = ?"
)


def _usable(alias: str) -> str:
    """A contact an export writes (`exports.best_contacts`): published, off the do-not-contact
    list, not invalid nor stale."""
    return (
        f"{alias}.deleted_at IS NULL AND {alias}.do_not_contact = 0 "
        f"AND {alias}.origin <> 'hypothesis' AND {alias}.validation_status IN "
        f"({', '.join(repr(s.value) for s in index.STATUS_RANK)})"
    )


#: The businesses with a usable contact of one kind, theirs or of a record merged into them.
_REACHABLE: Final = (
    "SELECT coalesce(b.merged_into_id, b.id) FROM contact_points c "  # noqa: S608
    f"JOIN businesses b ON b.id = c.business_id WHERE c.contact_type = ? AND {_usable('c')}"
)
_PARTIAL_AUDITS: Final = (WebsiteAuditStatus.PARTIAL, WebsiteAuditStatus.EMPTY)

#: Set fields a lead belongs to through a table: `has` is one uncorrelated subquery per query.
_MEMBERSHIP: Final = {_F.TAG: _TAGGED, _F.CAMPAIGN: _FOUND_BY, _F.REACHABLE_BY: _REACHABLE}

Sql = tuple[str, list[object], set[str]]


def suppressed_roots(conn: sqlite3.Connection) -> set[str]:
    """Every business on the do-not-contact list now, as the business its records merged into."""
    return {str(row[0]) for row in conn.execute(_SUPPRESSED_ROOTS)}


def unsuppressed_clause() -> str:
    """A condition over `lead_index li` leaving out the leads on the do-not-contact list now."""
    return f"NOT (li.business_id IN ({_SUPPRESSED_ROOTS}))"


def suppressed_sql(column: str) -> str:
    """A live do-not-contact entry for the business in `column` or a record merged into it."""
    return (
        "EXISTS (SELECT 1 FROM contact_suppressions s WHERE s.lifted_at IS NULL "  # noqa: S608
        f"AND s.kind = 'business' AND (s.value = {column} OR s.value IN "
        f"(SELECT m.id FROM businesses m WHERE m.merged_into_id = {column})))"
    )


def _marks(items: Sequence[object]) -> str:
    return ", ".join("?" * len(items))


def _stale_before(now: datetime) -> datetime:
    return now - timedelta(days=STALE_AFTER_DAYS)


def default_view() -> ViewDefinition:
    shown = set(DEFAULT_COLUMNS)
    return ViewDefinition(
        schema_version=VIEW_SCHEMA_VERSION,
        score_run_id=None,
        filters=no_filters(),
        search=None,
        sort=[],
        columns=[
            ViewColumn(field=field, visible=True, pinned=field in (_F.RANK, _F.NAME), width=None)
            for field in DEFAULT_COLUMNS
        ]
        + [
            ViewColumn(field=field, visible=False, pinned=False, width=None)
            for field in ExplorerField
            if field not in shown and field not in EXPLORER_FILTER_ONLY
        ],
    )


def catalog() -> ExplorerCatalog:
    return ExplorerCatalog(
        fields=[EXPLORER_FIELDS[field] for field in ExplorerField],
        max_depth=EXPLORER_MAX_DEPTH,
        max_conditions=EXPLORER_MAX_CONDITIONS,
        max_children=EXPLORER_MAX_CHILDREN,
        max_sort=EXPLORER_MAX_SORT,
        max_page=MAX_PAGE_SIZE,
        max_offset=EXPLORER_MAX_OFFSET,
        bulk_max_ids=BULK_MAX_IDS,
        bulk_max_matching=BULK_MAX_MATCHING,
        stale_after_days=STALE_AFTER_DAYS,
        max_compared=MAX_COMPARED,
        view_schema_version=VIEW_SCHEMA_VERSION,
        default_view=default_view(),
    )


# ponytail: a finished run's item is read once per process: its distribution and tier counts scan
# every score of the run, which no window may repeat. A project renamed afterwards keeps its old
# name here until the worker restarts; keep a per-run version stamp if names become editable.
_finished_items: dict[str, ScoreRunItem] = {}


def run_item(conn: sqlite3.Connection, run: ScoreRun) -> ScoreRunItem:
    """The run a window belongs to, as the explorer shows it."""
    if run.finished_at is None:
        return scoring_store.run_item(conn, run.id)
    item = _finished_items.get(run.id)
    if item is None:
        item = _finished_items[run.id] = scoring_store.run_item(conn, run.id)
    return item


def run_of(conn: sqlite3.Connection, score_run_id: str | None) -> ScoreRun | None:
    """The run asked for, or the newest finished one."""
    if score_run_id is not None:
        return repository.get(conn, ScoreRun, score_run_id)
    row = conn.execute(
        "SELECT id FROM score_runs WHERE finished_at IS NOT NULL "
        "ORDER BY finished_at DESC, id DESC LIMIT 1"
    ).fetchone()
    return None if row is None else repository.get(conn, ScoreRun, row["id"])


# ---- compiling a query --------------------------------------------------------------------------


def _expression(field: ExplorerField, now: datetime) -> Sql:
    """The SQL of a field over `lead_index li`, its parameters and the joins it needs."""
    if field is _F.FACTS_AGE_DAYS:
        age = "CAST(julianday(?) - julianday(li.facts_collected_at) AS INTEGER)"
        return age, [format_ts(now)], set()
    if field is _F.STALE:
        # every timestamp has one format, so an older one is a smaller string
        return "(li.facts_collected_at < ?)", [format_ts(_stale_before(now))], set()
    if field is _F.SUPPRESSED:
        return f"(li.business_id IN ({_SUPPRESSED_ROOTS}))", [], set()
    joins = {_JOINS[field]} if field in _JOINS else set()
    return _COLUMN[field], [], joins


def _condition(node: ExplorerCondition, now: datetime) -> Sql:
    op, value = node.operator, node.value
    if node.field in _MEMBERSHIP:
        within = "IN" if op is _OP.HAS else "NOT IN"
        return f"li.business_id {within} ({_MEMBERSHIP[node.field]})", [value], set()
    if node.field is _F.PROBLEM:
        bit = index.PROBLEM_BITS[ProblemType(str(value))]
        return f"(li.problem_mask & ?) {'<>' if op is _OP.HAS else '='} 0", [bit], set()
    expr, params, joins = _expression(node.field, now)
    if op in (_OP.IS_TRUE, _OP.IS_FALSE):
        return (expr if op is _OP.IS_TRUE else f"NOT {expr}"), params, joins
    if op in (_OP.IS_NULL, _OP.IS_NOT_NULL):
        return f"{expr} IS {'' if op is _OP.IS_NULL else 'NOT '}NULL", params, joins
    text = EXPLORER_FIELDS[node.field].type is ExplorerFieldType.TEXT
    column, mark = (f"lower({expr})", "lower(?)") if text else (expr, "?")
    if node.field is _F.NAME:
        # a name is compared by its sort key, so case, accents and spacing never matter
        column, mark = expr, "?"
        value = [sort_key(v) for v in value] if isinstance(value, list) else sort_key(str(value))
    values = list(value) if isinstance(value, list) else [value]
    if op is _OP.EQ:
        return f"{column} = {mark}", [*params, value], joins
    if op is _OP.NEQ:
        return f"({expr} IS NULL OR {column} <> {mark})", [*params, *params, value], joins
    if op is _OP.IN:
        return f"{column} IN ({', '.join([mark] * len(values))})", [*params, *values], joins
    if op is _OP.NOT_IN:
        marks = ", ".join([mark] * len(values))
        return f"({expr} IS NULL OR {column} NOT IN ({marks}))", [*params, *params, *values], joins
    if op in _COMPARISON:
        return f"{expr} {_COMPARISON[op]} ?", [*params, value], joins
    if op is _OP.CONTAINS:
        return f"instr({column}, lower(?)) > 0", [*params, value], joins
    if op is _OP.STARTS_WITH:
        return f"substr({column}, 1, length(?)) = lower(?)", [*params, value, value], joins
    raise LeadError(f"'{op}' does not apply to {node.field}")


def _group(group: ExplorerGroup, now: datetime) -> Sql:
    """An empty group, at any depth, filters nothing. A test on a missing value is false, so a
    negated group keeps the leads it did not match, missing values included."""
    parts: list[str] = []
    params: list[object] = []
    joins: set[str] = set()
    for child in group.children:
        sql, child_params, child_joins = (
            _group(child, now) if isinstance(child, ExplorerGroup) else _condition(child, now)
        )
        if sql:
            parts.append(sql)
            params += child_params
            joins |= child_joins
    if not parts:
        return "", [], set()
    sql = "(" + f" {group.join.upper()} ".join(parts) + ")"
    return (f"NOT coalesce({sql}, 0)" if group.negate else sql), params, joins


def _where(
    run_id: str, filters: ExplorerGroup, search: str | None, now: datetime
) -> tuple[str, list[object], set[str], bool]:
    """The conditions of a query, and whether any of them narrows the run."""
    clauses, params = ["li.score_run_id = ?"], list[object]([run_id])
    sql, filter_params, joins = _group(filters, now)
    if sql:
        clauses.append(sql)
        params += filter_params
    if (match := repository.fts_query(search)) is not None:
        clauses.append(_SEARCH)
        params.append(match)
    return " AND ".join(clauses), params, joins, len(clauses) > 1


def _order(sort: Sequence[ExplorerSort], now: datetime, finished: bool) -> Sql:
    """Missing values last whatever the direction; the business id breaks the remaining ties.

    A finished run's rank is unique: without keys the run keeps it, walking `lead_index_by_rank`
    instead of sorting the run, and a rank key needs no tie-break. An open run has no rank yet and
    is sorted in the same order. SQLite puts NULL first ascending and last descending, so only an
    ascending key on a field that may be missing asks for NULLS LAST, which no index can walk.
    Tiers sort by their order, best first."""
    if not sort:
        if finished:
            return "li.rank", [], set()
        return "li.tier_order, li.total_score DESC, li.confidence DESC, li.business_id", [], set()
    parts: list[str] = []
    params: list[object] = []
    joins: set[str] = set()
    for key in sort:
        if key.field in (_F.NAME, _F.TIER):
            expr = "li.name_sort_key" if key.field is _F.NAME else "li.tier_order"
            key_params, key_joins = list[object](), set[str]()
        else:
            expr, key_params, key_joins = _expression(key.field, now)
            if EXPLORER_FIELDS[key.field].type is ExplorerFieldType.TEXT:
                expr = f"lower({expr})"
        present = key.field in _NOT_NULL or (key.field is _F.RANK and finished)
        if key.direction is SortDirection.DESC:
            parts.append(f"{expr} DESC")
        else:
            parts.append(f"{expr} ASC" if present else f"{expr} ASC NULLS LAST")
        params += key_params
        joins |= key_joins
    if not (finished and any(key.field is _F.RANK for key in sort)):
        parts.append("li.business_id")
    return ", ".join(parts), params, joins


#: Orders an index holds for a single key, besides a finished run's rank.
_WALKED: Final = frozenset(
    {
        (_F.NAME, SortDirection.ASC),
        (_F.NAME, SortDirection.DESC),
        (_F.TOTAL_SCORE, SortDirection.DESC),
    }
)


def _walked(sort: Sequence[ExplorerSort], finished: bool) -> bool:
    """Whether an index already holds the order asked for: a finished run's rank, or one key of
    `lead_index_by_name` or of `lead_index_by_total` (descending). Any other order is sorted from
    `lead_index_scan`, never from the table."""
    if not sort:
        return finished
    if len(sort) > 1:
        return False
    key = sort[0]
    return (key.field is _F.RANK and finished) or (key.field, key.direction) in _WALKED


def _source(joins: set[str], scan: bool) -> str:
    """`scan` reads `lead_index_scan` alone: every column a condition or a sort reads is in it, so
    neither a selective condition nor an order no index holds makes the table be read."""
    source = "FROM lead_index li INDEXED BY lead_index_scan" if scan else "FROM lead_index li"
    if "business" in joins:
        source += " JOIN businesses b ON b.id = li.business_id"
    if "research" in joins:
        source += " LEFT JOIN lead_research lr ON lr.business_id = li.business_id"
    return source


# ---- reading ------------------------------------------------------------------------------------


def searches_of(conn: sqlite3.Connection, ids: Sequence[str]) -> dict[str, list[str]]:
    """The live searches whose discovery found each business or a record merged into it, by name."""
    found: dict[str, list[str]] = defaultdict(list)
    marks = _marks(ids)
    for row in conn.execute(
        "SELECT DISTINCT coalesce(b.merged_into_id, b.id) AS root, cp.name FROM businesses b "  # noqa: S608
        "JOIN entity_records er ON er.business_id = b.id "
        "JOIN discovery_candidates dc ON dc.id = er.candidate_id "
        "JOIN campaigns cp ON cp.id = dc.campaign_id "
        f"WHERE (b.id IN ({marks}) OR b.merged_into_id IN ({marks})) AND cp.deleted_at IS NULL "
        "ORDER BY cp.name",
        [*ids, *ids],
    ):
        found[row["root"]].append(row["name"])
    return found


def phone_twins(conn: sqlite3.Connection, ids: Sequence[str]) -> dict[str, list[PhoneTwin]]:
    """Per business, the other live businesses that publish one of its usable phones, in any live
    project as synthetic as its own: the lists that would have two people call one line."""
    pairs: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    marks = _marks(ids)
    for row in conn.execute(
        "SELECT DISTINCT coalesce(b.merged_into_id, b.id) AS root, t.id, t.display_name, "  # noqa: S608
        "p.name AS project FROM contact_points c JOIN businesses b ON b.id = c.business_id "
        "JOIN projects lp ON lp.id = b.project_id "
        "JOIN contact_points o ON o.contact_type = 'phone' "
        "AND o.value_normalized = c.value_normalized "
        "JOIN businesses ob ON ob.id = o.business_id "
        "JOIN businesses t ON t.id = coalesce(ob.merged_into_id, ob.id) "
        "JOIN projects p ON p.id = t.project_id "
        f"WHERE c.contact_type = 'phone' AND {_usable('c')} AND {_usable('o')} "
        f"AND (b.id IN ({marks}) OR b.merged_into_id IN ({marks})) "
        "AND t.id <> coalesce(b.merged_into_id, b.id) AND t.deleted_at IS NULL "
        "AND p.deleted_at IS NULL AND p.is_synthetic = lp.is_synthetic "
        "ORDER BY t.display_name, t.id",
        [*ids, *ids],
    ):
        if len(pairs[row["root"]]) < MAX_TWINS_SHOWN:
            pairs[row["root"]].append((row["id"], row["display_name"], row["project"]))
    twins = list({twin for listed in pairs.values() for twin, _, _ in listed})
    searches = searches_of(conn, twins) if twins else {}
    return {
        root: [
            PhoneTwin(
                business_id=twin,
                display_name=name,
                project_name=project,
                searches=searches.get(twin, []),
            )
            for twin, name, project in listed
        ]
        for root, listed in pairs.items()
    }


def rows_of(
    conn: sqlite3.Connection, found: Sequence[LeadIndexRow], now: datetime
) -> list[ExplorerRow]:
    """Explorer rows for index rows, with the live state of these businesses only."""
    if not found:
        return []
    ids = [row.business_id for row in found]
    marks = _marks(ids)
    live = {
        row["id"]: row
        for row in conn.execute(
            f"SELECT b.id, b.lead_stage, lr.status, lr.assignee, {suppressed_sql('b.id')} "  # noqa: S608
            "AS suppressed FROM businesses b LEFT JOIN lead_research lr ON lr.business_id = b.id "
            f"WHERE b.id IN ({marks})",
            ids,
        )
    }
    tags: dict[str, list[str]] = defaultdict(list)
    for row in conn.execute(
        "SELECT et.entity_id, t.name FROM entity_tags et JOIN tags t ON t.id = et.tag_id "  # noqa: S608
        f"WHERE et.entity_type = 'business' AND t.deleted_at IS NULL AND et.entity_id IN ({marks}) "
        "ORDER BY t.name",
        ids,
    ):
        tags[row["entity_id"]].append(row["name"])
    searches = searches_of(conn, ids)
    twins = phone_twins(conn, ids)
    rows: list[ExplorerRow] = []
    stale_after = timedelta(days=STALE_AFTER_DAYS)
    for row in found:
        state = live.get(row.business_id)
        age = now - row.facts_collected_at
        rows.append(
            ExplorerRow(
                business_id=row.business_id,
                project_id=row.project_id,
                rank=row.rank,
                display_name=row.display_name,
                locality=row.locality,
                region=row.region,
                category=row.primary_category,
                domain=row.primary_domain,
                lead_stage=LeadStage(state["lead_stage"]) if state else LeadStage.NOT_CONTACTED,
                research_status=ResearchStatus(state["status"])
                if state and state["status"]
                else None,
                assignee=state["assignee"] if state else None,
                tags=tags[row.business_id][:MAX_TAGS_SHOWN],
                tier=row.tier,
                total_score=row.total_score,
                confidence=row.confidence,
                data_coverage=row.data_coverage,
                icp_match=row.icp_match,
                opportunity_score=row.opportunity_score,
                digital_gap=row.digital_gap,
                business_health=row.business_health,
                buying_capacity=row.buying_capacity,
                contactability=row.contactability,
                commercial_intent=row.commercial_intent,
                data_confidence=row.data_confidence,
                latino_status=row.latino_status,
                latino_confidence=row.latino_confidence,
                main_channel=row.main_channel,
                channel_status=row.channel_status,
                usable_channels=row.usable_channels,
                problems=index.problems_of(row.problem_mask),
                top_problem=ProblemType(row.top_problem) if row.top_problem else None,
                top_reason=row.top_reason,
                offer=row.offer_key,
                facts_collected_at=row.facts_collected_at,
                facts_age_days=max(0, age.days),
                audit_status=row.audit_status,
                conflicts=row.conflicts,
                suppressed=bool(state["suppressed"]) if state else row.suppressed_when_scored,
                stale=age > stale_after,
                partial=row.tier is LeadTier.INSUFFICIENT_DATA
                or row.audit_status in _PARTIAL_AUDITS,
                is_synthetic=row.is_synthetic,
                searches=searches.get(row.business_id, []),
                same_phone=twins.get(row.business_id, []),
            )
        )
    return rows


def _statements(
    query: ExplorerQuery, run: ScoreRun, now: datetime, extra: str = ""
) -> tuple[tuple[str, list[object]] | None, tuple[str, list[object]]]:
    """A window's statements: its count when asked for, and the ids of its leads in order. `extra`
    is a parameterless condition added to the query."""
    finished = run.finished_at is not None
    where, params, joins, narrowed = _where(run.id, query.filters, query.search, now)
    if extra:
        where, narrowed = f"{where} AND {extra}", True
    order, order_params, order_joins = _order(query.sort, now, finished)
    counted: tuple[str, list[object]] | None = None
    if query.with_total:
        counted = f"SELECT count(*) {_source(joins, narrowed)} WHERE {where}", params
    scan = narrowed or not _walked(query.sort, finished)
    ids = (
        f"SELECT li.business_id {_source(joins | order_joins, scan)} WHERE {where} "
        f"ORDER BY {order} LIMIT ? OFFSET ?",
        [*params, *order_params, query.limit, query.offset],
    )
    return counted, ids


def _window(
    conn: sqlite3.Connection, query: ExplorerQuery, run: ScoreRun, now: datetime, extra: str = ""
) -> tuple[list[LeadIndexRow], int | None]:
    """One window of the query; `extra` is a parameterless condition added to it. The window's
    leads are found in the indexes, and only their rows are read from the table."""
    counted, (sql, params) = _statements(query, run, now, extra)
    total = None if counted is None else int(conn.execute(*counted).fetchone()[0])
    ids = [row["business_id"] for row in conn.execute(sql, params)]
    if not ids:
        return [], total
    marks = _marks(ids)
    found = {
        row["business_id"]: repository.from_row(LeadIndexRow, row)
        for row in conn.execute(
            f"SELECT * FROM lead_index WHERE score_run_id = ? AND business_id IN ({marks})",  # noqa: S608
            [run.id, *ids],
        )
    }
    return [found[business_id] for business_id in ids], total


def rows_for(
    conn: sqlite3.Connection, run: ScoreRun, ids: Sequence[str], now: datetime
) -> list[ExplorerRow]:
    """Explorer rows of these leads of a run, in the order given (leads the run lacks are left
    out)."""
    if not ids:
        return []
    found = {
        row["business_id"]: repository.from_row(LeadIndexRow, row)
        for row in conn.execute(
            f"SELECT * FROM lead_index WHERE score_run_id = ? AND business_id IN ({_marks(ids)})",  # noqa: S608
            [run.id, *ids],
        )
    }
    return rows_of(conn, [found[business_id] for business_id in ids if business_id in found], now)


def stream_ids(
    conn: sqlite3.Connection, query: ExplorerQuery, run: ScoreRun, now: datetime, batch: int
) -> Iterator[list[str]]:
    """Every lead a query matches, in its order, in batches: one pass over the indexes, never a
    window at a deep offset (an export of a whole run)."""
    finished = run.finished_at is not None
    where, params, joins, narrowed = _where(run.id, query.filters, query.search, now)
    order, order_params, order_joins = _order(query.sort, now, finished)
    scan = narrowed or not _walked(query.sort, finished)
    cursor = conn.execute(
        f"SELECT li.business_id {_source(joins | order_joins, scan)} WHERE {where} "
        f"ORDER BY {order}",
        [*params, *order_params],
    )
    while rows := cursor.fetchmany(batch):
        yield [str(row[0]) for row in rows]


def explore(conn: sqlite3.Connection, query: ExplorerQuery, now: datetime) -> ExplorerPage:
    run = run_of(conn, query.score_run_id)
    if run is None:
        return ExplorerPage(
            score_run=None,
            offset=query.offset,
            rows=[],
            total=0 if query.with_total else None,
            generated_at=now,
        )
    index.ensure_indexed(conn, run)
    found, total = _window(conn, query, run, now)
    return ExplorerPage(
        score_run=run_item(conn, run),
        offset=query.offset,
        rows=rows_of(conn, found, now),
        total=total,
        generated_at=now,
    )


def count(
    conn: sqlite3.Connection,
    run: ScoreRun,
    query: ExplorerQuery,
    now: datetime,
    extra: Sequence[str] = (),
    extra_params: Sequence[object] = (),
) -> int:
    """Leads the query matches, and every condition of `extra` (whose parameters follow)."""
    where, params, joins, narrowed = _where(run.id, query.filters, query.search, now)
    clauses = [where, *(clause for clause in extra if clause)]
    (total,) = conn.execute(
        f"SELECT count(*) {_source(joins, narrowed or len(clauses) > 1)} "
        f"WHERE {' AND '.join(clauses)}",
        [*params, *extra_params],
    ).fetchone()
    return int(total)


def matching_ids(
    conn: sqlite3.Connection, query: ExplorerQuery, now: datetime, cap: int
) -> tuple[ScoreRun, list[str]]:
    """Every lead the query matches, in its order, refused beyond `cap`."""
    run = run_of(conn, query.score_run_id)
    if run is None:
        raise LeadError("no finished score run to act on")
    index.ensure_indexed(conn, run)
    total = count(conn, run, query, now)
    if total > cap:  # a bulk action names what it touches: a query too broad is refused
        raise LeadError(f"{total} leads match: narrow the query to at most {cap}")
    finished = run.finished_at is not None
    where, params, joins, narrowed = _where(run.id, query.filters, query.search, now)
    order, order_params, order_joins = _order(query.sort, now, finished)
    scan = narrowed or not _walked(query.sort, finished)
    ids = [
        row["business_id"]
        for row in conn.execute(
            f"SELECT li.business_id {_source(joins | order_joins, scan)} WHERE {where} "
            f"ORDER BY {order} LIMIT ?",
            [*params, *order_params, cap],
        )
    ]
    return run, ids


def export_preview(
    conn: sqlite3.Connection, request: ExportPreviewRequest, now: datetime
) -> ExportPreview:
    """What an export of the query would hold, with a real sample. Suppressed leads are left out
    unless the request includes them on purpose; nothing is written."""
    run = run_of(conn, request.query.score_run_id)
    if run is None:
        return ExportPreview(
            score_run=None, matching=0, excluded_suppressed=0, exported=0, columns=request.columns,
            sample=[], warnings=["no_finished_run"], generated_at=now,
        )  # fmt: skip
    index.ensure_indexed(conn, run)
    query = request.query.model_copy(update={"offset": 0, "limit": request.sample_size})
    suppressed = f"(li.business_id IN ({_SUPPRESSED_ROOTS}))"
    kept = "1" if request.include_suppressed else f"NOT {suppressed}"
    where, params, joins, _ = _where(run.id, query.filters, query.search, now)
    # one pass over the index for every count and warning of the preview
    counted = conn.execute(
        f"SELECT count(*), total({suppressed}), total(({kept}) AND li.facts_collected_at < ?), "
        f"total(({kept}) AND {_COLUMN[_F.PARTIAL]}) {_source(joins, True)} WHERE {where}",
        [format_ts(_stale_before(now)), *params],
    ).fetchone()
    matching, with_suppression, stale, partial = (int(value) for value in counted)
    excluded = 0 if request.include_suppressed else with_suppression
    unsuppressed = "" if request.include_suppressed else kept
    sample, _ = _window(
        conn, query.model_copy(update={"with_total": False}), run, now, unsuppressed
    )
    warnings = [
        code
        for code, applies in (
            ("suppressed_included", request.include_suppressed and with_suppression > 0),
            ("stale_rows", stale > 0),
            ("partial_rows", partial > 0),
            ("synthetic_rows", any(row.is_synthetic for row in sample)),
            # addresses built from a pattern never leave the store (phase 09)
            ("hypotheses_never_exported", True),
        )
        if applies
    ]
    return ExportPreview(
        score_run=run_item(conn, run),
        matching=matching,
        excluded_suppressed=excluded,
        exported=matching - excluded,
        columns=request.columns,
        sample=rows_of(conn, sample, now),
        warnings=warnings,
        generated_at=now,
    )


def compare(conn: sqlite3.Connection, query: LeadCompareQuery, now: datetime) -> LeadComparison:
    run = run_of(conn, query.score_run_id)
    if run is None:
        raise repository.NotFoundError("no finished score run to compare leads in")
    index.ensure_indexed(conn, run)
    wanted = list(dict.fromkeys(query.business_ids))
    marks = _marks(wanted)
    found = {
        row["business_id"]: repository.from_row(LeadIndexRow, row)
        for row in conn.execute(
            f"SELECT * FROM lead_index WHERE score_run_id = ? AND business_id IN ({marks})",  # noqa: S608
            [run.id, *wanted],
        )
    }
    cards = {
        row["business_id"]: LeadScorecard.model_validate(orjson.loads(row["scorecard_json"]))
        for row in conn.execute(
            "SELECT business_id, scorecard_json FROM business_scores "  # noqa: S608
            f"WHERE score_run_id = ? AND business_id IN ({marks})",
            [run.id, *wanted],
        )
    }
    present = [business for business in wanted if business in found and business in cards]
    rows = rows_of(conn, [found[business] for business in present], now)
    return LeadComparison(
        score_run=run_item(conn, run),
        leads=[ComparedLead(row=row, scorecard=cards[row.business_id]) for row in rows],
        missing=[business for business in wanted if business not in present],
    )
