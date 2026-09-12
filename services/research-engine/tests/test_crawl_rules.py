"""The rules of the crawler, without a network: URLs, robots.txt, parsing, fallback and SSRF.

`test_crawl.py` runs the same rules end to end against a local fixture site.
"""

import gzip
from datetime import UTC, datetime

import pytest

from research_engine.crawl import guard
from research_engine.crawl.engine import GIB, robots_policy, slots_for
from research_engine.crawl.extract import (
    content_hash,
    decode,
    fallback_reason,
    gunzip,
    is_challenge,
    looks_binary,
    sitemap_locations,
    snapshot,
)
from research_engine.crawl.fetch import retry_after
from research_engine.crawl.robots import ALLOW_ALL, parse
from research_engine.crawl.urls import canonical, in_scope, page_kind, priority, skip_reason
from research_engine.domain import PageKind, default_crawl_settings

pytestmark = pytest.mark.anyio


# ---- URLs ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("HTTPS://WWW.Example.com:443/a/./b/../c", "https://www.example.com/a/c"),
        ("http://example.com:80", "http://example.com/"),
        ("http://example.com:8080/x", "http://example.com:8080/x"),
        ("https://example.com/p?utm_source=x&b=2&gclid=1&a=1#top", "https://example.com/p?a=1&b=2"),
        ("https://example.com/caf%c3%a9 menu", "https://example.com/caf%C3%A9%20menu"),
        ("https://münchen.example/ä", "https://xn--mnchen-3ya.example/%C3%A4"),
        ("javascript:alert(1)", None),
        ("mailto:hola@example.com", None),
        ("ftp://example.com/file", None),
        ("https://user:secret@example.com/", None),
        ("https://example.com:99999/", None),
        ("   ", None),
    ],
)
def test_canonical_urls_keep_only_what_changes_the_answer(given: str, expected: str | None) -> None:
    assert canonical(given) == expected


def test_relative_links_resolve_against_the_page() -> None:
    base = "https://example.com/about/team"
    assert canonical("../contact", base) == "https://example.com/contact"
    assert canonical("//cdn.example.net/x.js", base) == "https://cdn.example.net/x.js"
    assert canonical("?page=2", base) == "https://example.com/about/team?page=2"


def test_scope_and_page_kinds() -> None:
    assert in_scope("https://www.example.com/x", "example.com", include_subdomains=False)
    assert not in_scope("https://shop.example.com/x", "example.com", include_subdomains=False)
    assert in_scope("https://shop.example.com/x", "example.com", include_subdomains=True)
    assert not in_scope("https://example.com.evil.net/", "example.com", include_subdomains=True)
    assert page_kind("https://example.com/") is PageKind.HOME
    assert page_kind("https://example.com/quienes-somos/") is PageKind.ABOUT
    assert page_kind("https://example.com/x", "Contáctenos") is PageKind.CONTACT
    assert page_kind("https://example.com/our-services") is PageKind.SERVICES
    assert page_kind("https://example.com/blog/post") is PageKind.OTHER
    assert priority(PageKind.CONTACT, 1) > priority(PageKind.OTHER, 1)
    assert priority(PageKind.HOME, 0) > priority(PageKind.HOME, 2)


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("/contact", None),
        ("/brochure.pdf", "binary_resource"),
        ("/wp-login.php", "private_area"),
        ("/cart", "private_area"),
        ("/mi-cuenta/pedidos", "private_area"),
        ("/events/calendar?month=2026-09", "trap"),
        ("/2024/01/01/news", "trap"),
        ("/shop?color=red&size=m&sort=price&page=3", "trap"),
        ("/a/b/a/b/a/b", "trap"),
        ("/team", "excluded"),
        ("/menu", "not_included"),
    ],
)
def test_traps_private_areas_binaries_and_path_rules(path: str, reason: str | None) -> None:
    settings = default_crawl_settings().model_copy(
        update={"exclude_paths": ["/team"], "include_paths": ["/contact", "/team", "/blog/*"]}
    )
    if reason is None or reason in ("excluded", "not_included"):
        assert skip_reason(f"https://example.com{path}", settings) == reason
    else:
        assert skip_reason(f"https://example.com{path}", default_crawl_settings()) == reason


# ---- robots.txt ----------------------------------------------------------------------------------

ROBOTS = """
# comments and blank lines are ignored
User-agent: *
Allow: /
Disallow: /admin
Disallow: /*.pdf$
Allow: /admin/public

User-agent: LeadIntelligence
Disallow: /private/
Crawl-delay: 120

User-agent: OtherBot
Disallow: /

Sitemap: https://example.com/sitemap_index.xml
"""


def test_robots_follow_rfc_9309_longest_match() -> None:
    generic = parse(ROBOTS, "SomeOtherCrawler")
    assert not generic.allowed("/admin/users"), "Allow: / must not win over a longer Disallow"
    assert generic.allowed("/admin/public/page"), "the longest match wins"
    assert not generic.allowed("/files/menu.pdf")
    assert generic.allowed("/files/menu.pdf?download=1"), "$ anchors the end"
    assert generic.allowed("/robots.txt")

    mine = parse(ROBOTS, "LeadIntelligence")
    assert not mine.allowed("/private/area")
    assert mine.allowed("/admin"), "a group for our token replaces the * group"
    assert mine.crawl_delay_s == 30.0, "crawl delay is honoured, within a cap"
    assert mine.sitemaps == ("https://example.com/sitemap_index.xml",)

    tie = parse("User-agent: *\nDisallow: /page\nAllow: /page\n", "LeadIntelligence")
    assert tie.allowed("/page"), "Allow wins a tie"
    assert parse("", "LeadIntelligence") == ALLOW_ALL


def test_robots_fetch_outcomes_follow_rfc_9309() -> None:
    readable, stop = robots_policy(None, "User-agent: *\nDisallow: /\n")
    assert (readable.allowed("/x"), stop) == (False, None)
    for absent in ("not_found", "blocked", "http_error"):
        assert robots_policy(absent, None) == (ALLOW_ALL, None), absent
    for unreachable in ("server_error", "rate_limited", "timeout", "network_error"):
        robots, stop = robots_policy(unreachable, None)
        assert (robots.allowed("/"), stop) == (False, "robots_unreachable"), unreachable
    assert robots_policy("unsafe_target", None)[1] == "unsafe_target"


# ---- bytes to text -------------------------------------------------------------------------------


def test_charsets_are_detected_in_order() -> None:
    cafe = "Panadería El Café".encode("cp1252")
    assert decode(b"\xef\xbb\xbfhola", None) == ("hola", "utf-8-sig")
    assert decode("日本の店".encode("shift_jis"), "text/html; charset=Shift_JIS")[0] == "日本の店"
    meta = b'<meta http-equiv="Content-Type" content="text/html; charset=windows-1252">' + cafe
    assert decode(meta, "text/html")[0].endswith("Panadería El Café")
    assert decode(b"<meta charset=latin1>" + cafe, None)[1] == "cp1252"
    assert decode(cafe, "text/html; charset=bogus-label") == ("Panadería El Café", "cp1252")
    assert decode("señal".encode(), None) == ("señal", "utf-8")


def test_binary_bodies_and_decompression_bombs_are_recognised() -> None:
    assert looks_binary(b"%PDF-1.7 ...")
    assert looks_binary(b"MZ\x90\x00")
    assert not looks_binary(b"<!doctype html><html>")
    bomb = gzip.compress(b"<url>" * 1_000_000)
    with pytest.raises(ValueError, match="size limit"):
        gunzip(bomb, 1_000_000)
    assert gunzip(gzip.compress(b"<urlset/>"), 1_000) == b"<urlset/>"


PAGE = """<!doctype html><html lang="es"><head><title>Panadería El Sol</title>
<meta name="description" content="Pan fresco en Austin">
<link rel="canonical" href="/">
<script type="application/ld+json">{"@type": "Bakery", "telephone": "+1-512-555-0101"}</script>
<script type="application/ld+json">{broken</script></head>
<body><nav><a href="/about-us?utm_source=nav">Quiénes somos</a>
<a href="mailto:hola@sol.example">Email</a> <a href="tel:+15125550101">Call</a>
<a href="https://facebook.example/sol" rel="nofollow">FB</a></nav>
<main><h1>Bienvenidos</h1><h2>Pan del día</h2>
<p>Horneamos todos los días. Pedidos al (512) 555-0101 o pedidos@sol.example.</p>
<img src="logo@2x.png" alt=""></main><footer>© 2026</footer></body></html>"""


def test_a_page_becomes_raw_findings_with_deterministic_hashes() -> None:
    url = "https://sol.example/"
    page = snapshot(url, url, PAGE, rendered=False)
    assert page["lang"] == "es"
    assert page["title"] == "Panadería El Sol"
    assert page["canonical"] == "https://sol.example/"
    assert page["headings"] == [
        {"level": 1, "text": "Bienvenidos"},
        {"level": 2, "text": "Pan del día"},
    ]
    assert page["json_ld"] == [
        {"@type": "Bakery", "telephone": "+1-512-555-0101"},
        {"invalid_json": "{broken"},
    ]
    assert {"value": "hola@sol.example", "origin": "mailto"} in page["emails"]  # type: ignore[operator]
    assert {"value": "pedidos@sol.example", "origin": "text"} in page["emails"]  # type: ignore[operator]
    assert all("logo@2x" not in str(email) for email in page["emails"])  # type: ignore[union-attr]
    assert {"value": "(512) 555-0101", "origin": "text"} in page["phones"]  # type: ignore[operator]
    links = {str(link["url"]): link for link in page["links"] if isinstance(link, dict)}  # type: ignore[union-attr]
    assert "https://sol.example/about-us" in links, "tracking parameters are gone"
    assert links["https://facebook.example/sol"]["nofollow"] is True
    assert "Horneamos" in str(page["main_text"])
    assert "© 2026" not in str(page["main_text"]), "the footer is not main content"

    again = snapshot(url, url, PAGE, rendered=False)
    assert again == page, "same bytes, same findings"
    reformatted = PAGE.replace("<p>", "\n\n   <p>").replace("</h2>", "</h2>\n")
    assert (
        snapshot(url, url, reformatted, rendered=False)["content_sha256"] == page["content_sha256"]
    )
    assert content_hash("a  b\n c") == content_hash("a b c")


def test_fallback_needs_a_verifiable_reason_and_never_fights_a_challenge() -> None:
    url = "https://x.example/"
    static = snapshot(url, url, PAGE, rendered=False)
    assert fallback_reason(static, 200) is None
    shell = snapshot(
        url, url, "<body><div id='root'></div><script src='a.js'></script>", rendered=False
    )
    assert fallback_reason(shell, 200) == "empty_shell"
    noscript = (
        "<body><noscript>Please enable JavaScript to use this site.</noscript><p>x</p></body>"
    )
    assert fallback_reason(snapshot(url, url, noscript, rendered=False), 200) == "js_required"
    old = "<body><p>Your browser is not supported. Update your browser.</p><script></script></body>"
    assert fallback_reason(snapshot(url, url, old, rendered=False), 200) == "http_functional_block"
    challenge = snapshot(
        url,
        url,
        "<title>Just a moment...</title><body><div id='root'></div><script></script></body>",
        rendered=False,
    )
    assert is_challenge(challenge)
    assert fallback_reason(challenge, 200) is None, "a challenge is never a reason to render"


def test_sitemaps_are_read_without_expanding_entities() -> None:
    laughs = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
        "<urlset><url><loc>https://x.example/&lol2;</loc></url></urlset>"
    )
    is_index, locations = sitemap_locations(laughs)
    assert not is_index
    assert len(locations[0]) < 100, "entities stay unexpanded"
    index = "<sitemapindex><sitemap><loc>https://x.example/pages.xml</loc></sitemap></sitemapindex>"
    assert sitemap_locations(index) == (True, ["https://x.example/pages.xml"])


# ---- SSRF ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.5",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",  # noqa: S104 - the address under test
        "::1",
        "::ffff:127.0.0.1",
        "fd00::1",
        "224.0.0.1",
        "metadata.google.internal",
        "printer.local",
    ],
)
async def test_private_local_and_metadata_targets_are_refused(host: str) -> None:
    with pytest.raises(guard.UnsafeTargetError):
        await guard.public_addresses(host, 80, allow_private=False)


async def test_a_name_resolving_to_a_private_address_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def rebinding(host: str, port: int) -> list[str]:
        return ["93.184.215.14", "10.0.0.7"]

    monkeypatch.setattr(guard, "resolve", rebinding)
    with pytest.raises(guard.UnsafeTargetError, match=r"10\.0\.0\.7"):
        await guard.public_addresses("rebind.example", 443, allow_private=False)
    assert await guard.public_addresses("127.0.0.1", 80, allow_private=True) == ["127.0.0.1"]
    assert await guard.public_addresses("8.8.8.8", 53, allow_private=False) == ["8.8.8.8"]


# ---- small pieces --------------------------------------------------------------------------------


def test_retry_after_in_both_forms() -> None:
    now = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)
    assert retry_after("7") == 7.0
    assert retry_after("Sat, 12 Sep 2026 12:00:30 GMT", now) == 30.0
    assert retry_after("Sat, 12 Sep 2026 11:00:00 GMT", now) == 0.0
    assert retry_after("soon") is None


def test_slots_follow_the_machine() -> None:
    assert slots_for(20, 15 * GIB) == (16, 2)
    assert slots_for(2, 8 * GIB) == (4, 1)
    assert slots_for(8, int(1.5 * GIB)) == (16, 0), "no room for a browser means no browser"
    assert slots_for(4, None) == (8, 1)
