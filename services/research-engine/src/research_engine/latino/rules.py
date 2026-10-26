"""Latino evidence rules (phase 07): what counts as evidence, and how evidence becomes a status.

Pure and versioned: no store, no network, no clock (the moment is an argument), so the same
evidence, rules and moment always give the same result. Two tracks never mix:

- ownership: an explicit declaration of Latino or Hispanic ownership, a certification, or a
  directory whose listing criterion is ownership. Only these produce an ownership status.
- market affinity: service in Spanish, pages in Spanish, service to the Hispanic community. They
  produce at most `spanish_market_affinity`, however many there are.

Never read and never evidence: names of the business or of people, surnames, photos, the address
or neighbourhood, presumed nationality, "ethnicity" columns (data vendors usually infer them from
names), and anything a model concludes on its own. A model may point at a sentence of acquired
text; these patterns decide whether that sentence is a claim.
"""

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Final
from urllib.parse import urlsplit

from research_engine.domain import (
    EvidenceFreshness,
    EvidenceRole,
    EvidenceStatus,
    EvidenceStrength,
    EvidenceType,
    LatinoStatus,
    LatinoTrack,
)

RULES_VERSION: Final = "1.0.0"
#: Version of the extraction patterns; it stamps the extractions that store evidence.
EXTRACTOR_VERSION: Final = "1.1.0"


# ---- taxonomy ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClaimRule:
    track: LatinoTrack
    evidence_type: EvidenceType
    strength: EvidenceStrength
    #: Older than this, the evidence is stale and no longer counts.
    max_age_days: int
    #: What the evidence says, stored with it (the interface shows the claim code translated).
    label: str


_OWN, _MKT, _NOT = LatinoTrack.OWNERSHIP, LatinoTrack.MARKET, LatinoTrack.CONTRADICTION
_STRONG, _MODERATE, _WEAK = (
    EvidenceStrength.STRONG,
    EvidenceStrength.MODERATE,
    EvidenceStrength.WEAK,
)
_DECLARATION, _LANGUAGE = EvidenceType.OWNERSHIP_DECLARATION, EvidenceType.MARKET_LANGUAGE

CLAIMS: Final[Mapping[str, ClaimRule]] = {
    "ownership_statement": ClaimRule(_OWN, _DECLARATION, _STRONG, 1095,
        "The business declares that it is Latino- or Hispanic-owned"),
    "ownership_mention": ClaimRule(_OWN, _DECLARATION, _MODERATE, 1095,
        "A page describes the business as Latino- or Hispanic-owned"),
    "ownership_attribute": ClaimRule(_OWN, _DECLARATION, _STRONG, 1095,
        "A source lists the business as Latino- or Hispanic-owned"),
    "certification": ClaimRule(_OWN, EvidenceType.CERTIFICATION, _STRONG, 1095,
        "A source records a Hispanic- or Latino-owned business certification"),
    "certification_statement": ClaimRule(_OWN, EvidenceType.CERTIFICATION, _STRONG, 1095,
        "The business states that it is certified as Hispanic- or Latino-owned"),
    "ownership_directory": ClaimRule(_OWN, EvidenceType.DIRECTORY_MEMBERSHIP, _MODERATE, 730,
        "Listed in a directory of Latino- or Hispanic-owned businesses"),
    "ownership_denied": ClaimRule(_NOT, _DECLARATION, _STRONG, 1095,
        "A source states that the business is not Latino- or Hispanic-owned"),
    "ownership_changed": ClaimRule(_NOT, _DECLARATION, _MODERATE, 1095,
        "The business announces new ownership"),
    "spanish_service": ClaimRule(_MKT, _LANGUAGE, _MODERATE, 730,
        "The business offers service in Spanish"),
    "hispanic_community": ClaimRule(_MKT, _LANGUAGE, _MODERATE, 730,
        "The business says it serves the Hispanic or Latino community"),
    "spanish_language_attribute": ClaimRule(_MKT, _LANGUAGE, _MODERATE, 730,
        "A source lists Spanish among the languages the business serves in"),
    "structured_language": ClaimRule(_MKT, _LANGUAGE, _MODERATE, 730,
        "The site's structured data lists Spanish as an available language"),
    "community_directory": ClaimRule(_MKT, EvidenceType.DIRECTORY_MEMBERSHIP, _WEAK, 730,
        "Listed by a Hispanic or Latino community directory or chamber (not an ownership list)"),
    "spanish_page": ClaimRule(_MKT, _LANGUAGE, _WEAK, 730, "A page is published in Spanish"),
    "spanish_version": ClaimRule(_MKT, _LANGUAGE, _WEAK, 730,
        "The site links to a Spanish version"),
}  # fmt: skip

#: How far a kind of source is trusted to say who owns a business.
SOURCE_RELIABILITY: Final[Mapping[str, float]] = {
    "official_api": 0.95, "public_registry": 0.95, "platform_api": 0.85, "directory": 0.8,
    "business_website": 0.8, "public_dataset": 0.75, "file_import": 0.6, "synthetic": 0.6,
    "manual_seed": 0.5, "web_search": 0.4,
}  # fmt: skip
STRENGTH_WEIGHT: Final[Mapping[EvidenceStrength, float]] = {
    EvidenceStrength.STRONG: 0.9, EvidenceStrength.MODERATE: 0.6, EvidenceStrength.WEAK: 0.3,
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class Ruleset:
    """Everything an evaluation depends on. Changing any value is a new `version`."""

    version: str = RULES_VERSION
    claims: Mapping[str, ClaimRule] = field(default_factory=lambda: CLAIMS)
    source_reliability: Mapping[str, float] = field(default_factory=lambda: SOURCE_RELIABILITY)
    strength_weight: Mapping[EvidenceStrength, float] = field(
        default_factory=lambda: STRENGTH_WEIGHT
    )
    #: Evidence younger than this is fresh; it then loses weight until its claim's maximum age.
    fresh_days: int = 365
    #: `verified` needs strong, fresh evidence about this location from a source this reliable.
    verified_reliability: float = 0.9
    #: A less reliable source is not trusted: its strong evidence counts as moderate.
    trusted_reliability: float = 0.75
    #: Two independent sources scoring this much each make a high-confidence ownership.
    independent_score: float = 0.4


DEFAULT_RULESET: Final = Ruleset()


# ---- patterns ----------------------------------------------------------------------------------

#: Hyphens typed in "Latino-owned" and apostrophes in "we're" (escaped: they look alike).
_HYPHENS: Final = r"\-\N{HYPHEN}\N{NON-BREAKING HYPHEN}\N{EN DASH}"
_APOSTROPHES: Final = r"'\N{RIGHT SINGLE QUOTATION MARK}"
_DASH: Final = rf"[\s{_HYPHENS}]*"
_GROUP: Final = rf"(?:latin[aoxe]|hispanic(?:[\s{_HYPHENS}]+american)?)"
_ORIGIN: Final = (
    r"(?:mexican|puerto[\s\-]rican|cuban|dominican|salvadoran|guatemalan|honduran|nicaraguan|"
    r"costa[\s\-]rican|panamanian|colombian|venezuelan|ecuadorian|peruvian|bolivian|chilean|"
    rf"argentine|argentinian|uruguayan|paraguayan)(?:[\s{_HYPHENS}]+american)?"
)
_OWNERSHIP: Final = re.compile(
    rf"\b(?:{_GROUP}|{_ORIGIN}){_DASH}owned\b"
    rf"|\bowned\s+(?:and\s+operated\s+)?by\s+(?:(?:an?|two|proud)\s+)*(?:{_GROUP}|{_ORIGIN})s?\b"
    r"|\b(?:de\s+)?propiedad\s+(?:latina|hispana|de\s+(?:(?:una?|dos)\s+)?(?:latin|hispan)[oa]s?)\b"
    r"|\b(?:due[ñn]|propietari)[oa]s?\s+(?:latin|hispan)[oa]s?\b",
    re.IGNORECASE,
)
_DENIED: Final = re.compile(
    rf"\bnot\s+(?:an?\s+)?(?:{_GROUP}|{_ORIGIN}){_DASH}owned\b"
    r"|\bno\s+(?:es|somos)\s+(?:una?\s+)?(?:empresa|negocio)\s+(?:de\s+)?propiedad\s+"
    r"(?:latina|hispana)\b",
    re.IGNORECASE,
)
_CHANGED: Final = re.compile(
    r"\bunder\s+new\s+ownership\b|\bbajo\s+nuev[oa]s?\s+(?:propietari[oa]s?|due[ñn][oa]s?|propiedad)\b",
    re.IGNORECASE,
)
_CERTIFIED: Final = re.compile(
    rf"\bcertified\s+(?:as\s+)?(?:an?\s+)?{_GROUP}{_DASH}(?:owned\s+)?(?:small\s+)?"
    r"(?:business|firm|company|enterprise)\b"
    rf"|\b{_GROUP}\s+business\s+enterprise\b"
    r"|\bcertificad[oa]\s+como\s+(?:una?\s+)?(?:empresa|negocio)\s+(?:de\s+propiedad\s+)?"
    r"(?:latina|hispana)\b",
    re.IGNORECASE,
)
_SPANISH_SERVICE: Final = re.compile(
    r"\bse\s+habla\s+espa[ñn]ol\b|\bhablamos\s+espa[ñn]ol\b|\bwe\s+speak\s+spanish\b"
    r"|\bspanish[\s\-]speaking\s+(?:staff|team|technicians?|agents?|representatives?|specialists?)\b"
    r"|\bbilingual\s*\(?\s*(?:english\s*(?:/|and|&)\s*spanish|spanish\s*(?:/|and|&)\s*english)\b"
    r"|\b(?:services?|support|consultations?|appointments?)\s+(?:(?:are|is)\s+)?(?:also\s+)?"
    r"(?:available\s+|offered\s+)?in\s+spanish\b"
    r"|\batenci[oó]n\s+en\s+espa[ñn]ol\b|\bservicios?\s+en\s+espa[ñn]ol\b|\bpersonal\s+biling[üu]e\b",
    re.IGNORECASE,
)
_COMMUNITY: Final = re.compile(
    rf"\bserv(?:e|es|ed|ing)\s+(?:the\s+)?(?:local\s+|greater\s+)?(?:{_GROUP}|spanish[\s\-]speaking)"
    r"\s+communit(?:y|ies)\b|\bcomunidad\s+(?:hispana|latina|hispanohablante)\b",
    re.IGNORECASE,
)
#: Words just before an ownership phrase that make it about other businesses, not this one.
_THIRD_PARTY_WORD: Final = re.compile(
    r"serv(?:e|es|ed|ing)|support(?:s|ed|ing)?|help(?:s|ed|ing)?|empower(?:s|ed|ing)?|"
    r"partner(?:s|ed|ing)?|clients?|customers?|members?|director(?:y|ies)|list(?:s|ed|ing)?|"
    r"resources?|network|programs?|grants?|loans?|advocat\w*|connect(?:s|ed|ing)?|promot\w*|"
    r"celebrat\w*|spotlight\w*|featur\w*|shop|buy|visit|find|apoy\w*|ayud\w*|servimos|atendemos|"
    r"clientes|directorio|lista|red|programas?|miembros|promov\w*|compr\w*|busca\w*",
    re.IGNORECASE,
)
#: Nouns just after it with the same effect: "Latino-owned businesses" are other businesses.
_THIRD_PARTY_AFTER: Final = re.compile(
    r"^[\s\-]*(?:small\s+|local\s+|minority\s+)?(?:businesses|companies|firms|enterprises|vendors|"
    r"suppliers|restaurants|shops|stores|brands|entrepreneurs|owners|contractors|negocios|empresas|"
    r"emprendedores)\b",
    re.IGNORECASE,
)
_FIRST_PERSON: Final = re.compile(
    rf"\b(?:we|we[{_APOSTROPHES}]re|our|ours|us|i|i[{_APOSTROPHES}]m|my|proud(?:ly)?|family|somos|soy|"
    r"nuestr[oa]s?|orgullosamente|familiar)\b",
    re.IGNORECASE,
)
_SPANISH_LINK_TEXT: Final = re.compile(
    r"^\s*(?:espa[ñn]ol|en\s+espa[ñn]ol|ver\s+en\s+espa[ñn]ol|versi[oó]n\s+en\s+espa[ñn]ol)\s*$",
    re.IGNORECASE,
)
_SPANISH_URL: Final = re.compile(r"^https?://(?:es\.|[^/?#]+/es(?:[/?#]|$))", re.IGNORECASE)
_BOUNDARY: Final = re.compile(r"[.!?|•·\n]")
_WORDS: Final = re.compile(rf"[\w{_APOSTROPHES}]+")
_WINDOW_WORDS: Final = 4
SNIPPET_CHARS: Final = 300

_GROUP_WORD: Final = re.compile(r"\b(?:latin[aoxe]s?|hispanic|hispan[oa]s?)\b", re.IGNORECASE)
_OWNED_WORD: Final = re.compile(r"\b(?:owned|ownership|propiedad)\b", re.IGNORECASE)
_CERTIFICATE_WORD: Final = re.compile(r"\b(?:owned|enterprise|certif\w*)\b", re.IGNORECASE)
_SPANISH_WORD: Final = re.compile(r"\b(?:spanish|espa[ñn]ol|es)\b", re.IGNORECASE)

_ES_WORDS: Final = frozenset((
    "de", "la", "que", "el", "en", "y", "los", "las", "por", "para", "con", "una", "su", "sus",
    "nuestro", "nuestra", "nuestros", "nuestras", "servicio", "servicios", "calidad", "llame",
    "hoy", "más", "años", "empresa", "trabajo", "clientes", "contacto", "somos", "ofrecemos",
    "también",
))  # fmt: skip
_EN_WORDS: Final = frozenset((
    "the", "and", "of", "to", "for", "with", "our", "your", "we", "is", "are", "in", "on",
    "service", "services", "quality", "call", "today", "more", "years", "company", "work",
    "customers", "contact", "offer", "also",
))  # fmt: skip
#: A page whose function words are mostly Spanish is published in Spanish.
SPANISH_SHARE: Final = 0.6
_MIN_FUNCTION_WORDS: Final = 20

# structured attributes, by column name folded to lower-case letters and digits
_OWNERSHIP_FLAGS: Final = frozenset({
    "latinoowned", "latinaowned", "latinxowned", "latineowned", "hispanicowned",
    "hispanicorlatinoowned", "latinoorhispanicowned", "hispaniclatinoowned",
    "hispanicamericanowned", "hispanicamericanownedbusiness", "ishispanicowned", "islatinoowned",
    "propiedadlatina", "propiedadhispana",
})  # fmt: skip
_OWNERSHIP_TEXT: Final = frozenset({
    "ownership", "ownershiptype", "ownershipstatus", "businesstype", "businesstypes", "diversity",
    "diversitytype", "diversityclassification", "diversityclassifications",
})  # fmt: skip
_CERTIFICATIONS: Final = frozenset({
    "certification", "certifications", "certificationtype", "certificationtypes",
    "businesscertification", "businesscertifications", "certificacion", "certificaciones",
})  # fmt: skip
_EXPIRY: Final = (
    "certificationexpires", "certificationexpiration", "certificationexpirationdate",
    "certificationvaliduntil", "expirationdate", "validuntil", "expires", "vencimiento",
)  # fmt: skip
_DIRECTORIES: Final = frozenset({
    "directory", "directories", "directorymembership", "directorylisting", "membership",
    "memberships", "chamber", "chambermembership", "directorio", "membresia", "membresias",
})  # fmt: skip
_LANGUAGES: Final = frozenset({
    "languages", "language", "languagesspoken", "spokenlanguages", "servicelanguages",
    "languagesserved", "idiomas", "idioma",
})  # fmt: skip
_SPANISH_FLAGS: Final = frozenset({
    "spanishspoken", "spanishspeaking", "sehablaespanol", "hablaespanol", "spanishservice",
})  # fmt: skip
_TRUE: Final = frozenset({"yes", "y", "true", "t", "1", "x", "si", "sí"})
_FALSE: Final = frozenset({"no", "n", "false", "f", "0"})
_META_TEXT: Final = ("description", "og:description", "twitter:description", "og:title")
_JSON_LD_TEXT: Final = frozenset({"description", "slogan", "disambiguatingdescription", "award"})
_JSON_LD_LANGUAGE: Final = frozenset({"availablelanguage", "knowslanguage", "inlanguage"})


# ---- extraction --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Finding:
    """A claim found in acquired data, with the minimal fragment that shows it."""

    claim: str
    field_path: str
    observed_value: str
    snippet: str
    confidence: float
    detail: Mapping[str, str | float] = field(default_factory=dict)


def _clause(text: str, start: int, end: int) -> tuple[str, str, str]:
    """(the sentence around a match, what precedes the match in it, what follows it)."""
    low, high = max(0, start - 240), min(len(text), end + 240)
    first = low
    for boundary in _BOUNDARY.finditer(text, low, start):
        first = boundary.end()
    stop = _BOUNDARY.search(text, end, high)
    last = stop.start() + 1 if stop else high
    if last - first > SNIPPET_CHARS:
        first, last = max(first, start - SNIPPET_CHARS // 2), min(last, end + SNIPPET_CHARS // 2)
    return " ".join(text[first:last].split()), text[first:start], text[end:last]


def _third_party(before: str, after: str) -> bool:
    words = _WORDS.findall(before)[-_WINDOW_WORDS:]
    return bool(_THIRD_PARTY_AFTER.match(after)) or any(
        _THIRD_PARTY_WORD.fullmatch(word) for word in words
    )


def scan_text(text: str, field_path: str, *, prominent: bool = False) -> list[Finding]:
    """Claims made in one piece of acquired text. Prominent text (a title, a heading, a meta
    description) speaks for the business itself, so an ownership phrase there is a declaration."""
    found: list[Finding] = []
    taken: list[tuple[int, int]] = []

    def add(claim: str, match: re.Match[str], confidence: float) -> None:
        sentence, _, _ = _clause(text, match.start(), match.end())
        found.append(Finding(claim, field_path, match.group(0), sentence, confidence))
        taken.append(match.span())

    def overlaps(match: re.Match[str]) -> bool:
        return any(start < match.end() and match.start() < end for start, end in taken)

    for match in _DENIED.finditer(text):
        add("ownership_denied", match, 0.9)
    for match in _CERTIFIED.finditer(text):
        sentence, before, after = _clause(text, match.start(), match.end())
        if "certif" in sentence.casefold() and not _third_party(before, after):
            add("certification_statement", match, 0.85)
    for match in _OWNERSHIP.finditer(text):
        if overlaps(match):
            continue
        sentence, before, after = _clause(text, match.start(), match.end())
        if _third_party(before, after):
            continue
        declared = prominent or bool(_FIRST_PERSON.search(sentence))
        add(
            "ownership_statement" if declared else "ownership_mention",
            match,
            0.9 if declared else 0.7,
        )
    for match in _CHANGED.finditer(text):
        add("ownership_changed", match, 0.7)
    for match in _SPANISH_SERVICE.finditer(text):
        add("spanish_service", match, 0.8)
    for match in _COMMUNITY.finditer(text):
        add("hispanic_community", match, 0.7)
    return found


def spanish_share(text: str) -> float | None:
    """Share of Spanish among common function words; None when the text has too few of them."""
    words = re.findall(r"[a-záéíóúñü]+", text.casefold())
    spanish = sum(word in _ES_WORDS for word in words)
    english = sum(word in _EN_WORDS for word in words)
    total = spanish + english
    return spanish / total if total >= _MIN_FUNCTION_WORDS else None


def _json_ld(
    value: object, path: str, key: str = "", depth: int = 0
) -> Iterable[tuple[str, str, str]]:
    """(path, property, text) of every text in JSON-LD blocks; a language object gives its name."""
    if depth > 8:
        return
    if isinstance(value, str):
        if key:
            yield path, key, value
    elif isinstance(value, Mapping):
        if key in _JSON_LD_LANGUAGE and isinstance(value.get("name"), str):
            yield path, key, str(value["name"])
        for child, item in value.items():
            yield from _json_ld(item, f"{path}.{child}", str(child).casefold(), depth + 1)
    elif isinstance(value, list):
        for index, item in enumerate(value[:50]):
            yield from _json_ld(item, f"{path}[{index}]", key, depth + 1)


def page_texts(page: Mapping[str, object]) -> Iterable[tuple[str, str, bool]]:
    """(field path, text, prominent) of every text of a crawled page the patterns read."""
    if isinstance(title := page.get("title"), str):
        yield "title", title, True
    meta = page.get("meta")
    if isinstance(meta, Mapping):
        for key in _META_TEXT:
            if isinstance(value := meta.get(key), str):
                yield f"meta.{key}", value, True
    headings = page.get("headings")
    for index, heading in enumerate(headings if isinstance(headings, list) else []):
        if isinstance(heading, Mapping) and isinstance(text := heading.get("text"), str):
            yield f"headings[{index}]", text, True
    if isinstance(main := page.get("main_text"), str):
        yield "main_text", main, False
    for path, key, text in _json_ld(page.get("json_ld"), "json_ld"):
        if key in _JSON_LD_TEXT:
            yield path, text, True


def _is_spanish_language(value: str) -> bool:
    folded = value.strip().casefold()
    return folded in {"spanish", "español", "espanol", "es"} or folded.startswith("es-")


def _strongest(findings: Iterable[Finding]) -> list[Finding]:
    """One finding per claim: the most confident, the first on a tie."""
    best: dict[str, Finding] = {}
    for finding in findings:
        if finding.claim not in best or finding.confidence > best[finding.claim].confidence:
            best[finding.claim] = finding
    return list(best.values())


def _same_site(url: str, page_url: str) -> bool:
    """The link stays on the page's site or one of its subdomains (`es.`)."""
    host = urlsplit(url).hostname or ""
    site = (urlsplit(page_url).hostname or "").removeprefix("www.")
    return host.removeprefix("www.") == site or host.endswith(f".{site}")


def scan_page(page: Mapping[str, object]) -> list[Finding]:
    """Claims of one crawled page of a business's website (`crawl.extract.snapshot` payload)."""
    found: list[Finding] = []
    for path, text, prominent in page_texts(page):
        found += scan_text(text, path, prominent=prominent)
    for path, key, text in _json_ld(page.get("json_ld"), "json_ld"):
        if key in _JSON_LD_LANGUAGE and _is_spanish_language(text):
            found.append(Finding("structured_language", path, text, f"{key}: {text}", 0.8))
    lang, main = page.get("lang"), page.get("main_text")
    share = spanish_share(main) if isinstance(main, str) else None
    if isinstance(lang, str) and lang.strip().casefold().startswith("es"):
        found.append(Finding("spanish_page", "lang", lang, f'lang="{lang}"', 0.6, {"lang": lang}))
    elif share is not None and share >= SPANISH_SHARE:
        found.append(Finding("spanish_page", "main_text", f"{share:.0%} of common words in Spanish",
                             f"{share:.0%} of common words in Spanish", 0.5,
                             {"spanish_share": round(share, 2)}))  # fmt: skip
    links = page.get("links")
    home = page.get("final_url") or page.get("url")
    for index, link in enumerate(links if isinstance(links, list) else []):
        if not isinstance(link, Mapping):
            continue
        link_text, url = link.get("text"), link.get("url")
        if isinstance(url, str) and isinstance(home, str) and not _same_site(url, home):
            continue  # «Español» on a form host, a translator or a maker's page is not this site's
        if (isinstance(link_text, str) and _SPANISH_LINK_TEXT.match(link_text)) or (
            isinstance(url, str) and _SPANISH_URL.match(url)
        ):
            label = f"{link_text or ''} {url or ''}".strip()
            found.append(Finding("spanish_version", f"links[{index}]", str(url), label, 0.6))
            break
    return _strongest(found)


def _fold(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).casefold())


def _text(value: object) -> str | None:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        return " ".join(value.split()) or None
    if isinstance(value, list):
        return "; ".join(text for text in map(_text, value) if text) or None
    return None


def _date(text: str | None) -> date | None:
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime((text or "")[:10], pattern).date()
        except ValueError:
            continue
    return None


def scan_payload(payload: Mapping[str, object]) -> list[Finding]:
    """Claims in the structured attributes of a source record. Only columns that state ownership, a
    certification, a directory or languages are read; everything else is never looked at."""
    folded = {_fold(key): (str(key), value) for key, value in payload.items()}
    found: list[Finding] = []
    for name, (key, value) in folded.items():
        text = _text(value)
        if text is None:
            continue
        flag, path, shown = text.casefold(), f"payload.{key}", f"{key}: {text}"[:SNIPPET_CHARS]
        if name in _OWNERSHIP_FLAGS:
            if flag in _TRUE:
                found.append(Finding("ownership_attribute", path, text, shown, 0.9))
            elif flag in _FALSE:
                found.append(Finding("ownership_denied", path, text, shown, 0.9))
        elif name in _OWNERSHIP_TEXT:
            if _DENIED.search(text):
                found.append(Finding("ownership_denied", path, text, shown, 0.85))
            elif _OWNERSHIP.search(text):
                found.append(Finding("ownership_attribute", path, text, shown, 0.85))
        elif name in _CERTIFICATIONS:
            if _GROUP_WORD.search(text) and _CERTIFICATE_WORD.search(text):
                expiry = next((_date(_text(folded[k][1])) for k in _EXPIRY if k in folded), None)
                detail = {"expires_on": expiry.isoformat()} if expiry else {}
                found.append(Finding("certification", path, text, shown, 0.9, detail))
        elif name in _DIRECTORIES:
            if _GROUP_WORD.search(text):
                owned = bool(_OWNED_WORD.search(text))
                claim = "ownership_directory" if owned else "community_directory"
                found.append(Finding(claim, path, text, shown, 0.75 if owned else 0.6))
        elif (name in _LANGUAGES and _SPANISH_WORD.search(text)) or (
            name in _SPANISH_FLAGS and flag in _TRUE
        ):
            found.append(Finding("spanish_language_attribute", path, text, shown, 0.8))
    return _strongest(found)


#: Claims a model may propose; accepted only when the patterns read the quote the same way.
AI_CLAIMS: Final = frozenset(
    {"ownership_statement", "ownership_mention", "certification_statement", "ownership_denied"}
)
_COMPATIBLE: Final = {
    "ownership_statement": {"ownership_statement", "ownership_mention"},
    "ownership_mention": {"ownership_statement", "ownership_mention"},
    "certification_statement": {"certification_statement"},
    "ownership_denied": {"ownership_denied"},
}


def verify_candidate(claim: str, quote: str, page: Mapping[str, object]) -> Finding | str:
    """A claim a model proposes about a crawled page, checked against the page itself.

    Accepted only when the quote is literally in the acquired text and the patterns read that quote
    as the same kind of claim: the model points, the rules decide. Returns the finding, or the
    reason it was rejected (`claim_not_supported`, `quote_too_short`, `quote_not_found`,
    `not_a_declaration`).
    """
    if claim not in AI_CLAIMS:
        return "claim_not_supported"
    needle = " ".join(quote.split())
    if len(needle) < 12:
        return "quote_too_short"
    for path, text, prominent in page_texts(page):
        if needle.casefold() in " ".join(text.split()).casefold():
            matches = [
                finding
                for finding in scan_text(needle, path, prominent=prominent)
                if finding.claim in _COMPATIBLE[claim]
            ]
            if not matches:
                return "not_a_declaration"
            return replace(matches[0], detail={"proposed_by": "model"})
    return "quote_not_found"


# ---- evaluation --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Fact:
    """One stored piece of evidence, as the rules see it."""

    evidence_id: str
    claim: str
    status: EvidenceStatus
    source_kind: str
    #: Evidence from one source (a website, a connector) is not independent of itself.
    source_key: str
    observed_at: datetime
    #: It describes an organization several locations share (a brand's website).
    organizational: bool = False
    expires_on: date | None = None


@dataclass(frozen=True, slots=True)
class Factor:
    evidence_id: str
    role: EvidenceRole
    source_reliability: float
    freshness: float
    score: float
    counted: bool
    organizational: bool


@dataclass(frozen=True, slots=True)
class Note:
    code: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class Evaluation:
    status: LatinoStatus
    confidence: float
    factors: tuple[Factor, ...]
    warnings: tuple[Note, ...]
    cannot_conclude: tuple[str, ...]
    rationale: str
    limitations: str


#: What an assessment cannot conclude, in words (stored as `limitations`; codes go to the UI).
CANNOT_CONCLUDE: Final[Mapping[str, str]] = {
    "names_not_used": "Names of the business or of people, photos, the address or neighbourhood "
    "and presumed nationality are never used as evidence.",
    "affinity_is_not_ownership": "Service in Spanish or a Hispanic clientele shows market "
    "affinity, not who owns the business.",
    "absence_is_not_evidence": "Finding no evidence does not mean the business is not "
    "Latino-owned.",
    "self_declared": "Ownership rests on what the business or a listing declares; no "
    "certification or registry confirms it.",
    "location_ownership_unknown": "The evidence describes an organization with several "
    "locations; this location may have other owners.",
}
OWNERSHIP_STATUSES: Final = (
    LatinoStatus.VERIFIED_LATINO_OWNED,
    LatinoStatus.HIGH_CONFIDENCE_LATINO_OWNED,
    LatinoStatus.PROBABLE_LATINO_OWNED,
)


def reliability(source_kind: str, ruleset: Ruleset = DEFAULT_RULESET) -> float:
    return ruleset.source_reliability.get(source_kind, min(ruleset.source_reliability.values()))


def freshness(
    claim: str, observed_at: datetime, now: datetime, ruleset: Ruleset = DEFAULT_RULESET
) -> float:
    """1 while fresh, then down to 0.5 at the claim's maximum age, and 0 beyond it."""
    age = max(0.0, (now - observed_at).total_seconds() / 86_400)
    if age <= ruleset.fresh_days:
        return 1.0
    limit = ruleset.claims[claim].max_age_days
    if age > limit:
        return 0.0
    return round(1.0 - 0.5 * (age - ruleset.fresh_days) / max(1, limit - ruleset.fresh_days), 3)


def freshness_label(value: float) -> EvidenceFreshness:
    if value >= 1.0:
        return EvidenceFreshness.FRESH
    return EvidenceFreshness.AGING if value > 0 else EvidenceFreshness.STALE


def is_stale(fact: Fact, now: datetime, ruleset: Ruleset = DEFAULT_RULESET) -> bool:
    """Too old for its claim, or a certification past its expiry date."""
    expired = fact.expires_on is not None and fact.expires_on < now.date()
    return expired or freshness(fact.claim, fact.observed_at, now, ruleset) == 0.0


def contradicted(facts: Sequence[Fact], ruleset: Ruleset = DEFAULT_RULESET) -> set[str]:
    """Ownership evidence that newer active evidence contradicts: the business announcing new
    ownership, or a denial from a source at least as reliable."""
    against = [
        fact
        for fact in facts
        if fact.status is EvidenceStatus.ACTIVE
        and fact.claim in ruleset.claims
        and ruleset.claims[fact.claim].track is LatinoTrack.CONTRADICTION
    ]
    out: set[str] = set()
    for fact in facts:
        rule = ruleset.claims.get(fact.claim)
        if rule is None or rule.track is not LatinoTrack.OWNERSHIP:
            continue
        if fact.status not in (EvidenceStatus.ACTIVE, EvidenceStatus.CONTRADICTED):
            continue
        for other in against:
            if other.observed_at > fact.observed_at and (
                other.claim == "ownership_changed"
                or reliability(other.source_kind, ruleset) >= reliability(fact.source_kind, ruleset)
            ):
                out.add(fact.evidence_id)
                break
    return out


_RANK: Final = {status: rank for rank, status in enumerate(reversed(OWNERSHIP_STATUSES), start=1)}


def _lower(status: LatinoStatus, cap: LatinoStatus) -> LatinoStatus:
    return status if _RANK[status] <= _RANK[cap] else cap


def _combined(scores: Iterable[float]) -> float:
    return 1.0 - math.prod(1.0 - score for score in scores)


def evaluate(
    facts: Sequence[Fact], now: datetime, ruleset: Ruleset = DEFAULT_RULESET
) -> Evaluation:
    """The status the evidence of one business supports, with every factor and caveat.

    - `verified`: strong, fresh evidence about this location from a source at least as reliable as
      `verified_reliability` (a registry, an official source), and nothing contradicting it.
    - `high_confidence`: strong evidence from a trusted source, or two independent sources.
    - `probable`: any other ownership evidence.
    - `spanish_market_affinity`: market signals only (or ownership evidence a denial outweighs).
    - `unknown`: nothing that counts. Evidence the business shares with other locations caps
      ownership at `probable`; a weaker contradiction caps it at `probable` too.
    """
    known = [fact for fact in facts if fact.claim in ruleset.claims]
    rules = {fact.evidence_id: ruleset.claims[fact.claim] for fact in known}
    rel = {fact.evidence_id: reliability(fact.source_kind, ruleset) for fact in known}
    fresh = {
        fact.evidence_id: 0.0 if is_stale(fact, now, ruleset)
        else freshness(fact.claim, fact.observed_at, now, ruleset)
        for fact in known
    }  # fmt: skip
    score = {
        fact.evidence_id: round(
            ruleset.strength_weight[rules[fact.evidence_id].strength]
            * rel[fact.evidence_id]
            * fresh[fact.evidence_id],
            4,
        )
        for fact in known
    }
    active = [f for f in known if f.status is EvidenceStatus.ACTIVE and fresh[f.evidence_id] > 0]
    changes = [f.observed_at for f in active if f.claim == "ownership_changed"]
    changed_at = max(changes, default=None)
    ownership = [f for f in active if rules[f.evidence_id].track is LatinoTrack.OWNERSHIP]
    support = [f for f in ownership if changed_at is None or f.observed_at > changed_at]
    denials = [f for f in active if f.claim == "ownership_denied"]
    market = [f for f in active if rules[f.evidence_id].track is LatinoTrack.MARKET]
    warnings: list[Note] = []
    cannot: list[str] = ["names_not_used"]
    status: LatinoStatus | None = None
    confidence = 0.0
    counted: set[str] = set()

    if len(support) < len(ownership):
        warnings.append(Note("ownership_changed"))
    if support:
        groups: dict[str, float] = {}
        for fact in support:
            groups[fact.source_key] = max(groups.get(fact.source_key, 0.0), score[fact.evidence_id])
        trusted = [
            f
            for f in support
            if rules[f.evidence_id].strength is EvidenceStrength.STRONG
            and rel[f.evidence_id] >= ruleset.trusted_reliability
        ]
        tier = LatinoStatus.PROBABLE_LATINO_OWNED
        if any(
            rel[f.evidence_id] >= ruleset.verified_reliability
            and fresh[f.evidence_id] >= 1.0
            and not f.organizational
            for f in trusted
        ):
            tier = LatinoStatus.VERIFIED_LATINO_OWNED
        elif trusted or sum(value >= ruleset.independent_score for value in groups.values()) >= 2:
            tier = LatinoStatus.HIGH_CONFIDENCE_LATINO_OWNED
        elif any(rules[f.evidence_id].strength is EvidenceStrength.STRONG for f in support):
            warnings.append(Note("untrusted_source"))
        if all(fact.organizational for fact in support):
            tier = _lower(tier, LatinoStatus.PROBABLE_LATINO_OWNED)
            warnings.append(Note("organizational_evidence"))
            cannot.append("location_ownership_unknown")
        confidence = _combined(groups.values())
        decided: LatinoStatus | None = tier
        if denials:
            strongest = max(score[f.evidence_id] for f in denials)
            counted |= {f.evidence_id for f in denials}
            if strongest >= max(groups.values()):
                decided = None
            else:
                decided = _lower(tier, LatinoStatus.PROBABLE_LATINO_OWNED)
                confidence *= 1.0 - strongest
        if decided is not None:
            status = decided
            counted |= {f.evidence_id for f in support}
            confirmed = any(
                rules[f.evidence_id].evidence_type is EvidenceType.CERTIFICATION
                or rel[f.evidence_id] >= ruleset.verified_reliability
                for f in support
            )
            if not confirmed:
                cannot.append("self_declared")
    if status is None:
        if market:
            groups = {}
            for fact in market:
                key = fact.source_key
                groups[key] = max(groups.get(key, 0.0), score[fact.evidence_id])
            status, confidence = LatinoStatus.SPANISH_MARKET_AFFINITY, _combined(groups.values())
            counted |= {f.evidence_id for f in market}
            cannot.append("affinity_is_not_ownership")
        else:
            status, confidence = LatinoStatus.UNKNOWN, 0.0
            cannot.append("absence_is_not_evidence")

    stale = sum(f.status is EvidenceStatus.STALE or fresh[f.evidence_id] == 0 for f in known)
    revoked = sum(f.status is EvidenceStatus.REVOKED for f in known)
    if stale:
        warnings.append(Note("stale_evidence", str(stale)))
    if revoked:
        warnings.append(Note("revoked_evidence", str(revoked)))
    # shown whenever something speaks against ownership, whatever the status ends up being
    if denials or any(f.status is EvidenceStatus.CONTRADICTED for f in known):
        warnings.append(Note("contradicted"))

    roles = {
        LatinoTrack.OWNERSHIP: EvidenceRole.SUPPORTS,
        LatinoTrack.CONTRADICTION: EvidenceRole.CONTRADICTS,
        LatinoTrack.MARKET: EvidenceRole.SUPPORTS
        if status is LatinoStatus.SPANISH_MARKET_AFFINITY
        else EvidenceRole.CONTEXT,
    }
    factors = tuple(
        Factor(
            evidence_id=fact.evidence_id,
            role=roles[rules[fact.evidence_id].track],
            source_reliability=rel[fact.evidence_id],
            freshness=fresh[fact.evidence_id],
            score=score[fact.evidence_id],
            counted=fact.evidence_id in counted,
            organizational=fact.organizational,
        )
        for fact in sorted(known, key=lambda item: item.evidence_id)
    )
    confidence = round(min(confidence, 0.99), 3)
    rationale = (
        f"Rules {ruleset.version}: {status.value}. Ownership evidence counted: "
        f"{len(support) if status in OWNERSHIP_STATUSES else 0} of {len(ownership)} active; "
        f"denials: {len(denials)}; market signals: {len(market)}; not counted: {stale} stale, "
        f"{revoked} revoked, {sum(f.status is EvidenceStatus.CONTRADICTED for f in known)} "
        f"contradicted. Confidence {confidence:.2f}."
    )
    return Evaluation(
        status=status,
        confidence=confidence,
        factors=factors,
        warnings=tuple(warnings),
        cannot_conclude=tuple(cannot),
        rationale=rationale,
        limitations=" ".join(CANNOT_CONCLUDE[code] for code in cannot),
    )
