"""The minimal evidence each task reads for one canonical business, built from what is stored.

Pages come as the parsed `main_text` the crawler already kept (never HTML), split into sentence
fragments, home and service pages first, repeated text once. Findings come from the latest website
audit of the business's crawls (failures and warnings only), subscores from the deterministic
engines, screenshots from the crawl artifacts. Nothing here calls the network or a model; a task
with nothing to read gets no input and is never sent.
"""

import hashlib
import re
import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import orjson

from research_engine.ai.tasks import TASKS, PreparedInput, bounded_refs
from research_engine.contacts import store as contacts_store
from research_engine.domain import AiInputKind, AiTaskType, utc_now
from research_engine.latino.store import page_links
from research_engine.leads import brief as sales_brief

MAX_SCREENSHOT_BYTES: Final = 4 * 1024 * 1024
_KIND_RANK: Final = {"home": 0, "services": 1, "about": 2, "contact": 3, "other": 4}
_SENTENCE: Final = re.compile(r"(?<=[.!?])\s+")
_SEVERITY_RANK: Final = (
    "CASE f.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 "
    "WHEN 'low' THEN 3 ELSE 4 END"
)


@dataclass(frozen=True, slots=True)
class Page:
    raw_record_id: str
    url: str
    kind: str
    text: str
    screenshot_path: str | None
    source_run_id: str


@dataclass(frozen=True, slots=True)
class ProjectPages:
    """The crawled pages of every canonical business of one project, best pages first."""

    project_id: str
    by_root: Mapping[str, Sequence[str]]


def project_pages(conn: sqlite3.Connection, project_id: str) -> ProjectPages:
    roots = {
        row["id"]: row["merged_into_id"] or row["id"]
        for row in conn.execute(
            "SELECT id, merged_into_id FROM businesses WHERE project_id = ?", (project_id,)
        )
    }
    by_root: dict[str, set[str]] = defaultdict(set)
    for raw_record_id, businesses in page_links(conn, project_id).items():
        for business in businesses:
            by_root[roots.get(business, business)].add(raw_record_id)
    return ProjectPages(project_id, {root: sorted(ids) for root, ids in by_root.items()})


def root_of(conn: sqlite3.Connection, business_id: str) -> tuple[str, str] | None:
    """(canonical business id, project id) of a live business, or None."""
    row = conn.execute(
        "SELECT coalesce(merged_into_id, id) AS root, project_id FROM businesses "
        "WHERE id = ? AND deleted_at IS NULL",
        (business_id,),
    ).fetchone()
    return (row["root"], row["project_id"]) if row else None


def load_pages(conn: sqlite3.Connection, raw_record_ids: Sequence[str]) -> list[Page]:
    if not raw_record_ids:
        return []
    marks = ", ".join("?" * len(raw_record_ids))
    rows = conn.execute(
        "SELECT cu.raw_record_id, cu.url, cu.page_kind, cu.depth, cu.screenshot_path, "  # noqa: S608
        "cu.source_run_id, cu.updated_at, rr.payload_json FROM crawl_urls cu "
        f"JOIN raw_records rr ON rr.id = cu.raw_record_id WHERE cu.raw_record_id IN ({marks})",
        list(raw_record_ids),
    ).fetchall()
    seen: set[str] = set()
    pages: list[Page] = []
    for row in sorted(
        rows,
        key=lambda r: (_KIND_RANK.get(r["page_kind"], 9), r["depth"], r["updated_at"] or ""),
    ):
        if row["raw_record_id"] in seen:
            continue
        seen.add(row["raw_record_id"])
        payload = orjson.loads(row["payload_json"])
        text = payload.get("main_text") if isinstance(payload, dict) else None
        pages.append(
            Page(
                raw_record_id=row["raw_record_id"],
                url=row["url"],
                kind=row["page_kind"],
                text=text if isinstance(text, str) else "",
                screenshot_path=row["screenshot_path"],
                source_run_id=row["source_run_id"],
            )
        )
    return pages


def fragments(pages: Sequence[Page], size: int) -> list[tuple[str, str, str]]:
    """(raw record id, url, fragment) in page order: sentences packed up to `size` characters, each
    distinct text once (headers and footers repeat on every page)."""
    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for page in pages:
        current = ""
        for sentence in _SENTENCE.split(" ".join(page.text.split())):
            if current and len(current) + 1 + len(sentence) > size:
                found.append((page.raw_record_id, page.url, current))
                current = ""
            current = f"{current} {sentence}".strip()
        if current:
            found.append((page.raw_record_id, page.url, current))
    unique = []
    for item in found:
        key = item[2].casefold()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _facts(conn: sqlite3.Connection, root: str) -> dict[str, str]:
    row = conn.execute("SELECT display_name FROM businesses WHERE id = ?", (root,)).fetchone()
    facts = {"business_name": row["display_name"]} if row else {}
    categories = [
        found["label_raw"]
        for found in conn.execute(
            "SELECT DISTINCT c.label_raw FROM business_classifications c "
            "JOIN businesses b ON b.id = c.business_id "
            "WHERE coalesce(b.merged_into_id, b.id) = ? AND c.kind = 'category' "
            "ORDER BY c.is_primary DESC, c.label_raw LIMIT 5",
            (root,),
        )
    ]
    if categories:
        facts["listed_categories"] = "; ".join(categories)
    return facts


def _findings(
    conn: sqlite3.Connection, pages: Sequence[Page]
) -> tuple[str | None, list[tuple[AiInputKind, str | None, str | None, str]]]:
    runs = sorted({page.source_run_id for page in pages})
    if not runs:
        return None, []
    marks = ", ".join("?" * len(runs))
    audit = conn.execute(
        f"SELECT id, site FROM website_audits WHERE source_run_id IN ({marks}) "  # noqa: S608
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        runs,
    ).fetchone()
    if audit is None:
        return None, []
    rows = conn.execute(
        "SELECT f.id, f.rule_id, f.title, f.result, f.severity, f.observed_value, "  # noqa: S608
        f"f.expected_condition FROM website_findings f WHERE f.audit_id = ? "
        f"AND f.result IN ('fail', 'warning') ORDER BY {_SEVERITY_RANK}, f.rule_id",
        (audit["id"],),
    ).fetchall()
    pieces: list[tuple[AiInputKind, str | None, str | None, str]] = [
        (
            AiInputKind.WEBSITE_FINDING,
            row["id"],
            row["rule_id"],
            f"{row['title']} ({row['result']}, {row['severity']}). "
            f"Observed: {row['observed_value'] or 'n/a'}. Expected: {row['expected_condition']}",
        )
        for row in rows
    ]
    return audit["site"], pieces


def _subscores(
    conn: sqlite3.Connection, root: str
) -> list[tuple[AiInputKind, str | None, str | None, str]]:
    pieces: list[tuple[AiInputKind, str | None, str | None, str]] = []
    contactability = contacts_store.get_contact_detail(conn, root).contactability
    components = ", ".join(
        f"{component.code} {round(component.value * 100)}%"
        for component in contactability.components
    )
    score = "none" if contactability.score is None else f"{contactability.score}/100"
    gates = ", ".join(contactability.gates) or "none"
    pieces.append((
        AiInputKind.CONTACTABILITY, root, "contact_intelligence",
        f"Contactability subscore {score}; channels counted {contactability.channels_counted}; "
        f"gates: {gates}; components: {components or 'none'}",
    ))  # fmt: skip
    latino = conn.execute(
        "SELECT status, assessment_confidence, method FROM latino_assessments "
        "WHERE business_id = ? AND superseded_at IS NULL "
        "ORDER BY CASE method WHEN 'manual_review' THEN 0 ELSE 1 END LIMIT 1",
        (root,),
    ).fetchone()
    if latino is not None and latino["status"] != "unknown":
        reviewed = "a person's review" if latino["method"] == "manual_review" else "the rules"
        pieces.append((
            AiInputKind.LATINO_STATUS, root, "latino_evidence",
            f"Latino evidence status: {latino['status']} "
            f"(confidence {round(latino['assessment_confidence'] * 100)}%, decided by {reviewed})",
        ))  # fmt: skip
    return pieces


def prepare(
    conn: sqlite3.Connection,
    data_dir: Path | None,
    root: str,
    task: AiTaskType,
    pages_of_project: ProjectPages,
) -> PreparedInput | None:
    """The input of `task` for canonical business `root`, or None when there is nothing to read."""
    spec = TASKS[task]
    pages = load_pages(conn, pages_of_project.by_root.get(root, ()))
    facts = _facts(conn, root)
    match task:
        case (
            AiTaskType.EVIDENCE_EXTRACTION
            | AiTaskType.BUSINESS_CLASSIFICATION
            | AiTaskType.LANGUAGE_DETECTION
        ):
            pieces = [
                (AiInputKind.PAGE_FRAGMENT, raw_record_id, url, text)
                for raw_record_id, url, text in fragments(pages, spec.max_ref_chars)
            ]
            refs, warnings = bounded_refs(spec, "F", pieces)
            if task is AiTaskType.LANGUAGE_DETECTION:
                facts = {}
            images: tuple[bytes, ...] = ()
        case AiTaskType.FINDING_SYNTHESIS:
            site, found = _findings(conn, pages)
            refs, warnings = bounded_refs(spec, "W", found)
            facts = {"site": site} if site else {}
            images = ()
        case AiTaskType.OPPORTUNITY_REASONING:
            _, found = _findings(conn, pages)
            finding_refs, warnings = bounded_refs(spec, "W", found)
            subscores = _subscores(conn, root)
            context, more = bounded_refs(spec, "C", subscores[:1])
            latino, most = bounded_refs(spec, "L", subscores[1:])
            refs, warnings = (*finding_refs, *context, *latino), (*warnings, *more, *most)
            if not finding_refs:
                return None  # a subscore alone shows no problem worth explaining
        case AiTaskType.VISUAL_REVIEW:
            shots: list[tuple[AiInputKind, str | None, str | None, str]] = []
            images_found: list[bytes] = []
            for page in pages:
                if len(shots) >= spec.max_refs or data_dir is None or not page.screenshot_path:
                    continue
                path = data_dir / page.screenshot_path
                if not path.is_file() or path.stat().st_size > MAX_SCREENSHOT_BYTES:
                    continue
                shots.append((
                    AiInputKind.SCREENSHOT, page.raw_record_id, page.url,
                    f"Screenshot of {page.url} ({page.kind} page)",
                ))  # fmt: skip
                images_found.append(path.read_bytes())
            refs, warnings = bounded_refs(spec, "S", shots)
            images = tuple(images_found[: len(refs)])
            # a screenshot ref is identified by the image it stands for, not by its caption
            refs = tuple(
                ref.model_copy(update={"sha256": hashlib.sha256(image).hexdigest()})
                for ref, image in zip(refs, images, strict=True)
            )
            facts = {"business_name": facts.get("business_name", "")} if facts else {}
        case AiTaskType.SALES_BRIEF:
            brief = sales_brief.build(conn, root, None, utc_now())
            if brief.suppressed:
                return None  # nobody words a pitch for a business that must not be contacted
            refs, warnings = bounded_refs(spec, "B", sales_brief.ai_pieces(brief))
            facts = {"business_name": brief.display_name}
            images = ()
    if not refs:
        return None
    return PreparedInput(task, facts, tuple(refs), images, tuple(warnings))
