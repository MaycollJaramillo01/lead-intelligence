"""Persistence of the local AI layer: settings, inference runs and their answers, reviews,
benchmarks and AI jobs.

A run is written when its work is queued and updated as it runs; answers are immutable rows. The
database refuses a second valid result for the same cache key, keeps the request fixed once stored
and never lets a review touch a run that did not end with a valid answer (migration 0009).
"""

import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Final

import orjson
from pydantic import JsonValue

from research_engine import jobs, repository
from research_engine.domain import (
    AiBenchmarkResult,
    AiConfigureRequest,
    AiDevice,
    AiInferenceDetail,
    AiInferenceItem,
    AiInferenceListQuery,
    AiInferencePage,
    AiInputRef,
    AiProfile,
    AiProfileSetting,
    AiResourceLimits,
    AiReviewDecision,
    AiReviewRequest,
    AiReviewStatus,
    AiRun,
    AiRunListQuery,
    AiRunPage,
    AiStartRequest,
    AiStats,
    AiTaskSetting,
    AiTaskType,
    AuditAction,
    EntityType,
    EventLevel,
    InferenceOutput,
    InferenceRun,
    InferenceStatus,
    Job,
    JobStatus,
    JsonObject,
    Project,
    ReviewDecision,
    format_ts,
    new_id,
    utc_now,
)

JOB_KIND: Final = "ai_analysis"
ACTOR: Final = "ai-engine"
MAX_DECISIONS: Final = 50
VALID: Final = (InferenceStatus.SUCCEEDED, InferenceStatus.INSUFFICIENT_EVIDENCE)
_DECISIONS: Final = {
    AiReviewDecision.ACCEPT: ("ai_output_accepted", AiReviewStatus.ACCEPTED),
    AiReviewDecision.REJECT: ("ai_output_rejected", AiReviewStatus.REJECTED),
    AiReviewDecision.RESET: ("ai_review_withdrawn", AiReviewStatus.UNREVIEWED),
}


class AiError(ValueError):
    """The request cannot be served as asked (the message says why)."""


# ---- settings -----------------------------------------------------------------------------------


def profiles(conn: sqlite3.Connection) -> list[AiProfileSetting]:
    rows = conn.execute(
        f"SELECT {repository._columns(AiProfileSetting)} FROM ai_profile_settings"  # noqa: S608
    ).fetchall()
    order = list(AiProfile)
    return sorted(
        (repository.from_row(AiProfileSetting, row) for row in rows),
        key=lambda setting: order.index(setting.profile),
    )


def task_settings(conn: sqlite3.Connection) -> dict[AiTaskType, AiTaskSetting]:
    rows = conn.execute(
        f"SELECT {repository._columns(AiTaskSetting)} FROM ai_task_settings"  # noqa: S608
    ).fetchall()
    found = {
        setting.task_type: setting
        for setting in (repository.from_row(AiTaskSetting, row) for row in rows)
    }
    return {task: found[task] for task in AiTaskType if task in found}


def limits(conn: sqlite3.Connection) -> AiResourceLimits:
    row = conn.execute(
        f"SELECT {repository._columns(AiResourceLimits)} FROM ai_resource_limits"  # noqa: S608
    ).fetchone()
    return repository.from_row(AiResourceLimits, row)


def configure(conn: sqlite3.Connection, request: AiConfigureRequest) -> None:
    """Writes every setting of the request at once; each row keeps when it last changed."""
    if len({change.profile for change in request.profiles}) != len(request.profiles):
        raise AiError("a profile appears twice")
    if len({change.task_type for change in request.tasks}) != len(request.tasks):
        raise AiError("a task appears twice")
    now = format_ts(utc_now())
    with repository.atomic(conn):
        for change in request.profiles:
            conn.execute(
                "UPDATE ai_profile_settings SET model = ?, num_ctx = ?, allow_cpu = ?, "
                "updated_at = ? WHERE profile = ?",
                (change.model, change.num_ctx, int(change.allow_cpu), now, change.profile.value),
            )
        for task in request.tasks:
            conn.execute(
                "UPDATE ai_task_settings SET enabled = ?, profile = ?, updated_at = ? "
                "WHERE task_type = ?",
                (int(task.enabled), task.profile.value, now, task.task_type.value),
            )
        conn.execute(
            "UPDATE ai_resource_limits SET min_free_ram_mib = ?, min_free_vram_mib = ?, "
            "updated_at = ? WHERE scope = 'machine'",
            (request.min_free_ram_mib, request.min_free_vram_mib, now),
        )


# ---- runs ---------------------------------------------------------------------------------------


def cached(
    conn: sqlite3.Connection, cache_key: str
) -> tuple[InferenceRun, JsonObject | None] | None:
    """The valid, not rejected result of an identical request, with its validated answer."""
    row = conn.execute(
        f"SELECT {repository._columns(InferenceRun)} FROM ai_inference_runs "  # noqa: S608
        "WHERE cache_key = ? AND status IN ('succeeded', 'insufficient_evidence') "
        "AND review_status <> 'rejected'",
        (cache_key,),
    ).fetchone()
    if row is None:
        return None
    run = repository.from_row(InferenceRun, row)
    return run, validated_output(conn, run.id)


def validated_output(conn: sqlite3.Connection, run_id: str) -> JsonObject | None:
    row = conn.execute(
        "SELECT validated_output_json FROM ai_inference_outputs WHERE inference_run_id = ? "
        "AND validated_output_json IS NOT NULL ORDER BY attempt DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    loaded = orjson.loads(row["validated_output_json"]) if row else None
    return loaded if isinstance(loaded, dict) else None


def start(conn: sqlite3.Connection, run_id: str, now: datetime) -> None:
    stamp = format_ts(now)
    conn.execute(
        "UPDATE ai_inference_runs SET status = 'running', started_at = ?, updated_at = ? "
        "WHERE id = ? AND status = 'queued'",
        (stamp, stamp, run_id),
    )


def add_output(conn: sqlite3.Connection, output: InferenceOutput) -> None:
    repository.insert(conn, output)


def finish(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    status: InferenceStatus,
    now: datetime,
    device: AiDevice = AiDevice.UNKNOWN,
    attempts: int = 0,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    duration_ms: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    parameters: Mapping[str, JsonValue] | None = None,
) -> None:
    """Ends a run. A queued run can only be cancelled; anything else passes through `running`."""
    stamp = format_ts(now)
    with repository.atomic(conn):
        row = conn.execute(
            "SELECT status, started_at FROM ai_inference_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None or row["status"] not in ("queued", "running"):
            return
        never_started = row["status"] == "queued" and status is InferenceStatus.CANCELLED
        if row["status"] == "queued" and not never_started:
            start(conn, run_id, now)
        sets = (
            "status = ?, device = ?, attempts = ?, prompt_tokens = ?, completion_tokens = ?, "
            "duration_ms = ?, error_code = ?, error_message = ?, finished_at = ?, updated_at = ?"
        )
        values: list[object] = [
            status.value, device.value, attempts, prompt_tokens, completion_tokens, duration_ms,
            error_code, error_message[:2000] if error_message else None,
            None if never_started else stamp, stamp,
        ]  # fmt: skip
        if parameters is not None:
            sets += ", parameters_json = ?"
            values.append(orjson.dumps(dict(parameters), option=orjson.OPT_SORT_KEYS).decode())
        conn.execute(
            f"UPDATE ai_inference_runs SET {sets} WHERE id = ?",  # noqa: S608
            [*values, run_id],
        )


def recover(conn: sqlite3.Connection) -> tuple[int, int]:
    """At startup: work a dead worker left queued is cancelled, running work failed (retryable).
    Valid results are untouched, and the cache key keeps a new run from repeating them."""
    now = utc_now()
    counts = {"queued": 0, "running": 0}
    for row in conn.execute(
        "SELECT id, status FROM ai_inference_runs WHERE status IN ('queued', 'running')"
    ).fetchall():
        counts[row["status"]] += 1
        finish(
            conn,
            row["id"],
            status=(
                InferenceStatus.CANCELLED if row["status"] == "queued" else InferenceStatus.FAILED
            ),
            now=now,
            error_code="worker_restarted",
            error_message="the worker stopped before this inference ended",
        )
    return counts["queued"], counts["running"]


# ---- reads --------------------------------------------------------------------------------------

_ITEM_SQL: Final = (
    "SELECT r.id, r.task_type, r.status, r.review_status, r.model, r.device, r.business_id, "
    "b.display_name AS business_name, r.job_id, r.duration_ms, r.attempts, r.error_code, "
    "r.created_at, coalesce(p.is_synthetic, 0) AS is_synthetic FROM ai_inference_runs r "
    "LEFT JOIN businesses b ON b.id = r.business_id LEFT JOIN projects p ON p.id = r.project_id"
)


def list_inferences(conn: sqlite3.Connection, query: AiInferenceListQuery) -> AiInferencePage:
    where: list[str] = []
    params: list[object] = []
    for column, value in (
        ("r.task_type", query.task_type),
        ("r.status", query.status),
        ("r.review_status", query.review_status),
    ):
        if value is not None:
            where.append(f"{column} = ?")
            params.append(value.value)
    if query.business_id is not None:
        where.append(
            "r.business_id IN (SELECT id FROM businesses WHERE id = ? OR merged_into_id = ?)"
        )
        params += [query.business_id, query.business_id]
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    (total,) = conn.execute(
        f"SELECT count(*) FROM ai_inference_runs r{clause}",  # noqa: S608
        params,
    ).fetchone()
    if query.cursor is not None:
        last_key, last_id = repository.decode_cursor(query.cursor, "ai_inferences")
        where.append("(r.created_at, r.id) < (?, ?)")
        params += [last_key, last_id]
        clause = f" WHERE {' AND '.join(where)}"
    rows = conn.execute(
        f"{_ITEM_SQL}{clause} ORDER BY r.created_at DESC, r.id DESC LIMIT ?",
        [*params, query.limit + 1],
    ).fetchall()
    page = rows[: query.limit]
    next_cursor = (
        repository.encode_cursor("ai_inferences", page[-1]["created_at"], page[-1]["id"])
        if len(rows) > query.limit
        else None
    )
    return AiInferencePage(
        items=[AiInferenceItem.model_validate(dict(row)) for row in page],
        next_cursor=next_cursor,
        total=total,
    )


def get_inference(conn: sqlite3.Connection, run_id: str) -> AiInferenceDetail:
    run = repository.get(conn, InferenceRun, run_id)
    outputs = [
        repository.from_row(InferenceOutput, row)
        for row in conn.execute(
            f"SELECT {repository._columns(InferenceOutput)} FROM ai_inference_outputs "  # noqa: S608
            "WHERE inference_run_id = ? ORDER BY attempt",
            (run_id,),
        )
    ]
    refs = run.input_json.get("refs")
    warnings = run.input_json.get("warnings")
    decisions = [
        repository.from_row(ReviewDecision, row)
        for row in conn.execute(
            f"SELECT {repository._columns(ReviewDecision)} FROM review_decisions "  # noqa: S608
            "WHERE entity_type = 'inference_run' AND entity_id = ? "
            "ORDER BY decided_at DESC, id DESC LIMIT ?",
            (run_id, MAX_DECISIONS),
        )
    ]
    business = (
        conn.execute(
            "SELECT display_name FROM businesses WHERE id = ?", (run.business_id,)
        ).fetchone()
        if run.business_id
        else None
    )
    synthetic = (
        conn.execute("SELECT is_synthetic FROM projects WHERE id = ?", (run.project_id,)).fetchone()
        if run.project_id
        else None
    )
    notes = [str(item) for item in warnings] if isinstance(warnings, list) else []
    degraded = run.parameters_json.get("degraded")
    if isinstance(degraded, list):
        notes += [str(step) for step in degraded]
    return AiInferenceDetail(
        run=run,
        business_name=business["display_name"] if business else None,
        outputs=outputs,
        inputs=[AiInputRef.model_validate(ref) for ref in refs] if isinstance(refs, list) else [],
        warnings=notes,
        decisions=decisions,
        is_synthetic=bool(synthetic and synthetic["is_synthetic"]),
    )


def review(conn: sqlite3.Connection, request: AiReviewRequest, *, actor: str) -> None:
    """A person accepts or rejects an answer after checking it against its cited inputs, or
    withdraws the decision. It never turns the answer into a fact anywhere else."""
    reason = request.reason.strip()
    if not reason:
        raise AiError("a review needs a reason")
    run = repository.get(conn, InferenceRun, request.inference_run_id)
    if run.status not in VALID:
        raise AiError("only an answer that validated can be reviewed")
    code, target = _DECISIONS[request.decision]
    if run.review_status is target:
        raise AiError(f"this answer is already {target.value}")
    now = utc_now()
    try:
        with repository.atomic(conn):
            conn.execute(
                "UPDATE ai_inference_runs SET review_status = ?, updated_at = ? WHERE id = ?",
                (target.value, format_ts(now), run.id),
            )
            repository.insert(
                conn,
                ReviewDecision(
                    id=new_id(), entity_type=EntityType.INFERENCE_RUN, entity_id=run.id,
                    decision=code, previous_value=run.review_status.value, new_value=target.value,
                    reason=reason, actor=actor, decided_at=now,
                ),
            )  # fmt: skip
    except sqlite3.IntegrityError as exc:
        raise AiError(
            "a newer valid answer to the same request exists: review that one instead"
        ) from exc


# ---- benchmarks ---------------------------------------------------------------------------------


def add_benchmark(conn: sqlite3.Connection, result: AiBenchmarkResult) -> None:
    repository.insert(conn, result)


def latest_benchmarks(conn: sqlite3.Connection) -> list[AiBenchmarkResult]:
    rows = conn.execute(
        f"SELECT {repository._columns(AiBenchmarkResult, 'r')} FROM ai_benchmark_results r "  # noqa: S608
        "WHERE NOT EXISTS (SELECT 1 FROM ai_benchmark_results n WHERE n.model = r.model "
        "AND n.task_type = r.task_type AND (n.created_at, n.id) > (r.created_at, r.id)) "
        "ORDER BY r.task_type, r.model"
    ).fetchall()
    return [repository.from_row(AiBenchmarkResult, row) for row in rows]


# ---- jobs ---------------------------------------------------------------------------------------


def plan_runs(
    conn: sqlite3.Connection, request: AiStartRequest, *, actor: str
) -> tuple[list[str], list[str]]:
    """One queued job per project: the businesses listed (grouped by project) or every canonical
    business of the project. An identical open request is listed instead of started twice."""
    tasks = list(dict.fromkeys(request.task_types))
    wanted: dict[str, list[str] | None] = {}
    if request.business_ids:
        for business_id in dict.fromkeys(request.business_ids):
            row = conn.execute(
                "SELECT coalesce(merged_into_id, id) AS root, project_id FROM businesses "
                "WHERE id = ? AND deleted_at IS NULL",
                (business_id,),
            ).fetchone()
            if row is None:
                raise repository.NotFoundError(f"business {business_id} not found")
            if request.project_id is not None and row["project_id"] != request.project_id:
                raise AiError(f"business {business_id} belongs to another project")
            roots = wanted.setdefault(row["project_id"], [])
            if roots is not None and row["root"] not in roots:
                roots.append(row["root"])
    elif request.project_id is not None:
        project = repository.get(conn, Project, request.project_id)
        if project.deleted_at is not None:
            raise repository.NotFoundError(f"project {request.project_id} has been deleted")
        wanted[request.project_id] = None
    else:
        for row in conn.execute(
            "SELECT p.id FROM projects p WHERE p.deleted_at IS NULL AND EXISTS "
            "(SELECT 1 FROM businesses b WHERE b.project_id = p.id AND b.deleted_at IS NULL) "
            "ORDER BY p.created_at, p.id"
        ):
            wanted[row["id"]] = None
        if not wanted:
            raise AiError("there are no businesses to analyse yet: discover or import some")
    created: list[str] = []
    listed: list[str] = []
    now = utc_now()
    with repository.atomic(conn):
        for project_id, roots in wanted.items():
            params: JsonObject = {
                "project_id": project_id,
                "task_types": [task.value for task in tasks],
                "business_ids": list[JsonValue](sorted(roots)) if roots is not None else None,
            }
            same = conn.execute(
                "SELECT id FROM jobs WHERE kind = ? AND status IN ('queued', 'running', 'paused') "
                "AND json_extract(params_json, '$.project_id') = ? "
                "AND json_extract(params_json, '$.task_types') = json(?) "
                "AND coalesce(json_extract(params_json, '$.business_ids'), 'all') = "
                "coalesce(json(?), 'all') LIMIT 1",
                (JOB_KIND, project_id, orjson.dumps(params["task_types"]).decode(),
                 orjson.dumps(params["business_ids"]).decode() if roots is not None else None),
            ).fetchone()  # fmt: skip
            if same is not None:
                listed.append(same["id"])
                continue
            job = Job(
                id=new_id(), project_id=project_id, kind=JOB_KIND, status=JobStatus.QUEUED,
                priority=0 if roots is not None and len(roots) <= 5 else 5, params_json=params,
                idempotency_key=None, progress_done=0, progress_total=None, requested_at=now,
                started_at=None, finished_at=None, created_at=now, updated_at=now,
            )  # fmt: skip
            repository.insert(conn, job)
            repository.audit(conn, AuditAction.CREATE, EntityType.JOB, job.id, actor=actor)
            jobs.write_event(conn, job.id, None, "run_queued", EventLevel.INFO, params)
            created.append(job.id)
    return created, listed


def job_targets(conn: sqlite3.Connection, job: Job) -> list[str]:
    listed = job.params_json.get("business_ids")
    if isinstance(listed, list):
        return [str(business_id) for business_id in listed]
    return [
        row["id"]
        for row in conn.execute(
            "SELECT id FROM businesses WHERE project_id = ? AND deleted_at IS NULL "
            "AND merged_into_id IS NULL ORDER BY id",
            (job.project_id,),
        )
    ]


def job_tasks(job: Job) -> list[AiTaskType]:
    listed = job.params_json.get("task_types")
    known = {task.value for task in AiTaskType}
    if not isinstance(listed, list):
        return []
    return [AiTaskType(value) for value in listed if isinstance(value, str) and value in known]


def _run_view(conn: sqlite3.Connection, job: Job) -> AiRun:
    state = jobs.latest_state(conn, job.id)
    stats = state.get("stats")
    counters = stats if isinstance(stats, dict) else {}
    project = repository.get(conn, Project, job.project_id)
    stage = state.get("stage")
    listed = job.params_json.get("business_ids")
    return AiRun(
        job_id=job.id,
        project_id=job.project_id,
        project_name=project.name,
        status=job.status,
        stage=stage if isinstance(stage, str) and stage else "queued",
        progress_done=job.progress_done,
        progress_total=job.progress_total,
        task_types=job_tasks(job),
        business_count=len(listed) if isinstance(listed, list) else None,
        stats=AiStats.model_validate(
            {name: repository._counter(counters.get(name)) for name in AiStats.model_fields}
        ),
        requested_at=job.requested_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        events=repository._run_events(conn, job.id),
        is_synthetic=project.is_synthetic,
    )


def get_run(conn: sqlite3.Connection, job_id: str) -> AiRun:
    return _run_view(conn, jobs.live_job(conn, job_id, JOB_KIND))


def runs_by_id(conn: sqlite3.Connection, job_ids: Sequence[str]) -> AiRunPage:
    items = [get_run(conn, job_id) for job_id in job_ids]
    return AiRunPage(items=items, next_cursor=None, total=len(items))


def list_runs(conn: sqlite3.Connection, query: AiRunListQuery) -> AiRunPage:
    where, params = ["j.kind = ?"], list[object]([JOB_KIND])
    if query.project_id is not None:
        where.append("j.project_id = ?")
        params.append(query.project_id)
    (total,) = conn.execute(
        f"SELECT count(*) FROM jobs j WHERE {' AND '.join(where)}",  # noqa: S608
        params,
    ).fetchone()
    if query.cursor is not None:
        last_key, last_id = repository.decode_cursor(query.cursor, "ai_runs")
        where.append("(j.requested_at, j.id) < (?, ?)")
        params += [last_key, last_id]
    rows = conn.execute(
        f"SELECT {repository._columns(Job, 'j')} FROM jobs j "  # noqa: S608
        f"WHERE {' AND '.join(where)} ORDER BY j.requested_at DESC, j.id DESC LIMIT ?",
        [*params, query.limit + 1],
    ).fetchall()
    page = rows[: query.limit]
    next_cursor = (
        repository.encode_cursor("ai_runs", page[-1]["requested_at"], page[-1]["id"])
        if len(rows) > query.limit
        else None
    )
    return AiRunPage(
        items=[_run_view(conn, repository.from_row(Job, row)) for row in page],
        next_cursor=next_cursor,
        total=total,
    )
