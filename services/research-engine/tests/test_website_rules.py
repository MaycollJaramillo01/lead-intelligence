"""Website intelligence rules over controlled snapshots (phase 08): what each fixture site shows,
what a partial crawl leaves unknown, what is never concluded from an absence, determinism and the
difference between two audits. Pure: no store, no network. Synthetic test data only."""

import zlib
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import orjson

from research_engine.domain import (
    AcquisitionMethod,
    ConversionElementKind,
    CrawlUrlStatus,
    PageKind,
    SourceRunStatus,
    WebsiteAuditStatus,
    WebsiteCategory,
    WebsiteFindingChange,
    WebsiteFindingResult,
    WebsiteFindingSeverity,
    WebsiteFindingView,
)
from research_engine.testdata import synthetic_complete_site
from research_engine.website import extract, rules

SITE = "synthetic-shop-0701.example"
NOW = datetime(2026, 9, 1, tzinfo=UTC)
DOCS = Path(__file__).resolve().parents[3] / "docs" / "website-intelligence.md"
PASS, WARNING, FAIL = (
    WebsiteFindingResult.PASS,
    WebsiteFindingResult.WARNING,
    WebsiteFindingResult.FAIL,
)
UNKNOWN, NOT_APPLICABLE = WebsiteFindingResult.UNKNOWN, WebsiteFindingResult.NOT_APPLICABLE
ALLOW_ALL = "User-agent: *\nDisallow: /admin/\n"
ENGLISH = " ".join(
    ["We are the team that fixes the bikes of the people in this city, and we are here for all "
     "of you with a smile."] * 4
)  # fmt: skip
SPANISH = " ".join(
    ["Somos el equipo que arregla las bicicletas de la gente de la ciudad y estamos aqui para "
     "todos los vecinos con una sonrisa."] * 4
)  # fmt: skip


def crawl_id(key: str) -> str:
    return f"00000000-0000-4000-8000-{zlib.crc32(key.encode()):012d}"


def document(
    *, title: str = "Synthetic Shop 0701 | Bike repair in Austin", lang: str = "en", head: str = "",
    body: str = "", viewport: str = "width=device-width, initial-scale=1",
) -> str:  # fmt: skip
    return (
        f"<!doctype html><html lang='{lang}'><head><title>{title}</title>"
        f"<meta name='viewport' content='{viewport}'>{head}</head><body>{body}</body></html>"
    )


def page(
    path: str,
    html: str | None,
    *,
    status: CrawlUrlStatus = CrawlUrlStatus.FETCHED,
    method: AcquisitionMethod = AcquisitionMethod.HTTP_FETCH,
    fallback: str | None = None,
    redirects: tuple[rules.Hop, ...] = (),
    final_url: str | None = None,
    http_status: int = 200,
    reason: str | None = None,
) -> rules.PageInput:
    url = f"https://{SITE}{path}"
    final = final_url or url
    fetched = status is CrawlUrlStatus.FETCHED
    facts = extract.page_facts(html, url, final) if html is not None and fetched else None
    gap = None if facts is not None else ("artifact_missing" if fetched else "fetch_failed")
    return rules.PageInput(
        crawl_url_id=crawl_id(path), url=url, final_url=final if fetched else None,
        page_kind=PageKind.HOME if path == "/" else PageKind.OTHER, depth=0 if path == "/" else 1,
        status=status, reason=reason, http_status=http_status, method=method,
        fallback_reason=fallback, redirects=redirects, headers=(("content-encoding", "br"),),
        fetch_ms=180, browser_ms=900 if method is AcquisitionMethod.BROWSER_RENDER else None,
        byte_size=len(html or ""), duplicate_of_id=None, measured_at=NOW, gap=gap, facts=facts,
    )  # fmt: skip


def snapshot(
    *pages: rules.PageInput,
    robots: str | None = ALLOW_ALL,
    crawl_status: SourceRunStatus = SourceRunStatus.SUCCEEDED,
) -> rules.SiteInput:
    base = f"https://{SITE}"
    return rules.SiteInput(
        site=SITE,
        root_url=f"{base}/",
        crawl_status=crawl_status,
        crawler_version="1.0.0",
        crawled_at=NOW,
        extractor_version=extract.EXTRACTOR_VERSION,
        limits_hit=(),
        robots=None
        if robots is None
        else rules.RobotsInput(
            f"{base}/robots.txt", crawl_id("robots"), CrawlUrlStatus.FETCHED, None, robots
        ),
        sitemaps=(
            rules.SitemapInput(
                f"{base}/sitemap.xml", crawl_id("sitemap"), CrawlUrlStatus.FETCHED, None, 12
            ),
        ),
        pages=pages,
    )


def audit(data: rules.SiteInput) -> tuple[rules.AuditResult, dict[str, WebsiteFindingResult]]:
    result = rules.audit(data)
    return result, {finding.rule_id: finding.result for finding in result.findings}


def finding(result: rules.AuditResult, rule_id: str) -> WebsiteFindingView:
    return next(item for item in result.findings if item.rule_id == rule_id)


def complete_site() -> rules.SiteInput:
    pages = synthetic_complete_site(SITE, NOW.year)
    return snapshot(*(page(path, html) for path, html in pages.items()))


# ---- the fixture sites ---------------------------------------------------------------------------


def test_a_complete_site_passes_what_its_pages_show() -> None:
    result, found = audit(complete_site())
    assert result.status is WebsiteAuditStatus.COMPLETE
    assert (result.pages_expected, result.pages_analyzed) == (7, 7)
    assert [rule for rule, outcome in found.items() if outcome is FAIL] == []
    for rule_id in (
        "technical.https", "technical.broken_links", "seo.indexable", "seo.title", "seo.sitemap",
        "seo.hreflang", "local_seo.local_business_schema", "local_seo.nap_consistency",
        "local_seo.hours", "content.contact_page", "content.privacy_policy", "content.freshness",
        "conversion.primary_cta", "conversion.click_to_call", "conversion.lead_form",
        "conversion.trust_signals", "mobile.viewport", "accessibility.form_labels",
        "measurement.analytics", "aeo_geo.faq", "aeo_geo.entity_profiles", "aeo_geo.ai_crawlers",
    ):  # fmt: skip
        assert found[rule_id] is PASS, rule_id
    # every page repeats the same description: a finding, not a failure
    assert found["seo.duplicate_metadata"] is WARNING
    assert {technology.key for technology in result.technologies} == {
        "google_analytics_4",
        "google_maps",
    }
    kinds = {element.kind for element in result.conversion}
    assert {ConversionElementKind.PRIMARY_CTA, ConversionElementKind.CLICK_TO_CALL,
            ConversionElementKind.CONTACT_FORM} <= kinds  # fmt: skip
    assert result.summary.languages == ["en", "es"]
    assert all(score.score is not None for score in result.summary.subscores)
    call = finding(result, "conversion.click_to_call")
    assert call.evidence[0].url == f"https://{SITE}/"
    assert call.evidence[0].snippet == "+15125550161"
    assert call.remediation
    assert call.expected_condition
    assert call.commercial_relevance


def test_a_site_without_a_call_to_action_or_a_form() -> None:
    home = document(
        body="<h1>Synthetic Cleaning 0702</h1><p>Call (512) 555-0172 for house cleaning. "
        "Synthetic test data.</p>"
    )
    result, found = audit(snapshot(page("/", home)))
    assert found["conversion.primary_cta"] is FAIL
    assert found["conversion.lead_form"] is FAIL
    assert found["conversion.click_to_call"] is FAIL
    assert "plain text" in (finding(result, "conversion.click_to_call").observed_value or "")
    assert found["local_seo.phone_visible"] is PASS
    assert found["conversion.form_friction"] is NOT_APPLICABLE
    assert found["conversion.form_security"] is NOT_APPLICABLE
    assert found["accessibility.form_labels"] is NOT_APPLICABLE


def test_a_rendered_single_page_app_is_judged_on_both_htmls() -> None:
    rendered = document(
        body="<main><h1>Synthetic Tutoring 0703</h1><p>Lessons for every level.</p>"
        "<a class='btn' href='/book'>Book now</a></main>"
    )
    result, found = audit(
        snapshot(
            page("/", rendered, method=AcquisitionMethod.BROWSER_RENDER, fallback="empty_shell")
        )
    )
    assert found["aeo_geo.content_without_javascript"] is FAIL
    assert found["conversion.primary_cta"] is PASS, "read from the rendered page"
    (view,) = result.summary.pages
    assert (view.device_profile, view.render_ms) == ("crawler_browser", 900)
    assert "rendered_pages" in result.summary.notes

    forced = snapshot(
        page("/", rendered, method=AcquisitionMethod.BROWSER_RENDER, fallback="configured")
    )
    assert audit(forced)[1]["aeo_geo.content_without_javascript"] is UNKNOWN


def test_invalid_structured_data_is_reported_with_its_fragment() -> None:
    broken = '<script type="application/ld+json">{"@type": "Plumber", "name": </script>'
    result, found = audit(snapshot(page("/", document(head=broken))))
    assert found["local_seo.local_business_schema"] is FAIL
    assert found["aeo_geo.structured_data"] is FAIL
    assert found["local_seo.schema_properties"] is NOT_APPLICABLE
    snippet = finding(result, "aeo_geo.structured_data").evidence[0].snippet or ""
    assert '"@type": "Plumber"' in snippet

    valid = '<script type="application/ld+json">{"@type": "Plumber", "name": "S"}</script>'
    _, both = audit(snapshot(page("/", document(head=valid + broken))))
    assert both["local_seo.local_business_schema"] is PASS
    assert both["aeo_geo.structured_data"] is WARNING
    assert both["local_seo.schema_properties"] is WARNING


def test_redirect_chains_domain_changes_and_mixed_content() -> None:
    body = (
        f"<img src='http://{SITE}/a.png' alt='a'><form action='http://{SITE}/send' method='post'>"
        "<label for='m'>Message</label><textarea id='m' name='message'></textarea>"
        "<button>Send us a message</button></form>"
    )
    hops = (
        rules.Hop(f"http://{SITE}/", 301, f"https://{SITE}/"),
        rules.Hop(f"https://{SITE}/", 302, f"https://www.{SITE}/"),
        rules.Hop(f"https://www.{SITE}/", 302, f"https://www.{SITE}/home"),
    )
    home = page("/", document(body=body), redirects=hops, final_url=f"https://www.{SITE}/home")
    result, found = audit(snapshot(home))
    assert found["technical.redirect_chain"] is FAIL
    assert (finding(result, "technical.redirect_chain").observed_value or "").startswith(
        "3 redirects"
    )
    assert found["technical.domain_redirect"] is PASS, "www and https stay on the site"
    assert found["technical.mixed_content"] is FAIL
    assert found["conversion.form_security"] is FAIL

    two = page("/", document(body=body), redirects=hops[:2], final_url=f"https://www.{SITE}/")
    assert audit(snapshot(two))[1]["technical.redirect_chain"] is WARNING
    moved = page(
        "/", document(), redirects=hops[:1], final_url="https://synthetic-new-0701.example/"
    )
    moved_result, moved_found = audit(snapshot(moved))
    assert moved_found["technical.domain_redirect"] is WARNING
    assert "synthetic-new-0701.example" in (
        finding(moved_result, "technical.domain_redirect").observed_value or ""
    )


def test_multiple_locations_and_two_languages() -> None:
    offices = (
        "<p>Visit 12 Example Ave, Austin, TX 78701 or 88 Sample Mill Rd, Denver, CO 80202.</p>"
    )
    english = document(body=f"<main><h1>Our offices</h1>{offices}<p>{ENGLISH}</p></main>")
    spanish = document(lang="es", body=f"<main><h1>Oficinas</h1><p>{SPANISH}</p></main>")
    result, found = audit(snapshot(page("/", english), page("/es/", spanish)))
    assert result.summary.languages == ["en", "es"]
    assert found["local_seo.locations"] is WARNING
    assert found["seo.hreflang"] is WARNING

    alternates = (
        f"<link rel='alternate' hreflang='en' href='https://{SITE}/'>"
        f"<link rel='alternate' hreflang='es' href='https://{SITE}/es/'>"
    )
    linked = document(
        head=alternates,
        body=f"<main><h1>Our offices</h1>{offices}<a href='/locations'>Our locations</a>"
        f"<p>{ENGLISH}</p></main>",
    )
    _, fixed = audit(snapshot(page("/", linked), page("/es/", spanish)))
    assert fixed["local_seo.locations"] is PASS
    assert fixed["seo.hreflang"] is PASS
    _, single = audit(snapshot(page("/", document(body=f"<p>{ENGLISH}</p>"))))
    assert single["local_seo.locations"] is NOT_APPLICABLE
    assert single["seo.hreflang"] is NOT_APPLICABLE


def test_tracking_is_detected_or_left_unknown_never_denied() -> None:
    ga4 = "<script async src='https://www.googletagmanager.com/gtag/js?id=G-TEST0701'></script>"
    result, found = audit(snapshot(page("/", document(head=ga4))))
    assert found["measurement.analytics"] is PASS
    (technology,) = result.technologies
    assert (technology.key, technology.confidence) == ("google_analytics_4", 0.95)
    assert technology.evidence[0].field == "tech.script"

    gtm = "<script>(function(w){w.dataLayer=[]})(window,'GTM-TEST0701');</script>"
    _, managed = audit(snapshot(page("/", document(head=gtm))))
    assert managed["measurement.tag_manager"] is PASS
    assert managed["measurement.analytics"] is UNKNOWN, "analytics may load inside the tag manager"
    assert managed["conversion.chat_or_messaging"] is UNKNOWN

    bare, nothing = audit(snapshot(page("/", document())))
    assert nothing["measurement.analytics"] is WARNING
    assert finding(bare, "measurement.analytics").confidence == 0.6
    assert nothing["measurement.advertising"] is UNKNOWN
    assert "does not show" in (finding(bare, "measurement.advertising").observed_value or "")
    assert all(
        item.result is not FAIL
        for item in bare.findings
        if item.category is WebsiteCategory.MEASUREMENT
    )
    pixel = "<script>fbq('init', '000000000000000');</script>"
    assert audit(snapshot(page("/", document(head=pixel))))[1]["measurement.advertising"] is PASS


def test_ai_crawlers_and_search_engines_read_robots_txt() -> None:
    home = page("/", document())
    blocked = "User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    result, found = audit(snapshot(home, robots=blocked))
    assert found["aeo_geo.ai_crawlers"] is WARNING
    assert "gptbot" in (finding(result, "aeo_geo.ai_crawlers").observed_value or "")
    assert found["seo.indexable"] is PASS
    _, closed = audit(snapshot(home, robots="User-agent: *\nDisallow: /\n"))
    assert closed["seo.indexable"] is FAIL
    noindex = page("/", document(head="<meta name='robots' content='noindex, follow'>"))
    assert audit(snapshot(noindex))[1]["seo.indexable"] is FAIL


# ---- coverage and scores -------------------------------------------------------------------------


def test_missing_pages_make_a_partial_audit_and_a_missing_home_an_empty_one() -> None:
    pages = synthetic_complete_site(SITE, NOW.year)
    data = snapshot(
        page("/", pages["/"]),
        page("/about", None, status=CrawlUrlStatus.FAILED, http_status=500, reason="server_error"),
        page("/services", None),
    )
    result, found = audit(data)
    assert result.status is WebsiteAuditStatus.PARTIAL
    assert (result.pages_expected, result.pages_analyzed) == (3, 1)
    assert result.summary.coverage.gaps == ["artifact_missing", "fetch_failed"]
    assert result.summary.coverage.pages_failed == 1
    assert found["technical.page_errors"] is FAIL
    assert found["seo.title"] is PASS

    empty, missing = audit(snapshot(page("/", None)))
    assert empty.status is WebsiteAuditStatus.EMPTY
    assert missing["seo.title"] is UNKNOWN
    assert missing["conversion.primary_cta"] is UNKNOWN
    assert empty.coverage < result.coverage
    seo = next(score for score in empty.summary.subscores if score.category is WebsiteCategory.SEO)
    assert seo.unknown > 0
    assert seo.coverage < 1

    cancelled, _ = audit(snapshot(page("/", pages["/"]), crawl_status=SourceRunStatus.CANCELLED))
    assert cancelled.status is WebsiteAuditStatus.PARTIAL


def _view(result: WebsiteFindingResult, severity: WebsiteFindingSeverity) -> WebsiteFindingView:
    return WebsiteFindingView(
        rule_id=f"seo.{result.value}_{severity.value}", rule_version="1.0.0", title="Synthetic",
        category=WebsiteCategory.SEO, result=result, severity=severity, observed_value=None,
        expected_condition="synthetic", remediation=None, commercial_relevance=None,
        confidence=0.0 if result is UNKNOWN else 1.0, evidence=[],
    )  # fmt: skip


def test_unknown_and_not_applicable_never_lower_a_score() -> None:
    def seo(*findings: WebsiteFindingView) -> tuple[int | None, float]:
        score = next(s for s in rules.subscores(findings) if s.category is WebsiteCategory.SEO)
        return score.score, score.coverage

    high = WebsiteFindingSeverity.HIGH
    critical = WebsiteFindingSeverity.CRITICAL
    assert seo(_view(PASS, high), _view(UNKNOWN, critical)) == (100, 0.375)
    assert seo(_view(PASS, high), _view(NOT_APPLICABLE, critical)) == (100, 1.0)
    assert seo(_view(PASS, high), _view(FAIL, critical)) == (38, 1.0)
    assert seo(_view(PASS, high), _view(WARNING, critical)) == (69, 1.0)
    assert seo(_view(UNKNOWN, critical)) == (None, 0.0)
    assert seo(_view(PASS, WebsiteFindingSeverity.INFO)) == (None, 1.0), "info carries no weight"

    _, with_robots = audit(snapshot(page("/", document())))
    _, without = audit(snapshot(page("/", document()), robots=None))
    assert (with_robots["seo.robots_txt"], without["seo.robots_txt"]) == (PASS, UNKNOWN)
    assert without["aeo_geo.ai_crawlers"] is UNKNOWN
    assert audit(snapshot(page("/", document())))[1]["accessibility.image_alt"] is NOT_APPLICABLE


# ---- history -------------------------------------------------------------------------------------


def _comparable(result: rules.AuditResult, audit_id: str) -> rules.Comparable:
    return rules.Comparable(
        audit_id=audit_id,
        created_at=NOW,
        ruleset_version=rules.RULESET_VERSION,
        final_url=result.final_url,
        summary=result.summary,
        results={item.rule_id: item.result for item in result.findings},
        technologies=frozenset(technology.key for technology in result.technologies),
    )


def test_the_difference_between_two_snapshots() -> None:
    weak = document(body="<h1>Synthetic Shop 0701</h1><p>Call (512) 555-0171.</p>")
    improved = document(
        head="<script async src='https://www.googletagmanager.com/gtag/js?id=G-TEST0701'></script>",
        body="<h1>Synthetic Shop 0701</h1><a href='tel:+15125550171'>(512) 555-0171</a>"
        "<a href='/contact'>Contact</a>",
    )
    contact = document(title="Contact | Synthetic Shop 0701", body="<h1>Contact</h1>")
    before = rules.audit(snapshot(page("/", weak)))
    after = rules.audit(snapshot(page("/", improved), page("/contact", contact)))
    change = rules.diff(
        _comparable(before, crawl_id("before")), _comparable(after, crawl_id("after"))
    )
    assert change.technologies_added == ["google_analytics_4"]
    assert change.technologies_removed == []
    assert WebsiteFindingChange(rule_id="conversion.click_to_call", before=FAIL, after=PASS) in (
        change.findings
    )
    assert change.pages_added == [f"https://{SITE}/contact"]
    assert not change.ruleset_changed
    assert not change.domain_changed
    assert all(item.before != item.after for item in change.findings)
    assert any(score.category is WebsiteCategory.MEASUREMENT for score in change.subscores)

    moved = rules.audit(snapshot(page("/", weak, final_url="https://synthetic-new-0701.example/")))
    assert rules.diff(
        _comparable(before, crawl_id("a")), _comparable(moved, crawl_id("b"))
    ).domain_changed


def test_audits_are_deterministic_and_reproducible_from_their_snapshot() -> None:
    data = complete_site()
    first = rules.audit(data)
    assert rules.audit(data) == first
    stored = orjson.loads(orjson.dumps(rules.SNAPSHOT.dump_python(data, mode="json")))
    assert rules.audit(rules.SNAPSHOT.validate_python(stored)) == first
    html = synthetic_complete_site(SITE, NOW.year)["/contact"]
    url = f"https://{SITE}/contact"
    assert extract.page_facts(html, url, url) == extract.page_facts(html, url, url)


def test_rules_are_versioned_and_every_rule_is_documented() -> None:
    ids = [rule.id for rule in rules.RULES]
    assert len(ids) == len(set(ids))
    assert {rule.category for rule in rules.RULES} == set(WebsiteCategory)
    assert {rule.version for rule in rules.RULES} == {rules.RULESET_VERSION}
    assert all(rule.id.split(".")[0] in rule.category.value for rule in rules.RULES)
    text = DOCS.read_text(encoding="utf-8")
    assert [rule.id for rule in rules.RULES if f"`{rule.id}`" not in text] == []
    newer = rules.Ruleset("1.1.0", tuple(replace(rule, version="1.1.0") for rule in rules.RULES))
    assert {item.rule_version for item in rules.audit(complete_site(), newer).findings} == {"1.1.0"}


# ---- the extractor -------------------------------------------------------------------------------


def test_the_extractor_reads_what_a_page_shows() -> None:
    html = document(
        head="<meta name='generator' content='WordPress 6.4.2'>",
        viewport="width=device-width, maximum-scale=1.5",
        body="<header><nav><a href='/contact'>Contact</a> <a href='/'><img src='/logo.png'></a>"
        "<a href='tel:5125550173'>Call now</a></nav></header>"
        "<h2>How do I book?</h2><h4>Skipped a level</h4>"
        "<form><input type='password' name='p'></form>"
        "<form><input type='search' name='q'></form>"
        "<form><input type='email' name='email' placeholder='Subscribe'></form>"
        "<form><label for='n'>Name</label><input id='n' name='name'>"
        "<input name='phone' placeholder='Phone'><button>Get a free quote</button></form>"
        "<p>Write to hola@synthetic-shop-0701.example. Mon-Fri 9am-5pm. "
        "\N{COPYRIGHT SIGN} 2019\N{EN DASH}2025 Synthetic Shop 0701</p>",
    )
    url = f"https://{SITE}/"
    facts = extract.page_facts(html, url, url)
    (wordpress,) = [item for item in facts.technologies if item.id == "wordpress"]
    assert (wordpress.field, wordpress.version) == ("meta", "6.4.2")
    assert not facts.zoom_disabled
    assert [form.purpose for form in facts.forms] == ["login", "search", "newsletter", "quote"]
    assert facts.forms[3].unlabeled == 1
    primary = {cta.text: cta.primary for cta in facts.ctas}
    assert primary == {"Call now": True, "Get a free quote": True}
    assert facts.tel_links == ("5125550173",)
    assert facts.emails == ("hola@synthetic-shop-0701.example",)
    assert facts.copyright_year == 2025
    assert facts.hours is not None
    assert (facts.question_headings, facts.heading_skips, facts.empty_links) == (1, 1, 1)
    assert facts.images_missing_alt == 1
    blocked = document(viewport="width=device-width, user-scalable=no")
    assert extract.page_facts(blocked, url, url).zoom_disabled
