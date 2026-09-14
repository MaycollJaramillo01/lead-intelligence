"""The facts a score reads about the canonical businesses of a project (phase 11).

Read-only and independent of any ICP or model: the names, places, categories and websites of every
record resolved into a business; the newest audit of the crawled site that speaks for it (subscores,
the findings features read, detected technologies, what changed); its published channels decided as
phase 09 decides them, with cached mail answers only; its people; the do-not-contact list; and the
Latino assessment it shows. Stored with every score, so a run can be scored again over the same
facts. Nothing here calls the network or a model.
"""

import sqlite3
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from typing import Final

import orjson
from pydantic import JsonValue

from research_engine import repository
from research_engine.contacts import store as contacts_store
from research_engine.domain import BusinessFacts, ContactOrigin, FactAudit, FactLatino
from research_engine.latino.store import page_links
from research_engine.scoring.model import FACT_RULES, FACTS_VERSION

CHUNK: Final = 500
_RULES: Final = sorted(FACT_RULES)


def _chunks(items: Sequence[str], size: int = CHUNK) -> Iterator[Sequence[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _marks(items: Sequence[object]) -> str:
    return ", ".join("?" * len(items))


def root_ids(conn: sqlite3.Connection, project_id: str) -> list[str]:
    """Live canonical businesses of a project, by id (resumable)."""
    return contacts_store.root_ids(conn, project_id)


def audits_by_root(conn: sqlite3.Connection, project_id: str) -> dict[str, str]:
    """The newest audit of a crawled site that speaks for each canonical business: the site its
    candidate asked to crawl, or a site on the domain of one of its websites (phase 07 links)."""
    roots = {
        row["id"]: row["merged_into_id"] or row["id"]
        for row in conn.execute(
            "SELECT id, merged_into_id FROM businesses WHERE project_id = ?", (project_id,)
        )
    }
    links = page_links(conn, project_id)
    runs: dict[str, set[str]] = defaultdict(set)
    for chunk in _chunks(sorted(links)):
        for row in conn.execute(
            "SELECT raw_record_id, source_run_id FROM crawl_urls "  # noqa: S608
            f"WHERE raw_record_id IN ({_marks(chunk)})",
            list(chunk),
        ):
            for business in links[row["raw_record_id"]]:
                runs[roots.get(business, business)].add(row["source_run_id"])
    newest: dict[str, tuple[str, str]] = {}
    for row in conn.execute(
        "SELECT id, source_run_id, created_at FROM website_audits WHERE project_id = ?",
        (project_id,),
    ):
        marker = (row["created_at"], row["id"])
        newest[row["source_run_id"]] = max(marker, newest.get(row["source_run_id"], ("", "")))
    found: dict[str, str] = {}
    for root, crawls in runs.items():
        markers = [newest[run] for run in crawls if run in newest]
        if markers:
            found[root] = max(markers)[1]
    return found


def _object(text: str | None) -> dict[str, JsonValue]:
    loaded = orjson.loads(text) if text else None
    return loaded if isinstance(loaded, dict) else {}


def _first_url(refs: JsonValue) -> str | None:
    for ref in refs if isinstance(refs, list) else ():
        if isinstance(ref, dict) and isinstance(url := ref.get("url"), str):
            return url[:2048]
    return None


def _audits(conn: sqlite3.Connection, audit_ids: Sequence[str]) -> dict[str, FactAudit]:
    findings: dict[str, list[dict[str, object]]] = defaultdict(list)
    technologies: dict[str, list[dict[str, object]]] = defaultdict(list)
    audits: dict[str, FactAudit] = {}
    for chunk in _chunks(sorted(set(audit_ids))):
        marks = _marks(chunk)
        for row in conn.execute(
            "SELECT id, audit_id, rule_id, category, result, severity, confidence, "  # noqa: S608
            f"observed_value, evidence_json FROM website_findings WHERE audit_id IN ({marks}) "
            f"AND (rule_id IN ({_marks(_RULES)}) OR result IN ('fail', 'warning')) "
            "ORDER BY rule_id",
            [*chunk, *_RULES],
        ):
            url = _first_url(_object(row["evidence_json"]).get("refs"))
            observed = row["observed_value"]
            findings[row["audit_id"]].append({
                "id": row["id"], "rule_id": row["rule_id"], "category": row["category"],
                "result": row["result"], "severity": row["severity"],
                "confidence": row["confidence"],
                "observed_value": observed[:300] if observed else None,
                "url": url,
            })  # fmt: skip
        for row in conn.execute(
            "SELECT id, audit_id, technology_key, name, category "  # noqa: S608
            f"FROM website_technologies WHERE audit_id IN ({marks}) ORDER BY technology_key",
            list(chunk),
        ):
            technologies[row["audit_id"]].append({
                "id": row["id"], "key": row["technology_key"], "name": row["name"],
                "category": row["category"],
            })  # fmt: skip
        for row in conn.execute(
            "SELECT id, site, status, coverage, pages_analyzed, created_at, "  # noqa: S608
            f"summary_json, diff_json, previous_audit_id FROM website_audits WHERE id IN ({marks})",
            list(chunk),
        ):
            summary, diff = _object(row["summary_json"]), _object(row["diff_json"])
            subscores = summary.get("subscores")
            scored = subscores if isinstance(subscores, list) else []
            changed_scores = diff.get("subscores")
            changed_rules = diff.get("findings")
            changes = sum(
                1
                for item in (changed_scores if isinstance(changed_scores, list) else [])
                if isinstance(item, dict) and item.get("before") != item.get("after")
            ) + (len(changed_rules) if isinstance(changed_rules, list) else 0)
            audits[row["id"]] = FactAudit.model_validate({
                "id": row["id"], "site": row["site"], "status": row["status"],
                "coverage": row["coverage"], "pages_analyzed": row["pages_analyzed"],
                "created_at": row["created_at"],
                "subscores": [{"category": item["category"], "score": item.get("score")}
                              for item in scored if isinstance(item, dict)],
                "findings": findings[row["id"]], "technologies": technologies[row["id"]],
                "previous_audit_id": row["previous_audit_id"], "changes": changes,
            })  # fmt: skip
    return audits


def _latino(conn: sqlite3.Connection, roots: Sequence[str]) -> dict[str, FactLatino]:
    """The assessment each business shows: a person's review, else the rules."""
    found: dict[str, FactLatino] = {}
    for chunk in _chunks(roots):
        for row in conn.execute(
            "SELECT a.id, a.business_id, a.status, a.assessment_confidence, a.method, "  # noqa: S608
            "(SELECT count(*) FROM latino_assessment_evidence e WHERE e.assessment_id = a.id "
            "AND e.role = 'contradicts') AS contradictions FROM latino_assessments a "
            f"WHERE a.business_id IN ({_marks(chunk)}) AND a.superseded_at IS NULL "
            "ORDER BY a.method = 'manual_review' DESC, a.assessed_at DESC, a.id",
            list(chunk),
        ):
            if row["business_id"] not in found:
                found[row["business_id"]] = FactLatino.model_validate({
                    "assessment_id": row["id"], "status": row["status"],
                    "confidence": row["assessment_confidence"], "method": row["method"],
                    "contradictions": row["contradictions"],
                })  # fmt: skip
    return found


def collect(
    conn: sqlite3.Connection,
    roots: Sequence[str],
    audits: Mapping[str, str],
    now: datetime,
) -> list[BusinessFacts]:
    """The facts of these canonical businesses as the store shows them at `now`, in their order."""
    if not roots:
        return []
    owner: dict[str, str] = {}
    base: dict[str, dict[str, object]] = {}
    for chunk in _chunks(roots):
        marks = _marks(chunk)
        for row in conn.execute(
            "SELECT b.id, b.project_id, b.display_name, b.status, b.first_seen_at, "  # noqa: S608
            "b.last_seen_at, p.is_synthetic FROM businesses b "
            f"JOIN projects p ON p.id = b.project_id WHERE b.id IN ({marks})",
            list(chunk),
        ):
            owner[row["id"]] = row["id"]
            base[row["id"]] = {
                "facts_version": FACTS_VERSION, "business_id": row["id"],
                "project_id": row["project_id"], "display_name": row["display_name"],
                "status": row["status"], "is_synthetic": bool(row["is_synthetic"]),
                "first_seen_at": row["first_seen_at"], "last_seen_at": row["last_seen_at"],
                "collected_at": now, "conflict_count": 0,
            }  # fmt: skip
        for row in conn.execute(
            f"SELECT id, merged_into_id FROM businesses WHERE merged_into_id IN ({marks}) "  # noqa: S608
            "AND deleted_at IS NULL",
            list(chunk),
        ):
            owner[row["id"]] = row["merged_into_id"]
        for row in conn.execute(
            "SELECT business_id, count(*) AS n FROM canonical_values "  # noqa: S608
            f"WHERE business_id IN ({marks}) AND superseded_at IS NULL AND alternative_count > 1 "
            "GROUP BY business_id",
            list(chunk),
        ):
            base[row["business_id"]]["conflict_count"] = row["n"]
    names: dict[str, set[str]] = defaultdict(set)
    sources: dict[str, set[str]] = defaultdict(set)
    places: dict[str, set[tuple[str | None, ...]]] = defaultdict(set)
    categories: dict[str, set[str]] = defaultdict(set)
    domains: dict[str, set[str]] = defaultdict(set)
    current = repository.CURRENT_EXTRACTION
    for chunk in _chunks(sorted(owner)):
        marks = _marks(chunk)
        observed = (
            ("SELECT o.business_id, o.name_raw AS a, x.raw_record_id AS b, NULL AS c, NULL AS d "
             "FROM business_names o", "name"),
            ("SELECT o.business_id, o.country_code AS a, o.region AS b, o.locality AS c, "
             "o.postal_code AS d FROM business_locations o", "place"),
            ("SELECT o.business_id, o.label_raw AS a, NULL AS b, NULL AS c, NULL AS d "
             "FROM business_classifications o", "category"),
            ("SELECT o.business_id, o.domain AS a, NULL AS b, NULL AS c, NULL AS d "
             "FROM websites o", "domain"),
        )  # fmt: skip
        for select, kind in observed:
            for row in conn.execute(
                f"{select} JOIN extractions x ON x.id = o.extraction_id "
                f"WHERE o.business_id IN ({marks}) AND {current}",
                list(chunk),
            ):
                root = owner[row["business_id"]]
                if kind == "name":
                    names[root].add(row["a"])
                    sources[root].add(row["b"])
                elif kind == "place":
                    if any((row["a"], row["b"], row["c"], row["d"])):
                        places[root].add((row["a"], row["b"], row["c"], row["d"]))
                elif kind == "category":
                    categories[root].add(row["a"])
                else:
                    domains[root].add(row["a"])
    fact_audits = _audits(conn, [audits[root] for root in roots if root in audits])
    latino = _latino(conn, roots)
    clusters = contacts_store.load_clusters(conn, [root for root in roots if root in base])
    suppressions = contacts_store.live_suppressions(conn)
    found: dict[str, BusinessFacts] = {}
    for cluster, channels in zip(
        clusters, contacts_store._channels_now(conn, clusters, now), strict=True
    ):
        root = cluster.root
        suppressed = contacts_store.business_suppression(cluster, suppressions) is not None
        people: list[dict[str, object]] = []
        seen: set[str] = set()
        for person in cluster.people:  # newest observation of each name first
            maker = person.maker
            if maker.full_name_normalized in seen:
                continue
            seen.add(maker.full_name_normalized)
            people.append({
                "id": maker.id, "relationship": maker.relationship,
                "review_status": maker.review_status,
                "role": maker.role_raw[:300] if maker.role_raw else None,
            })  # fmt: skip
        found[root] = BusinessFacts.model_validate({
            **base[root],
            "source_count": len(sources[root]),
            "names": sorted(names[root]),
            "locations": [
                {"country_code": a, "region": b, "locality": c, "postal_code": d}
                for a, b, c, d in sorted(places[root], key=lambda p: tuple(v or "" for v in p))
            ],
            "categories": sorted(categories[root]),
            "domains": sorted(domains[root]),
            "audit": fact_audits.get(audits.get(root, "")),
            "channels": [
                {"key": c.key, "contact_type": c.contact_type, "validation_status": c.status,
                 "level": c.level, "decision_maker_id": c.decision_maker_id}
                for c in channels if c.origin is not ContactOrigin.HYPOTHESIS
            ],
            "contactability": contacts_store._contactability(channels, suppressed),
            "people": people,
            "business_suppressed": suppressed,
            "latino": latino.get(root),
        })  # fmt: skip
    return [found[root] for root in roots if root in found]
