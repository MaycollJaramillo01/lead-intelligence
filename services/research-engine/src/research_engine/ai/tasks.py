"""Task contracts of the local AI layer (phase 10): what each task may read, how it is asked, and
what makes an answer acceptable.

Every task has a strict output schema (`domain.*Output`), a versioned prompt template stored as a
file in `prompts/`, input limits, a timeout, a temperature and a failure strategy. An answer is
accepted only when it parses, follows the schema and passes the task's own checks: every cited id
exists in the input, `insufficient_evidence` says nothing else, quotes are literal and numbers come
from the evidence. Web content is untrusted data: it is cleaned, fenced and never read as
instructions; nothing a model answers is ever executed.
"""

import hashlib
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files
from string import Template
from typing import Any, Final

import orjson
from pydantic import BaseModel, ValidationError

from research_engine.domain import (
    AiInputKind,
    AiInputRef,
    AiOutcome,
    AiTaskType,
    BusinessClassificationOutput,
    EvidenceExtractionOutput,
    FindingSynthesisOutput,
    JsonObject,
    LanguageDetectionOutput,
    OpportunityReasoningOutput,
    SalesBriefOutput,
    VisualReviewOutput,
)

#: Answers repaired at most once: a second invalid answer ends the run as `invalid_output`.
MAX_REPAIRS: Final = 1
MAX_RAW_OUTPUT: Final = 200_000
#: Fixed seed: with temperature 0 the same input asks for the same answer.
SEED: Final = 42


#: Invalid JSON, schema or checks: one repair turn with the errors, then `invalid_output`; a runtime
#: failure: `failed` (retryable), with the documented context reduction on out-of-memory.
REPAIR_ONCE: Final = "repair_once_then_invalid_output"


@dataclass(frozen=True, slots=True)
class TaskSpec:
    task_type: AiTaskType
    version: str
    output: type[BaseModel]
    schema_version: str
    #: Kinds of input a task reads; a cited id of another kind is refused.
    ref_kinds: frozenset[AiInputKind]
    max_refs: int
    max_ref_chars: int
    max_input_chars: int
    timeout_s: int
    temperature: float
    #: Output tokens asked of the runtime (Ollama `num_predict`).
    max_output_tokens: int
    #: Short, independent requests that may run back to back on one loaded model.
    batchable: bool
    requires_vision: bool = False
    failure_strategy: str = REPAIR_ONCE
    #: Numbers written in these fields must appear in the cited evidence.
    numbers_from_evidence: bool = False


_TEXT = frozenset({AiInputKind.PAGE_FRAGMENT})

TASKS: Final[Mapping[AiTaskType, TaskSpec]] = {
    spec.task_type: spec
    for spec in (
        TaskSpec(AiTaskType.EVIDENCE_EXTRACTION, "1.0.0", EvidenceExtractionOutput, "1.0.0",
                 _TEXT, max_refs=30, max_ref_chars=400, max_input_chars=6_000, timeout_s=180,
                 temperature=0.0, max_output_tokens=1_500, batchable=True),
        TaskSpec(AiTaskType.BUSINESS_CLASSIFICATION, "1.0.0", BusinessClassificationOutput,
                 "1.0.0", _TEXT, max_refs=20, max_ref_chars=400, max_input_chars=4_000,
                 timeout_s=120, temperature=0.0, max_output_tokens=800, batchable=True),
        TaskSpec(AiTaskType.FINDING_SYNTHESIS, "1.0.0", FindingSynthesisOutput, "1.0.0",
                 frozenset({AiInputKind.WEBSITE_FINDING}), max_refs=25, max_ref_chars=300,
                 max_input_chars=6_000, timeout_s=180, temperature=0.2, max_output_tokens=1_000,
                 batchable=False, numbers_from_evidence=True),
        TaskSpec(AiTaskType.OPPORTUNITY_REASONING, "1.0.0", OpportunityReasoningOutput, "1.0.0",
                 frozenset({AiInputKind.WEBSITE_FINDING, AiInputKind.CONTACTABILITY,
                            AiInputKind.LATINO_STATUS}),
                 max_refs=20, max_ref_chars=300, max_input_chars=5_000, timeout_s=240,
                 temperature=0.2, max_output_tokens=1_200, batchable=False,
                 numbers_from_evidence=True),
        TaskSpec(AiTaskType.LANGUAGE_DETECTION, "1.0.0", LanguageDetectionOutput, "1.0.0",
                 _TEXT, max_refs=20, max_ref_chars=300, max_input_chars=3_000, timeout_s=120,
                 temperature=0.0, max_output_tokens=2_000, batchable=True),
        TaskSpec(AiTaskType.VISUAL_REVIEW, "1.0.0", VisualReviewOutput, "1.0.0",
                 frozenset({AiInputKind.SCREENSHOT}), max_refs=2, max_ref_chars=300,
                 max_input_chars=1_000, timeout_s=240, temperature=0.0, max_output_tokens=800,
                 batchable=False, requires_vision=True),
        TaskSpec(AiTaskType.SALES_BRIEF, "1.0.0", SalesBriefOutput, "1.0.0",
                 frozenset({AiInputKind.BRIEF_CLAIM}), max_refs=40, max_ref_chars=500,
                 max_input_chars=8_000, timeout_s=240, temperature=0.2, max_output_tokens=1_500,
                 batchable=False, numbers_from_evidence=True),
    )
}  # fmt: skip

#: Words a sales brief never uses about a business or its people (checked as whole words).
ACCUSATORY_WORDS: Final = frozenset(
    {
        "amateurish", "awful", "clueless", "disgraceful", "dumb", "embarrassing", "fraud",
        "garbage", "horrible", "idiot", "incompetent", "lazy", "negligent", "pathetic", "scam",
        "shady", "sloppy", "stupid", "terrible", "trash", "ugly", "unprofessional", "useless",
        "worthless",
    }
)  # fmt: skip
_WORDS: Final = re.compile(r"[a-z]+")


def accusatory_word(text: str) -> str | None:
    """The first word of `ACCUSATORY_WORDS` in `text`, or None."""
    return next(
        (word for word in _WORDS.findall(text.casefold()) if word in ACCUSATORY_WORDS), None
    )


# ---- prompt templates ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    id: str
    version: str
    system: Template
    user: Template
    sha256: str


_HEADER = re.compile(r"^# (id|version): (.+)$")


@cache
def template(task: AiTaskType) -> PromptTemplate:
    """The stored template of a task. Its hash joins the cache key, so an edit invalidates results
    even before its version is bumped (a test pins versions to hashes)."""
    text = (files("research_engine.ai") / "prompts" / f"{task.value}.txt").read_text("utf-8")
    text = text.replace("\r\n", "\n")
    header: dict[str, str] = {}
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.split("\n"):
        if current is None and (found := _HEADER.match(line)):
            header[found.group(1)] = found.group(2).strip()
        elif line in ("[system]", "[user]"):
            current = sections.setdefault(line[1:-1], [])
        elif current is not None:
            current.append(line)
    if (
        header.get("id") != task.value
        or "version" not in header
        or set(sections)
        != {
            "system",
            "user",
        }
    ):
        raise ValueError(f"prompt template of {task.value} is malformed")
    return PromptTemplate(
        id=header["id"],
        version=header["version"],
        system=Template("\n".join(sections["system"]).strip()),
        user=Template("\n".join(sections["user"]).strip()),
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


# ---- schemas ------------------------------------------------------------------------------------

#: Constraints this layer checks itself; left out of the grammar the runtime compiles.
_LOCAL_ONLY: Final = frozenset({"pattern", "minLength", "maxLength", "format", "title"})


def _inline(node: Any, defs: Mapping[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            return _inline(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
        return {
            key: _inline(value, defs)
            for key, value in node.items()
            if key not in _LOCAL_ONLY and key not in ("$defs", "description")
        }
    if isinstance(node, list):
        return [_inline(item, defs) for item in node]
    return node


@cache
def model_schema(task: AiTaskType) -> JsonObject:
    """The JSON schema the runtime constrains an answer with: no references, no local rules."""
    schema = TASKS[task].output.model_json_schema()
    inlined: JsonObject = _inline(schema, schema.get("$defs", {}))
    return inlined


@cache
def schema_sha256(task: AiTaskType) -> str:
    full = TASKS[task].output.model_json_schema()
    return hashlib.sha256(orjson.dumps(full, option=orjson.OPT_SORT_KEYS)).hexdigest()


# ---- untrusted content --------------------------------------------------------------------------

_INVISIBLE: Final = re.compile(
    "["
    + "".join(
        f"{re.escape(chr(first))}-{re.escape(chr(last))}"
        for first, last in (
            (0x00, 0x08),
            (0x0B, 0x1F),
            (0x7F, 0x7F),
            (0x200B, 0x200F),
            (0x202A, 0x202E),
            (0x2060, 0x2064),
            (0x2066, 0x2069),
            (0xFEFF, 0xFEFF),
        )
    )
    + "]"
)
_INJECTION: Final = re.compile(
    r"\b(?:ignore|disregard|forget|override|bypass)\b[^.\n]{0,60}"
    r"\b(?:instructions?|prompts?|rules|guidelines)\b"
    r"|\b(?:system|developer)\s+(?:prompt|message)\b"
    r"|\byou\s+are\s+now\b|\bact\s+as\b|\bnew\s+instructions\b"
    r"|\b(?:ignora|olvida|omite)\b[^.\n]{0,60}\binstrucciones\b"
    r"|(?:^|\s)(?:system|assistant|user)\s*:"
    r"|</?\s*untrusted_data",
    re.IGNORECASE,
)


def clean(text: str, limit: int) -> str:
    """Untrusted text as the model will see it: invisible and control characters removed, angle
    brackets replaced so nothing can close the data fence, whitespace collapsed, clipped."""
    text = _INVISIBLE.sub("", text)
    text = text.replace("<", "\N{SINGLE LEFT-POINTING ANGLE QUOTATION MARK}")
    text = " ".join(text.replace(">", "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\N{HORIZONTAL ELLIPSIS}"


def looks_like_injection(text: str) -> bool:
    return _INJECTION.search(text) is not None


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---- prepared input -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PreparedInput:
    """The minimal evidence of one task for one business, already cleaned and bounded."""

    task_type: AiTaskType
    #: Short facts about the business (name, site...), also untrusted.
    facts: Mapping[str, str]
    refs: tuple[AiInputRef, ...]
    #: Image bytes of screenshot refs, in ref order; only their hashes are stored.
    images: tuple[bytes, ...] = ()
    warnings: tuple[str, ...] = field(default=())

    def ref_ids(self) -> frozenset[str]:
        return frozenset(ref.ref for ref in self.refs)

    def input_json(self) -> JsonObject:
        return {
            "facts": dict(self.facts),
            "refs": [ref.model_dump(mode="json") for ref in self.refs],
            "warnings": list(self.warnings),
        }

    def sha256(self) -> str:
        return hashlib.sha256(
            orjson.dumps(self.input_json(), option=orjson.OPT_SORT_KEYS)
        ).hexdigest()


def bounded_refs(
    spec: TaskSpec, prefix: str, pieces: Sequence[tuple[AiInputKind, str | None, str | None, str]]
) -> tuple[tuple[AiInputRef, ...], tuple[str, ...]]:
    """Numbers (kind, entity id, source, text) pieces as refs within the task's limits, flagging the
    ones that read like instructions. The text keeps its place: it is data either way."""
    refs: list[AiInputRef] = []
    warnings: list[str] = []
    used = 0
    for kind, entity_id, source, raw in pieces:
        if len(refs) >= spec.max_refs:
            break
        text = clean(raw, spec.max_ref_chars)
        if not text or used + len(text) > spec.max_input_chars:
            continue
        ref = f"{prefix}{len(refs) + 1}"
        refs.append(
            AiInputRef(
                ref=ref,
                kind=kind,
                entity_id=entity_id,
                source=source[:2048] if source else None,
                text=text,
                sha256=sha256_text(raw),
            )
        )
        used += len(text)
        if looks_like_injection(raw):
            warnings.append(f"suspected_prompt_injection:{ref}")
    return tuple(refs), tuple(warnings)


def render(prepared: PreparedInput) -> tuple[str, str]:
    """(system, user) messages. The schema goes in the instructions; facts and evidence only ever
    inside the data fence."""
    task = prepared.task_type
    prompt = template(task)
    facts = "\n".join(
        f"{name}: {clean(value, 300)}" for name, value in sorted(prepared.facts.items())
    )
    evidence = "\n".join(f"[{ref.ref}] {ref.text}" for ref in prepared.refs)
    schema = orjson.dumps(model_schema(task)).decode()
    return (
        prompt.system.substitute(schema=schema),
        prompt.user.substitute(facts=facts, evidence=evidence),
    )


def repair_message(errors: Sequence[str]) -> str:
    listed = "; ".join(errors[:8])
    return (
        f"Your previous answer was refused: {listed}. Answer again with one JSON object that "
        "follows the schema exactly, citing only the ids you were given, and nothing else."
    )


# ---- checking an answer -------------------------------------------------------------------------

_NUMBER: Final = re.compile(r"\d+(?:[.,]\d+)*")


def _numbers(text: str) -> set[str]:
    return {match.replace(",", "") for match in _NUMBER.findall(text)}


def _cited(node: object) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("evidence_ids", "vertical_evidence_ids") and isinstance(value, list):
                yield from (str(item) for item in value)
            elif key == "evidence_id" and isinstance(value, str):
                yield value
            else:
                yield from _cited(value)
    elif isinstance(node, list):
        for item in node:
            yield from _cited(item)


def _texts(node: object, names: frozenset[str]) -> Iterator[tuple[str, list[str]]]:
    """(text, cited ids) of every field in `names` next to an `evidence_ids` list."""
    if isinstance(node, dict):
        cited = node.get("evidence_ids")
        for key, value in node.items():
            if key in names and isinstance(value, str) and isinstance(cited, list):
                yield value, [str(item) for item in cited]
            else:
                yield from _texts(value, names)
    elif isinstance(node, list):
        for item in node:
            yield from _texts(item, names)


def _empty(output: BaseModel) -> bool:
    return all(
        value in (None, [], "") for name, value in output.model_dump().items() if name != "outcome"
    )


def semantic_errors(output: BaseModel, prepared: PreparedInput) -> list[str]:
    spec = TASKS[prepared.task_type]
    data = output.model_dump(mode="json")
    refs = {ref.ref: ref for ref in prepared.refs}
    errors = [
        f"unknown_evidence_id:{cited}"
        for cited in dict.fromkeys(_cited(data))
        if cited not in refs or refs[cited].kind not in spec.ref_kinds
    ]
    outcome = AiOutcome(data["outcome"])
    if outcome is AiOutcome.INSUFFICIENT_EVIDENCE and not _empty(output):
        errors.append("insufficient_evidence_with_content")
    if outcome is AiOutcome.OK and _empty(output):
        errors.append("ok_without_content")
    if errors:
        return errors

    match output:
        case EvidenceExtractionOutput():
            for index, claim in enumerate(output.claims):
                needle = " ".join(claim.quote.split()).casefold()
                if not any(needle in refs[ref].text.casefold() for ref in claim.evidence_ids):
                    errors.append(f"quote_not_in_cited_evidence:{index}")
        case BusinessClassificationOutput():
            if outcome is AiOutcome.OK and (
                output.vertical is None or not output.vertical_evidence_ids
            ):
                errors.append("vertical_without_evidence")
        case LanguageDetectionOutput():
            seen = [item.evidence_id for item in output.fragments]
            if len(seen) != len(set(seen)):
                errors.append("fragment_repeated")
        case SalesBriefOutput():
            sections = [section.section for section in output.sections]
            if len(sections) != len(set(sections)):
                errors.append("section_repeated")
            errors += [
                f"accusatory_language:{word}"
                for section in output.sections
                for sentence in section.sentences
                if (word := accusatory_word(sentence.text)) is not None
            ]
    if spec.numbers_from_evidence:
        every = " ".join(ref.text for ref in prepared.refs)
        for text, cited in _texts(data, frozenset({"text", "explanation"})):
            known = _numbers(" ".join(refs[ref].text for ref in cited))
            errors += [f"number_not_in_evidence:{n}" for n in sorted(_numbers(text) - known)]
        summary = data.get("summary")
        if isinstance(summary, str):
            errors += [
                f"number_not_in_evidence:{n}" for n in sorted(_numbers(summary) - _numbers(every))
            ]
    return errors


@dataclass(frozen=True, slots=True)
class Checked:
    output: BaseModel | None
    errors: tuple[str, ...]


def check_answer(raw: str, prepared: PreparedInput) -> Checked:
    """Parses, validates and checks one raw answer. Never evaluates or executes any of it."""
    spec = TASKS[prepared.task_type]
    if len(raw) > MAX_RAW_OUTPUT:
        return Checked(None, ("answer_too_long",))
    try:
        document = orjson.loads(raw.strip())
    except orjson.JSONDecodeError as exc:
        return Checked(None, (f"invalid_json: {str(exc)[:120]}",))
    if not isinstance(document, dict):
        return Checked(None, ("not_a_json_object",))
    try:
        output = spec.output.model_validate(document)
    except ValidationError as exc:
        problems = tuple(
            f"schema:{'.'.join(map(str, error['loc'])) or 'answer'}: {error['msg']}"[:200]
            for error in exc.errors()[:8]
        )
        return Checked(None, problems)
    errors = semantic_errors(output, prepared)
    return Checked(None, tuple(errors)) if errors else Checked(output, ())


# ---- cache --------------------------------------------------------------------------------------


def cache_key(
    prepared: PreparedInput,
    *,
    provider: str,
    model: str,
    model_digest: str | None,
    parameters: Mapping[str, object],
) -> str:
    """Same task version, template, schema, runtime, model digest, parameters and input: same key.
    Changing any of them asks again."""
    spec = TASKS[prepared.task_type]
    prompt = template(prepared.task_type)
    request = {
        "task": [spec.task_type.value, spec.version, spec.schema_version],
        "schema_sha256": schema_sha256(spec.task_type),
        "template": [prompt.id, prompt.version, prompt.sha256],
        "runtime": [provider, model, model_digest],
        "parameters": dict(parameters),
        "input_sha256": prepared.sha256(),
        "images": [hashlib.sha256(image).hexdigest() for image in prepared.images],
    }
    return hashlib.sha256(orjson.dumps(request, option=orjson.OPT_SORT_KEYS)).hexdigest()
