"""Website intelligence rules (phase 08): a versioned catalogue of deterministic checks over what a
crawl acquired, the subscores they add up to, and what changed between two audits.

`audit(snapshot)` depends on nothing but its snapshot, so an audit can be recalculated from the
stored snapshot with newer rules and compared, never edited. A rule answers `unknown` when the
snapshot cannot show the answer (a page was not acquired, a tag may load in a tag manager) and
`not_applicable` when the question does not arise; neither lowers a score. What was not seen is
reported as not detected, never as absent: no pixel on the pages does not mean no advertising.
Every rule, its thresholds and its limits: docs/website-intelligence.md.
"""

import re
import statistics
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from urllib.parse import urlsplit

from pydantic import TypeAdapter

from research_engine.crawl.robots import parse as parse_robots
from research_engine.crawl.urls import path_and_query, site_of
from research_engine.domain import (
    AcquisitionMethod,
    ConversionElementKind,
    CrawlUrlStatus,
    PageKind,
    SourceRunStatus,
    WebsiteAuditStatus,
    WebsiteAuditSummary,
    WebsiteCategory,
    WebsiteConversionView,
    WebsiteCoverage,
    WebsiteDiff,
    WebsiteEvidence,
    WebsiteFindingChange,
    WebsiteFindingResult,
    WebsiteFindingSeverity,
    WebsiteFindingView,
    WebsitePageView,
    WebsiteScoreChange,
    WebsiteSubscore,
    WebsiteTechnologyView,
)
from research_engine.website.extract import (
    LOCAL_BUSINESS_TYPES,
    ORGANIZATION_TYPES,
    SIGNATURE_BY_ID,
    Detected,
    FormFacts,
    PageFacts,
    clip,
    kinds_of,
    phone_digits,
)

RULESET_VERSION: Final = "1.0.0"
C = WebsiteCategory
S = WebsiteFindingSeverity
PASS: Final = WebsiteFindingResult.PASS
WARNING: Final = WebsiteFindingResult.WARNING
FAIL: Final = WebsiteFindingResult.FAIL
UNKNOWN: Final = WebsiteFindingResult.UNKNOWN
NOT_APPLICABLE: Final = WebsiteFindingResult.NOT_APPLICABLE
FETCHED: Final = CrawlUrlStatus.FETCHED
FAILED: Final = CrawlUrlStatus.FAILED

WEIGHTS: Final = {S.CRITICAL: 10, S.HIGH: 6, S.MEDIUM: 3, S.LOW: 1, S.INFO: 0}
CREDIT: Final = {PASS: 1.0, WARNING: 0.5, FAIL: 0.0}
FORMULA: Final = (
    "score = round(100 * sum(weight * credit) / sum(weight)) over the rules of a category "
    "that could be evaluated; credit: pass 1, warning 0.5, fail 0; weight by severity: "
    "critical 10, high 6, medium 3, low 1, info 0. unknown and not_applicable results are "
    "left out, so they never lower a score. coverage = evaluated weight / (evaluated + "
    "unknown weight); confidence = mean confidence of the evaluated rules, weighted the same "
    "way. There is no overall score."
)
#: How much a technology trace is worth: a script address is a strong sign, a link a weak one.
TRACE_CONFIDENCE: Final = {
    "script": 0.95, "frame": 0.9, "meta": 0.9, "inline": 0.85, "markup": 0.75, "link": 0.7,
}  # fmt: skip
LEAD_PURPOSES: Final = ("contact", "quote", "booking")
#: robots.txt outcomes the crawler reads as "no rules" (RFC 9309: a 4xx means none).
ROBOTS_ABSENT: Final = frozenset({"not_found", "http_error", "unsupported_type", "empty_response"})
#: Failures a visitor would meet too, unlike the crawler's own limits and refusals.
VISITOR_FAILURES: Final = frozenset({
    "not_found", "http_error", "server_error", "dns_error", "network_error", "decoding_error",
    "empty_response", "redirect_loop", "too_many_redirects",
})  # fmt: skip
#: Crawlers of AI assistants and answer engines, as robots.txt names them.
AI_CRAWLERS: Final = ("gptbot", "oai-searchbot", "perplexitybot", "google-extended", "ccbot")
ENTITY_TYPES: Final = LOCAL_BUSINESS_TYPES | ORGANIZATION_TYPES | frozenset({
    "Service", "Product", "Offer", "FAQPage", "WebSite", "Person", "Review", "HowTo", "Article",
})  # fmt: skip
#: Always true of this engine: it measures no Core Web Vitals and has no field data.
NOTES: Final = ("core_web_vitals_unavailable", "field_data_unavailable",
                "security_headers_not_recorded")  # fmt: skip
MAX_REFS: Final = 5
MAX_ELEMENTS: Final = 60
MAX_DIFF_PAGES: Final = 100
NO_HOME: Final = "the home page could not be analyzed"
NO_PAGES: Final = "no page could be analyzed"


# ---- what an audit reads (its snapshot) --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Hop:
    url: str
    status: int
    location: str | None


@dataclass(frozen=True, slots=True)
class PageInput:
    """A page row of the crawl and, when its stored HTML could be read, the facts it shows."""

    crawl_url_id: str
    url: str
    final_url: str | None
    page_kind: PageKind
    depth: int
    status: CrawlUrlStatus
    reason: str | None
    http_status: int | None
    method: AcquisitionMethod | None
    fallback_reason: str | None
    redirects: tuple[Hop, ...]
    headers: tuple[tuple[str, str], ...]
    fetch_ms: int | None
    browser_ms: int | None
    byte_size: int | None
    duplicate_of_id: str | None
    measured_at: datetime
    #: Why there are no facts: fetch_failed, not_fetched, artifact_missing, unreadable, duplicate.
    gap: str | None
    facts: PageFacts | None

    @property
    def address(self) -> str:
        return self.final_url or self.url

    def header(self, name: str) -> str | None:
        return next((value for key, value in self.headers if key == name), None)


@dataclass(frozen=True, slots=True)
class RobotsInput:
    url: str
    crawl_url_id: str
    status: CrawlUrlStatus
    reason: str | None
    text: str | None


@dataclass(frozen=True, slots=True)
class SitemapInput:
    url: str
    crawl_url_id: str
    status: CrawlUrlStatus
    reason: str | None
    locations: int | None


@dataclass(frozen=True, slots=True)
class SiteInput:
    """Everything an audit reads, stored with it as its snapshot. Pages in (depth, url) order."""

    site: str
    root_url: str
    crawl_status: SourceRunStatus
    crawler_version: str
    crawled_at: datetime
    extractor_version: str
    limits_hit: tuple[str, ...]
    robots: RobotsInput | None
    sitemaps: tuple[SitemapInput, ...]
    pages: tuple[PageInput, ...]


SNAPSHOT: Final = TypeAdapter(SiteInput)


class Site:
    """A snapshot with what many rules look up worked out once."""

    def __init__(self, data: SiteInput) -> None:
        self.data = data
        self.pages = [page for page in data.pages if page.duplicate_of_id is None]
        self.facts: list[tuple[PageInput, PageFacts]] = [
            (page, page.facts) for page in self.pages if page.facts is not None
        ]
        self.home = next((page for page in self.pages if page.url == data.root_url), None) or next(
            (page for page in self.pages if page.depth == 0), None
        )
        self.home_facts = self.home.facts if self.home is not None else None
        self.tech: dict[str, list[tuple[PageInput, Detected]]] = {}
        for page, facts in self.facts:
            for detected in facts.technologies:
                self.tech.setdefault(detected.id, []).append((page, detected))
        self.forms: list[tuple[PageInput, FormFacts]] = [
            (page, form)
            for page, facts in self.facts
            for form in facts.forms
            if form.purpose not in ("search", "login")
        ]
        self.lead_forms = [
            (page, form) for page, form in self.forms if form.purpose in LEAD_PURPOSES
        ]
        self._kinds: dict[str, WebsiteEvidence | None] = {}

    def detected(self, *categories: str) -> list[tuple[str, list[tuple[PageInput, Detected]]]]:
        return [
            (key, hits)
            for key, hits in sorted(self.tech.items())
            if SIGNATURE_BY_ID[key].category in categories
        ]

    def kind(self, kind: str) -> WebsiteEvidence | None:
        """Where a page of this kind shows up: an analyzed page, a crawled URL or a link to one."""
        if kind not in self._kinds:
            self._kinds[kind] = self._find(kind)
        return self._kinds[kind]

    def _find(self, kind: str) -> WebsiteEvidence | None:
        for page, facts in self.facts:
            if kind in facts.kinds:
                return ref(page, "page_kind", facts.title or page.address)
        for page in self.data.pages:
            if page.page_kind.value == kind or kind in kinds_of(page.url):
                return ref(page, "crawl_url", page.url)
        for page, facts in self.facts:
            for link in facts.internal_links:
                if kind in kinds_of(link.url, link.text):
                    return ref(page, "link", f"{link.text} -> {link.url}")
        return None


# ---- outcomes ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Outcome:
    result: WebsiteFindingResult
    observed: str | None
    evidence: tuple[WebsiteEvidence, ...] = ()
    confidence: float = 0.95


def evidence(
    url: str | None, crawl_url_id: str | None, field: str, snippet: object = None
) -> WebsiteEvidence:
    text = clip(str(snippet), 500) if snippet is not None and str(snippet).strip() else None
    return WebsiteEvidence(url=url, crawl_url_id=crawl_url_id, field=field, snippet=text)


def ref(page: PageInput, field: str, snippet: object = None) -> WebsiteEvidence:
    return evidence(page.address, page.crawl_url_id, field, snippet)


def passed(observed: str, *refs: WebsiteEvidence, confidence: float = 0.95) -> Outcome:
    return Outcome(PASS, observed, refs[:MAX_REFS], confidence)


def warned(observed: str, *refs: WebsiteEvidence, confidence: float = 0.95) -> Outcome:
    return Outcome(WARNING, observed, refs[:MAX_REFS], confidence)


def failed(observed: str, *refs: WebsiteEvidence, confidence: float = 0.95) -> Outcome:
    return Outcome(FAIL, observed, refs[:MAX_REFS], confidence)


def unknown(observed: str) -> Outcome:
    return Outcome(UNKNOWN, observed, (), 0.0)


def not_applicable(observed: str) -> Outcome:
    return Outcome(NOT_APPLICABLE, observed, (), 1.0)


def _home(site: Site) -> tuple[PageInput, PageFacts] | None:
    home = site.home
    return (home, home.facts) if home is not None and home.facts is not None else None


def _chain(page: PageInput) -> str:
    return " -> ".join([hop.url for hop in page.redirects] + [page.final_url or "?"])


def _phone(digits: str) -> str:
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def _graded(
    value: int, good: int, fair: int, observed: str, *refs: WebsiteEvidence, confidence: float
) -> Outcome:
    """pass up to `good`, warning up to `fair`, fail above."""
    if value <= good:
        return passed(observed, *refs, confidence=confidence)
    if value <= fair:
        return warned(observed, *refs, confidence=confidence)
    return failed(observed, *refs, confidence=confidence)


# ---- technical ---------------------------------------------------------------------------------


def https(site: Site) -> Outcome:
    if site.home is None:
        return unknown("the home page was not crawled")
    address = site.home.address
    seen = ref(site.home, "final_url", address)
    return passed(address, seen) if urlsplit(address).scheme == "https" else failed(address, seen)


def mixed_content(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    secure = [(page, facts) for page, facts in site.facts if facts.https]
    if not secure:
        return not_applicable("no analyzed page is served over https")
    offenders = [(page, facts) for page, facts in secure if facts.mixed_content]
    if not offenders:
        return passed(f"{len(secure)} https pages reference no http resource", confidence=0.9)
    total = sum(len(facts.mixed_content) for _, facts in offenders)
    return failed(
        f"{total} http resources on {len(offenders)} https pages",
        *(ref(page, "mixed_content", facts.mixed_content[0]) for page, facts in offenders),
        confidence=0.9,
    )


def redirect_chain(site: Site) -> Outcome:
    home = site.home
    if home is None:
        return unknown("the home page was not crawled")
    if home.reason in ("redirect_loop", "too_many_redirects"):
        return failed(f"the home page ends in {home.reason}", ref(home, "redirects", _chain(home)))
    if not home.redirects:
        return passed("no redirect")
    return _graded(
        len(home.redirects), 1, 2, f"{len(home.redirects)} redirects: {_chain(home)}",
        ref(home, "redirects", _chain(home)), confidence=0.95,
    )  # fmt: skip


def domain_redirect(site: Site) -> Outcome:
    home = site.home
    if home is None:
        return unknown("the home page was not crawled")
    target = home.final_url
    if home.reason == "redirect_out_of_scope" and home.redirects and home.redirects[-1].location:
        target = home.redirects[-1].location
    if target is None:
        return unknown("the home page has no final address")
    if site_of(target) == site.data.site:
        return passed(f"the site stays on {site.data.site}")
    return warned(
        f"{site.data.site} sends visitors to {site_of(target)}",
        ref(home, "redirects", _chain(home) if home.redirects else target),
    )


def page_errors(site: Site) -> Outcome:
    tried = [page for page in site.pages if page.status in (FETCHED, FAILED)]
    if not tried:
        return unknown("no page was requested")
    broken = [page for page in tried if page.status is FAILED and page.reason in VISITOR_FAILURES]
    if not broken:
        return passed(f"{len(tried)} pages answered")
    reasons = ", ".join(sorted({str(page.reason) for page in broken}))
    observed = f"{len(broken)} of {len(tried)} pages failed ({reasons})"
    refs = [ref(page, "http_status", f"{page.http_status or ''} {page.reason}") for page in broken]
    if any(page is site.home for page in broken) or 5 * len(broken) > len(tried):
        return failed(observed, *refs)
    return warned(observed, *refs)


def broken_links(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    rows = {page.url: page for page in site.data.pages}
    checked: set[str] = set()
    broken: dict[str, WebsiteEvidence] = {}
    for page, facts in site.facts:
        for link in facts.internal_links:
            target = rows.get(link.url)
            if target is None or target.status not in (FETCHED, FAILED):
                continue
            checked.add(link.url)
            if target.status is FAILED and (
                target.reason in ("not_found", "http_error", "server_error")
                or (target.http_status or 0) >= 400
            ):
                answer = target.http_status or target.reason
                broken.setdefault(
                    link.url, ref(page, "link", f"{link.text} -> {link.url} ({answer})")
                )
    if not checked:
        return unknown("the crawl requested none of the linked pages")
    if not broken:
        return passed(f"{len(checked)} linked pages answered")
    return failed(f"{len(broken)} of {len(checked)} linked pages are broken", *broken.values())


def server_response(site: Site) -> Outcome:
    timed = [page for page, _ in site.facts if page.fetch_ms is not None]
    if not timed:
        return unknown("the crawl recorded no response time")
    median = int(statistics.median(page.fetch_ms or 0 for page in timed))
    slowest = max(timed, key=lambda page: page.fetch_ms or 0)
    observed = f"median {median} ms over {len(timed)} pages (lab: one crawler request each)"
    return _graded(
        median, 800, 2000, observed, ref(slowest, "fetch_ms", f"slowest: {slowest.fetch_ms} ms"),
        confidence=0.5,
    )  # fmt: skip


def html_weight(site: Site) -> Outcome:
    home = site.home
    if home is None or home.byte_size is None:
        return unknown("the size of the home page was not recorded")
    return _graded(
        home.byte_size, 512_000, 1_500_000, f"{home.byte_size // 1024} KiB of HTML",
        ref(home, "byte_size", home.byte_size), confidence=0.9,
    )  # fmt: skip


def compression(site: Site) -> Outcome:
    home = site.home
    if home is None or not home.headers or home.reason == "not_modified":
        return unknown("no response headers of the home page to read")
    encoding = (home.header("content-encoding") or "").lower()
    if any(code in encoding for code in ("br", "gzip", "zstd", "deflate")):
        return passed(
            f"content-encoding: {encoding}", ref(home, "headers.content_encoding", encoding)
        )
    return warned(
        "the home page is served uncompressed",
        ref(home, "headers.content_encoding", "none"),
        confidence=0.8,
    )


# ---- search engines ----------------------------------------------------------------------------


def indexable(site: Site) -> Outcome:
    found = _home(site)
    if found is None:
        return unknown(NO_HOME)
    home, facts = found
    directives = " ".join(v for v in (facts.robots, home.header("x-robots-tag")) if v).lower()
    if re.search(r"\b(?:noindex|none)\b", directives):
        return failed(
            "the home page asks search engines not to index it",
            ref(home, "meta.robots", directives),
        )
    robots = site.data.robots
    path = path_and_query(home.address)
    if (
        robots is not None
        and robots.text
        and not parse_robots(robots.text, "googlebot").allowed(path)
    ):
        return failed(
            "robots.txt disallows the home page to Googlebot",
            evidence(robots.url, robots.crawl_url_id, "robots.txt", robots.text),
        )
    return passed("no noindex directive, and robots.txt allows the home page", confidence=0.9)


def title(site: Site) -> Outcome:
    found = _home(site)
    if found is None:
        return unknown(NO_HOME)
    home, facts = found
    if not facts.title:
        return failed("the home page has no title", ref(home, "title"))
    observed = f"{len(facts.title)} characters: {facts.title}"
    seen = ref(home, "title", facts.title)
    return passed(observed, seen) if 10 <= len(facts.title) <= 70 else warned(observed, seen)


def duplicate_metadata(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    if len(site.facts) < 2:
        return not_applicable("one page was analyzed")
    problems: list[str] = []
    refs: list[WebsiteEvidence] = []
    untitled = [page for page, facts in site.facts if not facts.title]
    if untitled:
        problems.append(f"{len(untitled)} pages without a title")
        refs += [ref(page, "title") for page in untitled]
    for field, values in (
        ("title", [(page, facts.title) for page, facts in site.facts]),
        ("description", [(page, facts.description) for page, facts in site.facts]),
    ):
        counts = Counter(value for _, value in values if value)
        shared = [(page, value) for page, value in values if value and counts[value] > 1]
        if shared:
            problems.append(f"{len(shared)} pages share a {field}")
            refs += [ref(page, field, value) for page, value in shared]
    if not problems:
        return passed(f"{len(site.facts)} pages with their own titles and descriptions")
    return warned("; ".join(problems), *refs)


def meta_description(site: Site) -> Outcome:
    found = _home(site)
    if found is None:
        return unknown(NO_HOME)
    home, facts = found
    if not facts.description:
        return failed("the home page has no meta description", ref(home, "meta.description"))
    observed = f"{len(facts.description)} characters: {facts.description}"
    seen = ref(home, "meta.description", facts.description)
    if 50 <= len(facts.description) <= 170:
        return passed(observed, seen)
    return warned(observed, seen)


def h1(site: Site) -> Outcome:
    found = _home(site)
    if found is None:
        return unknown(NO_HOME)
    home, facts = found
    first = next((text for level, text in facts.headings if level == 1), None)
    seen = ref(home, "h1", first)
    if facts.h1 == 1:
        return passed(f"one H1: {first}", seen)
    if facts.h1 == 0:
        return failed("the home page has no H1 heading", seen)
    return warned(f"{facts.h1} H1 headings", seen)


def canonical_url(site: Site) -> Outcome:
    found = _home(site)
    if found is None:
        return unknown(NO_HOME)
    home, facts = found
    if facts.canonical is None:
        return warned("the home page declares no canonical URL", confidence=0.9)
    seen = ref(home, "link.canonical", facts.canonical)
    if site_of(facts.canonical) == site_of(home.address):
        return passed(facts.canonical, seen)
    return failed(f"the canonical URL points to another site: {facts.canonical}", seen)


def sitemap(site: Site) -> Outcome:
    maps = [item for item in site.data.sitemaps if item.status in (FETCHED, FAILED)]
    if not maps:
        return unknown("the crawl read no sitemap")
    read = [item for item in maps if item.status is FETCHED]
    listed = sum(item.locations or 0 for item in read)
    if listed:
        return passed(
            f"{listed} URLs in {len(read)} sitemaps",
            *(evidence(item.url, item.crawl_url_id, "sitemap", item.locations) for item in read),
        )
    if read:
        return warned(
            "the sitemap lists no URL", evidence(read[0].url, read[0].crawl_url_id, "sitemap")
        )
    reasons = ", ".join(sorted({item.reason or "error" for item in maps}))
    return warned(
        f"no sitemap could be read ({reasons})",
        *(evidence(item.url, item.crawl_url_id, "sitemap", item.reason) for item in maps),
        confidence=0.85,
    )


def robots_txt(site: Site) -> Outcome:
    robots = site.data.robots
    if robots is None or robots.status not in (FETCHED, FAILED):
        return unknown("robots.txt was not requested")
    seen = evidence(robots.url, robots.crawl_url_id, "robots.txt", robots.reason or "read")
    if robots.status is FETCHED:
        return passed("robots.txt was read", seen)
    if robots.reason in ROBOTS_ABSENT:
        return warned(f"no usable robots.txt ({robots.reason})", seen, confidence=0.9)
    return failed(
        f"robots.txt could not be read ({robots.reason}): search engines stop crawling such a site",
        seen,
    )


def hreflang(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    spanish = [page for page, facts in site.facts if facts.language == "es"]
    english = [page for page, facts in site.facts if facts.language == "en"]
    if not spanish or not english:
        return not_applicable("the analyzed pages are in one language")
    declared = [(page, facts) for page, facts in site.facts if facts.hreflang]
    if declared:
        page, facts = declared[0]
        codes = ", ".join(code for code, _ in facts.hreflang)
        return passed(f"hreflang on {len(declared)} pages", ref(page, "link.hreflang", codes))
    return warned(
        f"{len(spanish)} Spanish and {len(english)} English pages without hreflang",
        ref(spanish[0], "language", "es"),
        ref(english[0], "language", "en"),
        confidence=0.8,
    )


# ---- local search ------------------------------------------------------------------------------


def phone_visible(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    shown = [
        (page, facts.phones[0] if facts.phones else facts.tel_links[0])
        for page, facts in site.facts
        if facts.phones or facts.tel_links
    ]
    if shown:
        return passed(
            f"a phone number on {len(shown)} of {len(site.facts)} pages",
            *(ref(page, "phone", value) for page, value in shown),
        )
    return failed(f"no phone number on the {len(site.facts)} analyzed pages", confidence=0.85)


def address_or_area(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    for page, facts in site.facts:
        if facts.addresses:
            first = facts.addresses[0]
            return passed(f"address: {first}", ref(page, "address", first), confidence=0.8)
    for page, facts in site.facts:
        if facts.schema.local_business and "address" not in facts.schema.missing_properties:
            return passed(
                "address in structured data", ref(page, "json_ld.address"), confidence=0.9
            )
    for page, facts in site.facts:
        if facts.service_area:
            area = facts.service_area
            return passed(f"service area: {area}", ref(page, "service_area", area), confidence=0.7)
        if facts.schema.area_served:
            return passed("areaServed in structured data", ref(page, "json_ld.area_served"))
    return warned("no address or service area stated", confidence=0.7)


def local_business_schema(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    for page, facts in site.facts:
        if facts.schema.local_business:
            kinds = ", ".join(kind for kind in facts.schema.types if kind in LOCAL_BUSINESS_TYPES)
            return passed(kinds, ref(page, "json_ld", ", ".join(facts.schema.types)))
    for page, facts in site.facts:
        if facts.schema.organization:
            return warned(
                "Organization structured data, but no LocalBusiness type",
                ref(page, "json_ld", ", ".join(facts.schema.types)),
            )
    invalid = [(page, facts) for page, facts in site.facts if facts.schema.invalid_blocks]
    if invalid:
        blocks = sum(facts.schema.invalid_blocks for _, facts in invalid)
        return failed(
            f"{blocks} JSON-LD blocks are not valid",
            *(ref(page, "json_ld", facts.schema.invalid_snippet) for page, facts in invalid),
        )
    return failed("no LocalBusiness structured data", confidence=0.9)


def schema_properties(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    local = [(page, facts) for page, facts in site.facts if facts.schema.local_business]
    if not local:
        return not_applicable("no LocalBusiness structured data")
    page, facts = min(local, key=lambda item: len(item[1].schema.missing_properties))
    seen = ref(page, "json_ld", ", ".join(facts.schema.types))
    if not facts.schema.missing_properties:
        return passed("name, address and telephone are present", seen)
    return warned(f"missing: {', '.join(facts.schema.missing_properties)}", seen)


def nap_consistency(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    declared = {phone: page for page, facts in site.facts for phone in facts.schema.telephones}
    shown: set[str] = set()
    for _, facts in site.facts:
        shown.update(facts.phones)
        shown.update(digits for link in facts.tel_links if (digits := phone_digits(link)))
    if not declared:
        return not_applicable("no telephone in structured data")
    if not shown:
        return not_applicable("no phone number is shown on the pages")
    missing = sorted(set(declared) - shown)
    if not missing:
        return passed("the structured-data telephone is the one the pages show")
    return warned(
        f"the structured-data telephone {_phone(missing[0])} is not shown on the pages",
        ref(declared[missing[0]], "json_ld.telephone", _phone(missing[0])),
    )


def hours(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    for page, facts in site.facts:
        if facts.hours:
            return passed(facts.hours, ref(page, "hours", facts.hours), confidence=0.75)
    for page, facts in site.facts:
        if facts.schema.opening_hours:
            return passed("opening hours in structured data", ref(page, "json_ld.opening_hours"))
    return warned("no opening hours stated", confidence=0.7)


def map_or_directions(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    for page, facts in site.facts:
        if facts.map_links:
            return passed(f"map: {facts.map_links[0]}", ref(page, "map", facts.map_links[0]))
        if directions := next((cta for cta in facts.ctas if cta.kind == "directions"), None):
            seen = ref(page, "cta", directions.text)
            return passed(f"directions: {directions.text}", seen, confidence=0.85)
    return warned("no map or directions", confidence=0.8)


def locations(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    addresses = sorted({address for _, facts in site.facts for address in facts.addresses})
    page_ref = site.kind("locations")
    if len(addresses) < 2 and page_ref is None:
        return not_applicable("the pages show one location or none")
    if page_ref is not None:
        return passed(f"{len(addresses)} addresses and a locations page", page_ref, confidence=0.8)
    first = next(page for page, facts in site.facts if facts.addresses)
    return warned(
        f"{len(addresses)} addresses but no locations page",
        ref(first, "address", "; ".join(addresses[:3])),
        confidence=0.75,
    )


# ---- content -----------------------------------------------------------------------------------


def linked_page(kind: str, missing: WebsiteFindingResult, label: str) -> Callable[[Site], Outcome]:
    def check(site: Site) -> Outcome:
        if site.home_facts is None:
            return unknown(NO_HOME)
        if (found := site.kind(kind)) is not None:
            return passed(f"{label} found", found, confidence=0.85)
        return Outcome(missing, f"no {label} among the analyzed pages or their links", (), 0.7)

    return check


def privacy_policy(site: Site) -> Outcome:
    if site.home_facts is None:
        return unknown(NO_HOME)
    if (found := site.kind("privacy")) is not None:
        return passed("privacy policy found", found, confidence=0.85)
    if site.lead_forms:
        page, form = site.lead_forms[0]
        return failed(
            f"{len(site.lead_forms)} forms collect personal data and no privacy policy is linked",
            ref(page, "form", form.purpose),
            confidence=0.7,
        )
    return warned("no privacy policy among the analyzed pages or their links", confidence=0.7)


def home_content(site: Site) -> Outcome:
    found = _home(site)
    if found is None:
        return unknown(NO_HOME)
    home, facts = found
    observed = f"{facts.words} words of main content"
    seen = ref(home, "main_text", observed)
    if facts.words >= 250:
        return passed(observed, seen, confidence=0.85)
    if facts.words >= 80:
        return warned(observed, seen, confidence=0.85)
    return failed(observed, seen, confidence=0.85)


def freshness(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    years = [(page, facts.copyright_year) for page, facts in site.facts if facts.copyright_year]
    if not years:
        return unknown("no copyright year to judge by")
    page, year = max(years, key=lambda item: item[1] or 0)
    seen = ref(page, "copyright", year)
    if (year or 0) >= site.data.crawled_at.year - 1:
        return passed(f"copyright {year}", seen, confidence=0.6)
    return warned(f"the newest copyright year is {year}", seen, confidence=0.6)


def open_graph(site: Site) -> Outcome:
    found = _home(site)
    if found is None:
        return unknown(NO_HOME)
    home, facts = found
    missing = sorted({"og:title", "og:image"} - set(facts.open_graph))
    seen = ref(home, "meta.open_graph", ", ".join(facts.open_graph) or "none")
    if not missing:
        return passed(", ".join(facts.open_graph), seen)
    return warned(f"missing: {', '.join(missing)}", seen, confidence=0.9)


def favicon(site: Site) -> Outcome:
    found = _home(site)
    if found is None:
        return unknown(NO_HOME)
    home, facts = found
    if facts.favicon:
        return passed("an icon is declared", ref(home, "link.icon"))
    return warned("no icon is declared (a /favicon.ico may still be served)", confidence=0.7)


# ---- conversion --------------------------------------------------------------------------------


def primary_cta(site: Site) -> Outcome:
    found = _home(site)
    if found is None:
        return unknown(NO_HOME)
    home, facts = found
    actions = [
        cta for cta in facts.ctas if cta.kind in ("call", "quote", "booking", "contact", "buy")
    ]
    if primary := [cta for cta in actions if cta.primary]:
        seen = ref(home, "cta", primary[0].text)
        return passed(f"{primary[0].kind}: {primary[0].text}", seen, confidence=0.8)
    if actions:
        seen = ref(home, "cta", actions[0].text)
        return warned(f"actions only outside the header: {actions[0].text}", seen, confidence=0.7)
    return failed("the home page has no call to action", confidence=0.8)


def click_to_call(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    for page, facts in site.facts:
        if facts.tel_links:
            return passed(f"tel: {facts.tel_links[0]}", ref(page, "tel_link", facts.tel_links[0]))
    for page, facts in site.facts:
        if facts.phones:
            number = _phone(facts.phones[0])
            return failed(
                f"{number} is shown as plain text, not as a tap-to-call link",
                ref(page, "phone", number),
            )
    return failed("no phone number to call", confidence=0.85)


def lead_form(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    if site.lead_forms:
        page, form = site.lead_forms[0]
        seen = ref(page, "form", form.submit or form.purpose)
        return passed(f"{form.purpose} form with {form.fields} fields", seen, confidence=0.85)
    if tools := site.detected("forms"):
        names = ", ".join(SIGNATURE_BY_ID[key].name for key, _ in tools)
        page, hit = tools[0][1][0]
        return passed(
            f"form tool: {names}", ref(page, f"tech.{hit.field}", hit.snippet), confidence=0.8
        )
    if others := site.detected("booking", "chat"):
        names = ", ".join(SIGNATURE_BY_ID[key].name for key, _ in others)
        return warned(f"no form; {names} is the written channel", confidence=0.75)
    return failed("no contact, quote or booking form on the analyzed pages", confidence=0.8)


def form_friction(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    if not site.lead_forms:
        return not_applicable("no lead form")
    page, form = max(site.lead_forms, key=lambda item: item[1].fields)
    observed = f"the longest lead form asks for {form.fields} fields ({form.required} required)"
    return _graded(form.fields, 7, 12, observed, ref(page, "form", form.purpose), confidence=0.85)


def form_security(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    if not site.forms:
        return not_applicable("no form")
    insecure = [
        (page, form)
        for page, form in site.forms
        if form.insecure_action or urlsplit(page.address).scheme != "https"
    ]
    if insecure:
        return failed(
            f"{len(insecure)} of {len(site.forms)} forms send data without https",
            *(ref(page, "form.action", form.action or page.address) for page, form in insecure),
        )
    return passed(f"{len(site.forms)} forms submit over https")


def booking_or_quote(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    if booking := site.detected("booking"):
        page, hit = booking[0][1][0]
        names = ", ".join(SIGNATURE_BY_ID[key].name for key, _ in booking)
        return passed(f"booking tool: {names}", ref(page, f"tech.{hit.field}", hit.snippet))
    for page, facts in site.facts:
        if action := next((cta for cta in facts.ctas if cta.kind in ("booking", "quote")), None):
            seen = ref(page, "cta", action.text)
            return passed(f"{action.kind}: {action.text}", seen, confidence=0.8)
    for page, form in site.lead_forms:
        if form.purpose in ("booking", "quote"):
            return passed(f"{form.purpose} form", ref(page, "form", form.purpose), confidence=0.85)
    return warned("no online booking or quote request", confidence=0.7)


def chat_or_messaging(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    if chat := site.detected("chat"):
        page, hit = chat[0][1][0]
        names = ", ".join(SIGNATURE_BY_ID[key].name for key, _ in chat)
        return passed(f"chat: {names}", ref(page, f"tech.{hit.field}", hit.snippet))
    for page, facts in site.facts:
        if facts.messaging_links:
            link = facts.messaging_links[0]
            return passed(
                f"messaging link: {link}", ref(page, "messaging_link", link), confidence=0.9
            )
    if site.detected("tag_manager"):
        return unknown("not detected: a chat widget may load through the tag manager")
    return warned("not detected: no chat widget or messaging link", confidence=0.6)


def trust_signals(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    kinds: dict[str, WebsiteEvidence] = {}
    for page, facts in site.facts:
        for signal in facts.trust:
            kinds.setdefault(signal.id, ref(page, f"trust.{signal.id}", signal.snippet))
        if facts.schema.aggregate_rating:
            kinds.setdefault("rating", ref(page, "json_ld.aggregate_rating"))
    for key, hits in site.detected("reviews"):
        page, hit = hits[0]
        kinds.setdefault("review_widget", ref(page, f"tech.{hit.field}", SIGNATURE_BY_ID[key].name))
    observed = ", ".join(sorted(kinds)) or "none"
    if len(kinds) >= 2:
        return passed(observed, *kinds.values(), confidence=0.75)
    if kinds:
        return warned(f"only one kind: {observed}", *kinds.values(), confidence=0.75)
    return failed(
        "no reviews, licence, insurance, guarantee, portfolio or experience stated", confidence=0.7
    )


def contact_in_navigation(site: Site) -> Outcome:
    found = _home(site)
    if found is None:
        return unknown(NO_HOME)
    home, facts = found
    for link in facts.internal_links:
        if link.in_header and "contact" in kinds_of(link.url, link.text):
            return passed(f"{link.text} -> {link.url}", ref(home, "navigation", link.text))
    for cta in facts.ctas:
        if cta.primary and cta.kind == "call":
            return passed(f"call button: {cta.text}", ref(home, "cta", cta.text))
    return warned("no contact link or call button in the header or navigation", confidence=0.75)


# ---- mobile and accessibility ------------------------------------------------------------------


def viewport(site: Site) -> Outcome:
    found = _home(site)
    if found is None:
        return unknown(NO_HOME)
    home, facts = found
    if not facts.viewport:
        return failed("the home page has no viewport meta tag", ref(home, "meta.viewport"))
    seen = ref(home, "meta.viewport", facts.viewport)
    if "width=device-width" in facts.viewport.replace(" ", "").lower():
        return passed(facts.viewport, seen)
    return warned(facts.viewport, seen)


def zoom(site: Site) -> Outcome:
    found = _home(site)
    if found is None:
        return unknown(NO_HOME)
    home, facts = found
    if not facts.viewport:
        return not_applicable("no viewport meta tag")
    seen = ref(home, "meta.viewport", facts.viewport)
    if facts.zoom_disabled:
        return failed(f"zoom is disabled: {facts.viewport}", seen)
    return passed("people can zoom", seen)


def lang(site: Site) -> Outcome:
    found = _home(site)
    if found is None:
        return unknown(NO_HOME)
    home, facts = found
    if facts.lang:
        return passed(f"lang={facts.lang}", ref(home, "html.lang", facts.lang))
    return failed("the html element declares no language", ref(home, "html.lang"))


def image_alt(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    images = sum(facts.images for _, facts in site.facts)
    if not images:
        return not_applicable("no images")
    missing = sum(facts.images_missing_alt for _, facts in site.facts)
    refs = [
        ref(page, "img.alt", f"{facts.images_missing_alt} of {facts.images} without alt")
        for page, facts in site.facts
        if facts.images_missing_alt
    ]
    observed = f"{missing} of {images} images have no alt attribute"
    if not missing:
        return passed(observed)
    return warned(observed, *refs) if missing * 10 <= images else failed(observed, *refs)


def form_labels(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    fields = sum(form.fields for _, form in site.forms)
    if not fields:
        return not_applicable("no form fields")
    unlabeled = [(page, form) for page, form in site.forms if form.unlabeled]
    count = sum(form.unlabeled for _, form in unlabeled)
    if not count:
        return passed(f"{fields} form fields are labelled")
    return failed(
        f"{count} of {fields} form fields have no label",
        *(
            ref(page, "form.label", f"{form.purpose}: {form.unlabeled} unlabeled")
            for page, form in unlabeled
        ),
    )


def link_names(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    empty = sum(facts.empty_links for _, facts in site.facts)
    refs = [
        ref(page, "a.name", facts.empty_links) for page, facts in site.facts if facts.empty_links
    ]
    return _graded(empty, 0, 3, f"{empty} links without a name", *refs, confidence=0.9)


def heading_order(site: Site) -> Outcome:
    found = _home(site)
    if found is None:
        return unknown(NO_HOME)
    home, facts = found
    if not facts.headings:
        return not_applicable("no headings")
    if not facts.heading_skips:
        return passed(f"{len(facts.headings)} headings in order")
    return warned(f"{facts.heading_skips} heading levels skipped", ref(home, "headings"))


# ---- measurement -------------------------------------------------------------------------------


def _detected_outcome(site: Site, categories: tuple[str, ...], missing: Outcome) -> Outcome:
    found = site.detected(*categories)
    if not found:
        return missing
    names = ", ".join(SIGNATURE_BY_ID[key].name for key, _ in found)
    refs = [ref(hits[0][0], f"tech.{hits[0][1].field}", hits[0][1].snippet) for _, hits in found]
    return passed(
        names,
        *refs,
        confidence=max(TRACE_CONFIDENCE[h[1].field] for _, hits in found for h in hits),
    )


def analytics(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    missing = (
        unknown("not detected: analytics may load inside the tag manager")
        if site.detected("tag_manager")
        else warned("not detected: no analytics tag on the analyzed pages", confidence=0.6)
    )
    return _detected_outcome(site, ("analytics",), missing)


def tag_manager(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    return _detected_outcome(site, ("tag_manager",), unknown("not detected"))


def advertising(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    missing = unknown(
        "not detected: no advertising or call-tracking tag was seen, which does not show that the "
        "business does not advertise"
    )
    return _detected_outcome(site, ("advertising", "call_tracking"), missing)


# ---- answer engines and AI assistants ----------------------------------------------------------


def content_without_javascript(site: Site) -> Outcome:
    analyzed = [page for page, _ in site.facts]
    if not analyzed:
        return unknown(NO_PAGES)
    needed = [
        page
        for page in analyzed
        if page.fallback_reason in ("empty_shell", "js_required", "http_functional_block")
    ]
    if needed:
        return failed(
            f"{len(needed)} of {len(analyzed)} pages show their content only in a browser",
            *(ref(page, "fallback_reason", page.fallback_reason) for page in needed),
        )
    if all(page.fallback_reason == "configured" for page in analyzed):
        return unknown("every page was rendered on request, so the plain HTML was not judged")
    return passed(f"{len(analyzed)} pages show their content without JavaScript", confidence=0.9)


def structured_data(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    invalid = [(page, facts) for page, facts in site.facts if facts.schema.invalid_blocks]
    typed = [(page, facts) for page, facts in site.facts if set(facts.schema.types) & ENTITY_TYPES]
    invalid_refs = [ref(page, "json_ld", facts.schema.invalid_snippet) for page, facts in invalid]
    if typed and not invalid:
        page, facts = typed[0]
        kinds = ", ".join(kind for kind in facts.schema.types if kind in ENTITY_TYPES)
        return passed(kinds, ref(page, "json_ld", ", ".join(facts.schema.types)))
    if typed:
        return warned("valid structured data, and some JSON-LD blocks that are not", *invalid_refs)
    if invalid:
        return failed("the only JSON-LD blocks are not valid", *invalid_refs)
    for page, facts in site.facts:
        if facts.schema.microdata_types:
            kinds = ", ".join(facts.schema.microdata_types)
            return passed(f"microdata: {kinds}", ref(page, "microdata", kinds), confidence=0.8)
    return failed("no structured data", confidence=0.9)


def faq(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    for page, facts in site.facts:
        if facts.schema.faq:
            return passed("FAQPage structured data", ref(page, "json_ld.faq_page"))
    if (found := site.kind("faq")) is not None:
        return passed("FAQ page", found, confidence=0.85)
    asked = [(page, facts) for page, facts in site.facts if facts.question_headings]
    questions = sum(facts.question_headings for _, facts in asked)
    refs = [ref(page, "headings", f"{facts.question_headings} questions") for page, facts in asked]
    if questions >= 3:
        return passed(f"{questions} question headings", *refs, confidence=0.8)
    if questions:
        return warned(f"{questions} question headings", *refs, confidence=0.8)
    return failed("no questions and answers on the analyzed pages", confidence=0.75)


def entity_profiles(site: Site) -> Outcome:
    if not site.facts:
        return unknown(NO_PAGES)
    for page, facts in site.facts:
        if facts.schema.same_as:
            observed = f"sameAs lists {facts.schema.same_as} profiles"
            return passed(observed, ref(page, "json_ld.same_as", facts.schema.same_as))
    for page, facts in site.facts:
        if facts.social_links:
            link = facts.social_links[0]
            return passed(f"profile link: {link}", ref(page, "social_link", link), confidence=0.85)
    return warned("no links to the business's profiles elsewhere", confidence=0.8)


def ai_crawlers(site: Site) -> Outcome:
    robots = site.data.robots
    if robots is None or robots.status not in (FETCHED, FAILED):
        return unknown("robots.txt was not requested")
    seen = evidence(robots.url, robots.crawl_url_id, "robots.txt", robots.text or robots.reason)
    if robots.status is FAILED:
        if robots.reason in ROBOTS_ABSENT:
            return passed("no robots.txt, so no crawler is restricted", seen, confidence=0.9)
        return unknown(f"robots.txt could not be read ({robots.reason})")
    blocked = [
        agent for agent in AI_CRAWLERS if not parse_robots(robots.text or "", agent).allowed("/")
    ]
    if blocked:
        return warned(f"robots.txt blocks {', '.join(blocked)}", seen)
    return passed("robots.txt lets the crawlers of AI assistants read the site", seen)


# ---- the catalogue -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    version: str
    category: WebsiteCategory
    #: The weight of the rule, whatever its result.
    severity: WebsiteFindingSeverity
    title: str
    expected: str
    remediation: str
    relevance: str
    check: Callable[[Site], Outcome]


@dataclass(frozen=True, slots=True)
class Ruleset:
    version: str
    rules: tuple[Rule, ...]


V: Final = RULESET_VERSION
RULES: Final[tuple[Rule, ...]] = (
    # technical
    Rule("technical.https", V, C.TECHNICAL, S.HIGH, "Served over HTTPS",
         "the home page ends on an https address",
         "Install a TLS certificate and redirect every http address to https.",
         "Browsers label http pages 'Not secure', which turns visitors away before they call.",
         https),
    Rule("technical.mixed_content", V, C.TECHNICAL, S.MEDIUM, "No mixed content",
         "https pages load no resource over http",
         "Load scripts, images, frames and stylesheets over https.",
         "Browsers block or flag insecure resources: broken images, forms and trust warnings.",
         mixed_content),
    Rule("technical.redirect_chain", V, C.TECHNICAL, S.MEDIUM, "Short redirect chain",
         "the home page answers after at most one redirect (two is a warning)",
         "Point links and the http and www variants straight to the final address.",
         "Every hop delays the first screen, on mobile networks most of all.",
         redirect_chain),
    Rule("technical.domain_redirect", V, C.TECHNICAL, S.MEDIUM, "Stays on its own domain",
         "the home page does not send visitors to another domain",
         "If the business moved, point listings and ads at the new domain; else drop the redirect.",
         "Listings and ads pointing at an old domain waste visits and confuse search engines.",
         domain_redirect),
    Rule("technical.page_errors", V, C.TECHNICAL, S.HIGH, "Pages answer",
         "the crawled pages answer without errors (a failing home page or over 20% fail)",
         "Fix or redirect the pages that return errors, starting with the home page.",
         "An error page loses the visitor who reached it.",
         page_errors),
    Rule("technical.broken_links", V, C.TECHNICAL, S.MEDIUM, "No broken internal links",
         "links between pages lead to pages that answer",
         "Update or remove links to missing pages.",
         "Broken links end visits and waste the crawl budget of search engines.",
         broken_links),
    Rule("technical.server_response", V, C.TECHNICAL, S.LOW, "Server response time (lab)",
         "a median crawler response of at most 800 ms (2 s is a warning)",
         "Use caching, a CDN or a faster host.",
         "Slow answers raise abandonment; a lab measurement, not what visitors experience.",
         server_response),
    Rule("technical.html_weight", V, C.TECHNICAL, S.LOW, "Lean home page HTML",
         "home page HTML of at most 500 KiB (1.5 MiB is a warning)",
         "Remove inline data, unused markup and embedded assets from the HTML.",
         "Heavy pages load slowly on phones and cost visitors data.",
         html_weight),
    Rule("technical.compression", V, C.TECHNICAL, S.LOW, "Compressed responses",
         "the home page is served with gzip, Brotli, Zstandard or deflate",
         "Enable compression on the web server or CDN.",
         "Uncompressed HTML loads slower, especially on mobile networks.",
         compression),
    # search engines
    Rule("seo.indexable", V, C.SEO, S.CRITICAL, "Home page can be indexed",
         "no noindex directive, and robots.txt allows the home page to search engines",
         "Remove noindex from the home page and allow it in robots.txt.",
         "A page search engines may not index cannot be found by people looking for the business.",
         indexable),
    Rule("seo.title", V, C.SEO, S.HIGH, "Home page title",
         "a title of 10 to 70 characters",
         "Write a title with the business name, its main service and its area.",
         "The title is the headline of the search result people click.",
         title),
    Rule("seo.duplicate_metadata", V, C.SEO, S.MEDIUM, "Distinct titles and descriptions",
         "every analyzed page has a title, and no two pages share a title or description",
         "Give each page a title and description that say what it offers.",
         "Duplicated metadata makes pages compete with each other in search results.",
         duplicate_metadata),
    Rule("seo.meta_description", V, C.SEO, S.MEDIUM, "Home page meta description",
         "a description of 50 to 170 characters",
         "Summarize the offer, the area and the next step in one or two sentences.",
         "The description is the text under the search result and often decides the click.",
         meta_description),
    Rule("seo.h1", V, C.SEO, S.MEDIUM, "One main heading",
         "exactly one H1 on the home page",
         "Use one H1 that says what the business does and where.",
         "The main heading tells visitors and search engines what the page is about.",
         h1),
    Rule("seo.canonical", V, C.SEO, S.LOW, "Canonical URL",
         "the home page declares a canonical URL on its own site",
         'Add <link rel="canonical"> pointing at the preferred address of each page.',
         "Without it, duplicate addresses split ranking signals.",
         canonical_url),
    Rule("seo.sitemap", V, C.SEO, S.LOW, "XML sitemap",
         "a sitemap that lists URLs can be read",
         "Publish /sitemap.xml and declare it in robots.txt.",
         "Sitemaps help search engines find every service and location page.",
         sitemap),
    Rule("seo.robots_txt", V, C.SEO, S.LOW, "robots.txt",
         "robots.txt can be read",
         "Serve a robots.txt that allows the public pages and declares the sitemap.",
         "A robots.txt that errors can stop search engines from crawling the site at all.",
         robots_txt),
    Rule("seo.hreflang", V, C.SEO, S.LOW, "Language versions linked",
         "a site with Spanish and English pages declares hreflang alternates",
         "Add hreflang links between the language versions of each page.",
         "Search engines can then show Spanish speakers the Spanish page.",
         hreflang),
    # local search
    Rule("local_seo.phone_visible", V, C.LOCAL_SEO, S.HIGH, "Phone number shown",
         "a phone number appears on the site",
         "Show the business phone in the header or footer of every page.",
         "Local customers call: a phone they cannot find is a lost lead.",
         phone_visible),
    Rule("local_seo.address_or_area", V, C.LOCAL_SEO, S.MEDIUM, "Address or service area",
         "the site states an address or the area it serves",
         "Show the address, or the cities and areas served when the business travels to customers.",
         "Local search and customers both need to know where the business works.",
         address_or_area),
    Rule("local_seo.local_business_schema", V, C.LOCAL_SEO, S.HIGH, "LocalBusiness structured data",
         "valid JSON-LD with a LocalBusiness type or subtype",
         "Add LocalBusiness JSON-LD (or a more specific type) with name, address, phone and hours.",
         "Structured data feeds local results, maps and assistants with the business facts.",
         local_business_schema),
    Rule("local_seo.schema_properties", V, C.LOCAL_SEO, S.MEDIUM, "Complete business data",
         "the LocalBusiness data carries name, address and telephone",
         "Complete the missing LocalBusiness properties.",
         "Incomplete data is ignored or shown without the details that bring calls.",
         schema_properties),
    Rule("local_seo.nap_consistency", V, C.LOCAL_SEO, S.MEDIUM, "Consistent phone",
         "the telephone in structured data is one the pages show",
         "Use the same phone number in structured data, on the pages and in listings.",
         "Inconsistent name, address and phone data weakens local rankings and misroutes calls.",
         nap_consistency),
    Rule("local_seo.hours", V, C.LOCAL_SEO, S.LOW, "Opening hours",
         "opening hours are stated on a page or in structured data",
         "Publish opening hours on the site and in structured data.",
         "People check the hours before they call or visit.",
         hours),
    Rule("local_seo.map_or_directions", V, C.LOCAL_SEO, S.LOW, "Map or directions",
         "a map or directions are offered",
         "Embed a map or link to directions on the contact page.",
         "Directions turn a visit to the site into a visit to the business.",
         map_or_directions),
    Rule("local_seo.locations", V, C.LOCAL_SEO, S.LOW, "Locations page",
         "a site that shows several addresses has a page for its locations",
         "Give each location its own page, or list every address on a locations page.",
         "Location pages let each branch rank for its own area.",
         locations),
    # content
    Rule("content.contact_page", V, C.CONTENT, S.MEDIUM, "Contact page",
         "a contact page is among the analyzed pages or linked from them",
         "Add a contact page with phone, email, a form and the address or area.",
         "It is where visitors who are ready to buy go first.",
         linked_page("contact", FAIL, "contact page")),
    Rule("content.about_page", V, C.CONTENT, S.MEDIUM, "About page",
         "an about page is among the analyzed pages or linked from them",
         "Add an about page with the team, the history and the credentials.",
         "Visitors check who is behind a business before they trust it.",
         linked_page("about", WARNING, "about page")),
    Rule("content.services_page", V, C.CONTENT, S.MEDIUM, "Services page",
         "a services page is among the analyzed pages or linked from them",
         "Describe every service on its own page or on a services page.",
         "Service pages match what people search for and explain the offer.",
         linked_page("services", WARNING, "services page")),
    Rule("content.privacy_policy", V, C.CONTENT, S.LOW, "Privacy policy",
         "a privacy policy is linked, above all where forms collect personal data",
         "Publish a privacy policy and link it from the footer and the forms.",
         "Forms without a privacy policy erode trust and may breach privacy law.",
         privacy_policy),
    Rule("content.home_content", V, C.CONTENT, S.MEDIUM, "Substantial home page content",
         "at least 250 words of main content on the home page (80 is a warning)",
         "Explain the services, the area, the credentials and the next step in text.",
         "Thin pages give visitors, search engines and assistants little to go on.",
         home_content),
    Rule("content.freshness", V, C.CONTENT, S.LOW, "Signs of maintenance",
         "the newest copyright year is the crawl's year or the year before",
         "Keep the footer year and the content current.",
         "An outdated site suggests the business may have closed.",
         freshness),
    Rule("content.open_graph", V, C.CONTENT, S.LOW, "Social sharing metadata",
         "og:title and og:image on the home page",
         "Add Open Graph title, description and image tags.",
         "Shared links then show a proper preview in social networks and messaging apps.",
         open_graph),
    Rule("content.favicon", V, C.CONTENT, S.LOW, "Favicon",
         "the home page declares an icon",
         "Add a favicon link.",
         "The icon identifies the business in tabs, bookmarks and some search results.",
         favicon),
    # conversion
    Rule("conversion.primary_cta", V, C.CONVERSION, S.HIGH, "Main call to action",
         "a call, quote, booking or buy action in the header or as a button on the home page",
         "Put one clear action (call, request a quote, book) at the top of the home page.",
         "Without a visible next step, interested visitors leave without contacting the business.",
         primary_cta),
    Rule("conversion.click_to_call", V, C.CONVERSION, S.HIGH, "Tap to call",
         "phone numbers are tel: links",
         "Wrap every phone number in a tel: link.",
         "Most local visits happen on phones: one tap turns a visit into a call.",
         click_to_call),
    Rule("conversion.lead_form", V, C.CONVERSION, S.MEDIUM, "Lead form",
         "a contact, quote or booking form, or a form tool, is available",
         "Add a short contact or quote request form.",
         "Forms capture leads after hours and from people who prefer not to call.",
         lead_form),
    Rule("conversion.form_friction", V, C.CONVERSION, S.LOW, "Short forms",
         "lead forms ask for at most 7 fields (12 is a warning)",
         "Ask only for what is needed to reply; leave the rest for the follow-up.",
         "Every extra field lowers the share of visitors who send the form.",
         form_friction),
    Rule("conversion.form_security", V, C.CONVERSION, S.MEDIUM, "Forms submit securely",
         "forms are on https pages and submit to https addresses",
         "Serve form pages over https and point form actions at https addresses.",
         "Browsers warn before sending data insecurely, and visitors abandon the form.",
         form_security),
    Rule("conversion.booking_or_quote", V, C.CONVERSION, S.LOW, "Online booking or quote",
         "a booking tool, or a quote or booking action or form, is offered",
         "Offer online booking or a quote request.",
         "Booking or asking for a price online captures leads without a call.",
         booking_or_quote),
    Rule("conversion.chat_or_messaging", V, C.CONVERSION, S.LOW, "Chat or messaging",
         "a chat widget or a messaging link (WhatsApp, SMS, Messenger) is detected",
         "Offer a messaging channel, such as a WhatsApp link.",
         "Many customers prefer writing to calling.",
         chat_or_messaging),
    Rule("conversion.trust_signals", V, C.CONVERSION, S.MEDIUM, "Trust signals",
         "two or more kinds: reviews, rating, licence, insurance, guarantee, portfolio, experience",
         "Show reviews, licences, insurance, guarantees and real work.",
         "Trust signals turn a comparison visit into a call.",
         trust_signals),
    Rule("conversion.contact_in_navigation", V, C.CONVERSION, S.LOW, "Contact in the navigation",
         "the header or navigation links to contact or offers a call button",
         "Add a contact link or a call button to the header.",
         "Visitors look for contact options at the top of the page.",
         contact_in_navigation),
    # mobile
    Rule("mobile.viewport", V, C.MOBILE, S.HIGH, "Mobile viewport",
         "a viewport meta tag with width=device-width",
         'Add <meta name="viewport" content="width=device-width, initial-scale=1">.',
         "Without it phones show a shrunken desktop page.",
         viewport),
    Rule("mobile.zoom", V, C.MOBILE, S.MEDIUM, "Zoom allowed",
         "the viewport does not disable zooming",
         "Remove user-scalable=no and maximum-scale=1 from the viewport.",
         "People who cannot zoom cannot read small text.",
         zoom),
    # accessibility
    Rule("accessibility.lang", V, C.ACCESSIBILITY, S.MEDIUM, "Page language declared",
         "the html element of the home page declares its language",
         'Add lang="en" or lang="es" to the html element.',
         "Screen readers and translators need the language to read the page correctly.",
         lang),
    Rule("accessibility.image_alt", V, C.ACCESSIBILITY, S.MEDIUM, "Text alternatives for images",
         "every image has an alt attribute (up to 10% missing is a warning)",
         'Describe meaningful images in alt text; give decorative ones alt="".',
         "Blind visitors and search engines cannot see images without alt text.",
         image_alt),
    Rule("accessibility.form_labels", V, C.ACCESSIBILITY, S.MEDIUM, "Labelled form fields",
         "every form field has a label",
         "Give each field a <label> or an aria-label.",
         "Unlabelled fields cannot be filled in with a screen reader.",
         form_labels),
    Rule("accessibility.link_names", V, C.ACCESSIBILITY, S.LOW, "Named links",
         "every link has text or an accessible name (up to 3 without is a warning)",
         "Give icon links an aria-label or visible text.",
         "Links without a name are announced as 'link' and cannot be understood.",
         link_names),
    Rule("accessibility.heading_order", V, C.ACCESSIBILITY, S.LOW, "Heading order",
         "no heading level is skipped on the home page",
         "Nest headings in order: H1, then H2, then H3.",
         "Screen reader users move through a page by its headings.",
         heading_order),
    # measurement
    Rule("measurement.analytics", V, C.MEASUREMENT, S.MEDIUM, "Analytics",
         "an analytics tag is detected on the analyzed pages",
         "Install analytics (Google Analytics 4 or another) to measure visits and leads.",
         "Without measurement nobody knows which channels bring customers.",
         analytics),
    Rule("measurement.tag_manager", V, C.MEASUREMENT, S.INFO, "Tag manager",
         "a tag manager is detected",
         "A tag manager is optional; it makes adding tracking easier.",
         "Informational: tags loaded inside a tag manager are invisible to this audit.",
         tag_manager),
    Rule("measurement.advertising", V, C.MEASUREMENT, S.INFO, "Advertising and call tracking",
         "advertising pixels or call tracking are detected",
         "If the business advertises, install the conversion tags of each platform.",
         "Informational: not detecting a pixel does not show that the business does not advertise.",
         advertising),
    # answer engines and AI assistants
    Rule("aeo_geo.content_without_javascript", V, C.AEO_GEO_READINESS, S.HIGH,
         "Content without JavaScript",
         "pages show their content in the HTML, without running JavaScript",
         "Render the main content on the server, or pre-render the pages.",
         "Many search and AI assistant crawlers do not run JavaScript and see an empty page.",
         content_without_javascript),
    Rule("aeo_geo.structured_data", V, C.AEO_GEO_READINESS, S.MEDIUM, "Valid structured data",
         "valid JSON-LD (or microdata) describing the business, its services or its FAQs",
         "Publish valid JSON-LD for the business, its services and FAQs; fix invalid blocks.",
         "Answer engines and assistants rely on structured facts to cite a business.",
         structured_data),
    Rule("aeo_geo.faq", V, C.AEO_GEO_READINESS, S.MEDIUM, "Questions and answers",
         "an FAQ page, FAQPage data or at least three question headings",
         "Answer the questions customers ask, in text, on an FAQ or on the service pages.",
         "Question-and-answer content is what answer engines quote.",
         faq),
    Rule("aeo_geo.entity_profiles", V, C.AEO_GEO_READINESS, S.LOW, "Linked profiles",
         "sameAs data or links to the business's profiles elsewhere",
         "Link the business's profiles (Google, Facebook, Yelp) and list them in sameAs.",
         "Assistants connect a site with the business through its profiles.",
         entity_profiles),
    Rule("aeo_geo.ai_crawlers", V, C.AEO_GEO_READINESS, S.MEDIUM, "AI crawlers allowed",
         "robots.txt does not block the crawlers of AI assistants",
         "Allow GPTBot, PerplexityBot, Google-Extended and others to appear in AI answers.",
         "Blocked crawlers cannot learn about the business; blocking may be a deliberate choice.",
         ai_crawlers),
)  # fmt: skip
DEFAULT_RULESET: Final = Ruleset(RULESET_VERSION, RULES)


# ---- the audit ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuditResult:
    status: WebsiteAuditStatus
    final_url: str | None
    pages_expected: int
    pages_analyzed: int
    coverage: float
    summary: WebsiteAuditSummary
    findings: tuple[WebsiteFindingView, ...]
    technologies: tuple[WebsiteTechnologyView, ...]
    conversion: tuple[WebsiteConversionView, ...]


def _finding(rule: Rule, outcome: Outcome) -> WebsiteFindingView:
    return WebsiteFindingView(
        rule_id=rule.id,
        rule_version=rule.version,
        title=rule.title,
        category=rule.category,
        result=outcome.result,
        severity=rule.severity,
        observed_value=clip(outcome.observed, 2000) if outcome.observed else None,
        expected_condition=rule.expected,
        remediation=rule.remediation,
        commercial_relevance=rule.relevance,
        confidence=round(outcome.confidence, 3),
        evidence=list(outcome.evidence),
    )


def subscores(findings: Sequence[WebsiteFindingView]) -> list[WebsiteSubscore]:
    """One per category, by `FORMULA`."""
    scores: list[WebsiteSubscore] = []
    for category in WebsiteCategory:
        mine = [finding for finding in findings if finding.category is category]
        counts = Counter(finding.result for finding in mine)
        evaluated = [finding for finding in mine if finding.result in CREDIT]
        weight = sum(WEIGHTS[finding.severity] for finding in evaluated)
        missing = sum(WEIGHTS[finding.severity] for finding in mine if finding.result is UNKNOWN)
        earned = sum(WEIGHTS[finding.severity] * CREDIT[finding.result] for finding in evaluated)
        trust = sum(WEIGHTS[finding.severity] * finding.confidence for finding in evaluated)
        scores.append(
            WebsiteSubscore(
                category=category,
                score=round(100 * earned / weight) if weight else None,
                coverage=round(weight / (weight + missing), 3) if weight + missing else 1.0,
                confidence=round(trust / weight, 3) if weight else 0.0,
                passed=counts[PASS],
                warnings=counts[WARNING],
                failed=counts[FAIL],
                unknown=counts[UNKNOWN],
                not_applicable=counts[NOT_APPLICABLE],
            )
        )
    return scores


def _page_view(page: PageInput) -> WebsitePageView:
    facts = page.facts
    return WebsitePageView(
        crawl_url_id=page.crawl_url_id,
        url=page.url,
        final_url=page.final_url,
        page_kind=page.page_kind,
        depth=page.depth,
        status=page.status,
        http_status=page.http_status,
        method=page.method,
        analyzed=facts is not None,
        gap=page.gap,
        title=facts.title if facts else None,
        words=facts.words if facts else None,
        redirects=len(page.redirects),
        response_ms=page.fetch_ms,
        render_ms=page.browser_ms,
        html_bytes=page.byte_size,
        scripts=facts.scripts if facts else None,
        images=facts.images if facts else None,
        device_profile=(
            "crawler_browser" if page.method is AcquisitionMethod.BROWSER_RENDER else "crawler_http"
        ),
        measured_at=page.measured_at,
    )


def _technologies(site: Site) -> tuple[WebsiteTechnologyView, ...]:
    return tuple(
        WebsiteTechnologyView(
            key=key,
            name=SIGNATURE_BY_ID[key].name,
            category=SIGNATURE_BY_ID[key].category,
            version=next((hit.version for _, hit in hits if hit.version), None),
            confidence=max(TRACE_CONFIDENCE[hit.field] for _, hit in hits),
            evidence=[ref(page, f"tech.{hit.field}", hit.snippet) for page, hit in hits[:3]],
        )
        for key, hits in sorted(site.tech.items())
    )


def _conversion(site: Site) -> tuple[WebsiteConversionView, ...]:
    found: list[WebsiteConversionView] = []
    seen: set[tuple[ConversionElementKind, str | None, str | None]] = set()

    def add(
        kind: ConversionElementKind, label: str | None, page: PageInput, target: str | None,
        confidence: float, field: str, snippet: object,
    ) -> None:  # fmt: skip
        if (kind, label, target) in seen or len(found) >= MAX_ELEMENTS:
            return
        seen.add((kind, label, target))
        found.append(
            WebsiteConversionView(
                kind=kind,
                label=label,
                page_url=page.address,
                target=target,
                confidence=confidence,
                evidence=[ref(page, field, snippet)],
            )
        )

    for page, facts in site.facts:
        for cta in facts.ctas:
            kind = (
                ConversionElementKind.PRIMARY_CTA if cta.primary
                else ConversionElementKind.SECONDARY_CTA
            )  # fmt: skip
            add(kind, f"{cta.kind}: {cta.text}", page, cta.target, 0.75, "cta", cta.text)
        for number in facts.tel_links:
            add(ConversionElementKind.CLICK_TO_CALL, number, page, f"tel:{number}", 0.95,
                "tel_link", number)  # fmt: skip
        for link in facts.messaging_links:
            add(ConversionElementKind.MESSAGING_LINK, None, page, link, 0.9, "messaging_link", link)
        for form in facts.forms:
            if form.purpose in (*LEAD_PURPOSES, "newsletter"):
                label = f"{form.purpose} form, {form.fields} fields"
                add(ConversionElementKind.CONTACT_FORM, label, page, form.action, 0.85, "form",
                    form.submit or form.purpose)  # fmt: skip
        for link in facts.map_links:
            add(ConversionElementKind.OTHER, "map", page, link, 0.9, "map", link)
    widgets = {
        "booking": ConversionElementKind.BOOKING_WIDGET,
        "chat": ConversionElementKind.CHAT_WIDGET,
    }
    for key, hits in sorted(site.tech.items()):
        signature = SIGNATURE_BY_ID[key]
        if (widget := widgets.get(signature.category)) is not None:
            page, hit = hits[0]
            confidence = TRACE_CONFIDENCE[hit.field]
            add(widget, signature.name, page, None, confidence, f"tech.{hit.field}", hit.snippet)
    return tuple(found)


def audit(data: SiteInput, ruleset: Ruleset = DEFAULT_RULESET) -> AuditResult:
    """Every rule of `ruleset` over one snapshot. Deterministic."""
    site = Site(data)
    findings = tuple(_finding(rule, rule.check(site)) for rule in ruleset.rules)
    evaluated = [finding for finding in findings if finding.result in CREDIT]
    unknowns = [finding for finding in findings if finding.result is UNKNOWN]
    weight = sum(WEIGHTS[finding.severity] for finding in evaluated)
    applicable = weight + sum(WEIGHTS[finding.severity] for finding in unknowns)
    analyzed = [page for page, _ in site.facts]
    home_analyzed = site.home_facts is not None
    if not analyzed:
        status = WebsiteAuditStatus.EMPTY
    elif (
        len(analyzed) < len(site.pages)
        or not home_analyzed
        or data.crawl_status is not SourceRunStatus.SUCCEEDED
    ):
        status = WebsiteAuditStatus.PARTIAL
    else:
        status = WebsiteAuditStatus.COMPLETE
    coverage = WebsiteCoverage(
        pages_expected=len(site.pages),
        pages_analyzed=len(analyzed),
        pages_failed=sum(1 for page in site.pages if page.status is FAILED),
        gaps=sorted({page.gap for page in site.pages if page.gap}),
        home_analyzed=home_analyzed,
        crawl_status=data.crawl_status,
        limits_hit=list(data.limits_hit),
        rules=len(findings),
        rules_evaluated=len(evaluated),
        rules_unknown=len(unknowns),
        rules_not_applicable=sum(1 for finding in findings if finding.result is NOT_APPLICABLE),
    )
    notes = [*NOTES]
    if any(page.method is AcquisitionMethod.BROWSER_RENDER for page in analyzed):
        notes.append("rendered_pages")
    if data.limits_hit:
        notes.append("crawl_limits_reached")
    summary = WebsiteAuditSummary(
        formula=FORMULA,
        subscores=subscores(findings),
        coverage=coverage,
        pages=[_page_view(page) for page in data.pages],
        languages=sorted({facts.language for _, facts in site.facts if facts.language}),
        lab_tool=f"crawler {data.crawler_version}: one request per page, no device or network "
        "emulation",
        notes=notes,
    )
    return AuditResult(
        status=status,
        final_url=site.home.final_url if site.home is not None else None,
        pages_expected=len(site.pages),
        pages_analyzed=len(analyzed),
        coverage=round(weight / applicable, 3) if applicable else 1.0,
        summary=summary,
        findings=findings,
        technologies=_technologies(site),
        conversion=_conversion(site),
    )


# ---- comparison --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Comparable:
    """What a diff compares of one audit."""

    audit_id: str
    created_at: datetime
    ruleset_version: str
    final_url: str | None
    summary: WebsiteAuditSummary
    results: Mapping[str, WebsiteFindingResult]
    technologies: frozenset[str]


def diff(before: Comparable, after: Comparable) -> WebsiteDiff:
    old = {score.category: score.score for score in before.summary.subscores}
    new = {score.category: score.score for score in after.summary.subscores}
    old_pages = {page.final_url or page.url for page in before.summary.pages if page.analyzed}
    new_pages = {page.final_url or page.url for page in after.summary.pages if page.analyzed}
    rule_ids = sorted(set(before.results) | set(after.results))
    return WebsiteDiff(
        previous_audit_id=before.audit_id,
        previous_created_at=before.created_at,
        ruleset_changed=before.ruleset_version != after.ruleset_version,
        final_url_before=before.final_url,
        final_url_after=after.final_url,
        domain_changed=before.final_url is not None
        and after.final_url is not None
        and site_of(before.final_url) != site_of(after.final_url),
        subscores=[
            WebsiteScoreChange(category=category, before=old.get(category), after=new.get(category))
            for category in WebsiteCategory
            if old.get(category) != new.get(category)
        ],
        findings=[
            WebsiteFindingChange(
                rule_id=rule_id,
                before=before.results.get(rule_id),
                after=after.results.get(rule_id),
            )
            for rule_id in rule_ids
            if before.results.get(rule_id) != after.results.get(rule_id)
        ],
        technologies_added=sorted(after.technologies - before.technologies),
        technologies_removed=sorted(before.technologies - after.technologies),
        pages_added=sorted(new_pages - old_pages)[:MAX_DIFF_PAGES],
        pages_removed=sorted(old_pages - new_pages)[:MAX_DIFF_PAGES],
    )
