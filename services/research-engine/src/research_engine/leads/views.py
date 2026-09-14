"""Saved Lead Explorer views (phase 12): named, versioned, never rewritten.

Saving a changed definition makes a new immutable version; saving an unchanged one makes nothing.
Archiving and restoring need a reason and are audited. A definition stored with an older schema is
upgraded when it is read (`upgrade`) and never rewritten: the next save appends a current version.

Schema version 1 held flat `and` conditions, one optional sort key and the visible columns:

    {"schema_version": 1, "score_run_id": null, "search": null,
     "conditions": [{"field": "tier", "operator": "eq", "value": "priority"}],
     "sort": {"field": "total_score", "direction": "desc"}, "columns": ["rank", "name"]}
"""

import sqlite3
from typing import Final

import orjson
from pydantic import ValidationError

from research_engine import repository
from research_engine.domain import (
    VIEW_SCHEMA_VERSION,
    AuditAction,
    EntityType,
    ExplorerCondition,
    ExplorerField,
    ExplorerGroup,
    ExplorerSort,
    JsonObject,
    SavedView,
    SavedViewAction,
    SavedViewChangeRequest,
    SavedViewItem,
    SavedViewList,
    SavedViewQuery,
    SavedViewSaveRequest,
    SavedViewVersion,
    ViewColumn,
    ViewDefinition,
    format_ts,
    new_id,
    utc_now,
)
from research_engine.leads import LeadError

MAX_VERSIONS_SHOWN: Final = 20
_PINNED: Final = (ExplorerField.RANK, ExplorerField.NAME)


def _field(value: object) -> ExplorerField | None:
    try:
        return ExplorerField(str(value))
    except ValueError:
        return None


def _from_v1(old: JsonObject) -> ViewDefinition:
    conditions = old.get("conditions")
    children = [
        ExplorerCondition.model_validate({"kind": "condition", **condition})
        for condition in (conditions if isinstance(conditions, list) else [])
        if isinstance(condition, dict)
    ]
    sort = old.get("sort")
    listed = old.get("columns")
    shown = list(
        dict.fromkeys(
            field
            for field in (_field(item) for item in (listed if isinstance(listed, list) else []))
            if field is not None
        )
    )
    search, run = old.get("search"), old.get("score_run_id")
    return ViewDefinition(
        schema_version=VIEW_SCHEMA_VERSION,
        score_run_id=run if isinstance(run, str) else None,
        filters=ExplorerGroup(kind="group", join="and", negate=False, children=children),
        search=search if isinstance(search, str) else None,
        sort=[ExplorerSort.model_validate(sort)] if isinstance(sort, dict) else [],
        columns=[
            ViewColumn(field=field, visible=True, pinned=field in _PINNED, width=None)
            for field in shown
        ]
        + [
            ViewColumn(field=field, visible=False, pinned=False, width=None)
            for field in ExplorerField
            if field not in shown
        ],
    )


def upgrade(definition: JsonObject, schema_version: int) -> tuple[ViewDefinition, int | None]:
    """A stored definition in the current schema, and the version it was upgraded from."""
    try:
        if schema_version == VIEW_SCHEMA_VERSION:
            return ViewDefinition.model_validate(definition), None
        if schema_version == 1:
            return _from_v1(definition), 1
    except ValidationError as exc:
        raise LeadError(f"a saved view cannot be read: {exc.errors()[0]['msg']}") from exc
    raise LeadError(
        f"this build reads saved views up to schema version {VIEW_SCHEMA_VERSION}, "
        f"not {schema_version}"
    )


def _item(conn: sqlite3.Connection, view: SavedView) -> SavedViewItem:
    versions = [
        repository.from_row(SavedViewVersion, row)
        for row in conn.execute(
            f"SELECT {repository._columns(SavedViewVersion)} FROM saved_view_versions "  # noqa: S608
            "WHERE view_id = ? ORDER BY version DESC LIMIT ?",
            (view.id, MAX_VERSIONS_SHOWN),
        )
    ]
    if not versions:
        raise repository.NotFoundError(f"saved view {view.id} has no version")
    definition, upgraded = upgrade(versions[0].definition_json, versions[0].schema_version)
    return SavedViewItem(
        view=view, versions=versions, definition=definition, upgraded_from=upgraded
    )


def get_view(conn: sqlite3.Connection, view_id: str) -> SavedViewItem:
    return _item(conn, repository.get(conn, SavedView, view_id))


def list_views(conn: sqlite3.Connection, query: SavedViewQuery) -> SavedViewList:
    live = "" if query.include_archived else " AND archived_at IS NULL"
    views = [
        repository.from_row(SavedView, row)
        for row in conn.execute(
            f"SELECT {repository._columns(SavedView)} FROM saved_views WHERE scope = ?{live} "  # noqa: S608
            "ORDER BY archived_at IS NOT NULL, name, id",
            (query.scope,),
        )
    ]
    return SavedViewList(
        items=[_item(conn, view) for view in views], schema_version=VIEW_SCHEMA_VERSION
    )


def save_view(
    conn: sqlite3.Connection, request: SavedViewSaveRequest, *, actor: str
) -> SavedViewItem:
    definition = request.definition.model_dump(mode="json")
    name, now = request.name.strip(), utc_now()
    try:
        with repository.atomic(conn):
            if request.view_id is None:
                view = SavedView(
                    id=new_id(), scope=request.scope, name=name, description=request.description,
                    created_at=now, updated_at=now, archived_at=None,
                )  # fmt: skip
                repository.insert(conn, view)
                repository.audit(
                    conn, AuditAction.CREATE, EntityType.SAVED_VIEW, view.id, actor=actor
                )
                number = 1
            else:
                view = repository.get(conn, SavedView, request.view_id)
                if view.scope != request.scope:
                    raise LeadError("that view belongs to another screen")
                if view.archived_at is not None:
                    raise LeadError("restore this view before saving a new version")
                if (view.name, view.description) != (name, request.description):
                    conn.execute(
                        "UPDATE saved_views SET name = ?, description = ?, updated_at = ? "
                        "WHERE id = ?",
                        (name, request.description, format_ts(now), view.id),
                    )
                latest = conn.execute(
                    "SELECT version, definition_json, schema_version FROM saved_view_versions "
                    "WHERE view_id = ? ORDER BY version DESC LIMIT 1",
                    (view.id,),
                ).fetchone()
                unchanged = (
                    latest is not None
                    and latest["schema_version"] == VIEW_SCHEMA_VERSION
                    and orjson.loads(latest["definition_json"]) == definition
                )
                number = 0 if unchanged else (latest["version"] + 1 if latest else 1)
            if number:
                version = SavedViewVersion(
                    id=new_id(), view_id=view.id, version=number, definition_json=definition,
                    schema_version=VIEW_SCHEMA_VERSION, created_by=actor, created_at=now,
                )  # fmt: skip
                repository.insert(conn, version)
                repository.audit(
                    conn, AuditAction.UPDATE, EntityType.SAVED_VIEW, view.id, actor=actor,
                    detail={"version": number, "version_id": version.id},
                )  # fmt: skip
    except sqlite3.IntegrityError as exc:
        if "saved_views" in str(exc):
            raise LeadError(f"a view named '{name}' already exists") from exc
        raise
    return get_view(conn, view.id)


def change_view(
    conn: sqlite3.Connection, request: SavedViewChangeRequest, *, actor: str
) -> SavedViewItem:
    now = format_ts(utc_now())
    try:
        with repository.atomic(conn):
            view = repository.get(conn, SavedView, request.view_id)
            if request.action is SavedViewAction.ARCHIVE and view.archived_at is None:
                conn.execute(
                    "UPDATE saved_views SET archived_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, view.id),
                )
            elif request.action is SavedViewAction.RESTORE and view.archived_at is not None:
                conn.execute(
                    "UPDATE saved_views SET archived_at = NULL, updated_at = ? WHERE id = ?",
                    (now, view.id),
                )
            repository.audit(
                conn, AuditAction.UPDATE, EntityType.SAVED_VIEW, view.id, actor=actor,
                detail={"action": request.action.value, "reason": request.reason},
            )  # fmt: skip
    except sqlite3.IntegrityError as exc:
        raise LeadError("a live view already uses this name: rename it first") from exc
    return get_view(conn, request.view_id)
