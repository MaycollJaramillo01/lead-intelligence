"""From bytes to a parsed page: charset, type sniffing, DOM extraction, sitemaps and hashes.

What comes out is a set of *raw findings*: the title, metadata, headings, links, the emails and
phone numbers visible on the page, JSON-LD blocks and the main text, as the page shows them. None of
it is validated, normalised or interpreted here - contacts are checked in phase 09, evidence is read
in phases 07 and 08. The function is deterministic: the same bytes always give the same snapshot and
the same hashes, so a stored body can be parsed again and compared.
"""

import codecs
import hashlib
import re
import zlib
from typing import Final

import orjson
from pydantic import JsonValue
from selectolax.lexbor import LexborHTMLParser, LexborNode

from research_engine.crawl.urls import canonical
from research_engine.domain import JsonObject
from research_engine.resolution.normalize import not_the_business

PARSER: Final = "selectolax.lexbor"
PARSER_VERSION: Final = "1.0.0"
MAX_MAIN_TEXT: Final = 20_000
MAX_LINKS: Final = 300
MAX_HEADINGS: Final = 50
MAX_FINDINGS: Final = 25
MAX_JSON_LD_BLOCKS: Final = 20
MAX_JSON_LD_CHARS: Final = 64_000
MAX_SITEMAP_LOCS: Final = 1_000
#: An empty shell: scripts and next to no visible text, or an empty app mount point and little text.
BLANK_PAGE_CHARS: Final = 30
EMPTY_SHELL_CHARS: Final = 200
_APP_ROOT: Final = "#root, #app, #__next, #___gatsby, app-root, [data-reactroot], [ng-app]"

_BOMS: Final = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
_META_CHARSET: Final = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.I)
#: WHATWG maps these labels to windows-1252; decoding them as latin-1 would lose characters.
_ALIASES: Final = {"iso-8859-1": "cp1252", "latin1": "cp1252", "us-ascii": "cp1252",
                   "ascii": "cp1252", "x-sjis": "shift_jis", "utf8": "utf-8"}  # fmt: skip
_BINARY_MAGIC: Final = (b"%PDF", b"PK\x03\x04", b"MZ", b"\x7fELF", b"GIF8", b"\x89PNG",
                        b"\xff\xd8\xff", b"Rar!", b"7z\xbc\xaf", b"\x1f\x8b", b"OggS")  # fmt: skip
_EMAIL: Final = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})+")
_PHONE: Final = re.compile(r"(?<![\w+])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
_NOT_AN_EMAIL_TLD: Final = frozenset({"png", "jpg", "jpeg", "gif", "webp", "svg", "css", "js"})
_NOISE: Final = "script, style, noscript, template, svg, iframe, object, canvas"
_CHROME: Final = "nav, header, footer, aside, form"
#: Markers of CAPTCHA pages, WAF challenges and bot walls. They are never worked around.
_CHALLENGE: Final = re.compile(
    r"(captcha|cf-chl-|challenge-platform|just a moment\.\.\.|attention required! \| cloudflare|"
    r"_incapsula_resource|px-captcha|datadome|are you a robot|verify you are human)",
    re.IGNORECASE,
)
_JS_REQUIRED: Final = re.compile(
    r"(enable javascript|javascript is (required|disabled)|requires javascript|"
    r"habilita javascript|activa javascript)",
    re.IGNORECASE,
)
_UNSUPPORTED_CLIENT: Final = re.compile(
    r"(browser is not supported|unsupported browser|update your browser|navegador no compatible)",
    re.IGNORECASE,
)
GZIP_LIMIT_ERROR: Final = "decompressed sitemap exceeds its size limit"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _codec(label: bytes | str | None) -> str | None:
    if not label:
        return None
    text = (label.decode("ascii", "ignore") if isinstance(label, bytes) else label).strip().lower()
    try:
        return codecs.lookup(_ALIASES.get(text, text)).name
    except LookupError:
        return None


def charset_of(content_type: str | None) -> str | None:
    for part in (content_type or "").split(";")[1:]:
        key, _, value = part.partition("=")
        if key.strip().lower() == "charset":
            return _codec(value.strip().strip("\"'"))
    return None


def decode(body: bytes, content_type: str | None) -> tuple[str, str]:
    """Text and the codec used: BOM, then the HTTP header, then `<meta>`, then UTF-8, then 1252."""
    for bom, bom_codec in _BOMS:
        if body.startswith(bom):
            return body.decode(bom_codec, errors="replace"), bom_codec
    codec = charset_of(content_type)
    if codec is None and (match := _META_CHARSET.search(body[:4096])):
        codec = _codec(match.group(1))
        if codec is not None and codec.startswith("utf-16"):
            codec = "utf-8"  # a meta tag readable as ASCII cannot be UTF-16
    if codec is None:
        try:
            return body.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            codec = "cp1252"
    return body.decode(codec, errors="replace"), codec


def looks_binary(body: bytes) -> bool:
    head = body[:16]
    return any(head.startswith(magic) for magic in _BINARY_MAGIC) or b"\x00" in body[:1024]


def gunzip(body: bytes, limit: int) -> bytes:
    """Bounded gzip decompression: a bomb stops at `limit` instead of filling the memory."""
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    data = inflater.decompress(body, limit + 1)
    if len(data) > limit or inflater.unconsumed_tail:
        raise ValueError(GZIP_LIMIT_ERROR)
    return data


def _text(node: LexborNode | None) -> str:
    return " ".join((node.text(deep=True, separator=" ", strip=True) if node else "").split())


def _unique(values: list[JsonObject], key: str, limit: int) -> list[JsonValue]:
    seen: set[str] = set()
    out: list[JsonValue] = []
    for value in values:
        marker = str(value[key])
        if marker not in seen:
            seen.add(marker)
            out.append(value)
    return out[:limit]


def _json_ld(tree: LexborHTMLParser) -> list[JsonValue]:
    blocks: list[JsonValue] = []
    for node in tree.css('script[type="application/ld+json"]')[:MAX_JSON_LD_BLOCKS]:
        raw = (node.text(deep=True) or "").strip()[:MAX_JSON_LD_CHARS]
        try:
            blocks.append(orjson.loads(raw))
        except orjson.JSONDecodeError:
            blocks.append({"invalid_json": raw[:2_000]})
    return blocks


def snapshot(url: str, final_url: str, html: str, *, rendered: bool) -> JsonObject:
    """The page as raw findings. `final_url` is the base links are resolved against.

    `signals` records the measurements the fallback and block decisions are made from, so the
    reason a page was rendered in a browser can be checked against the page itself.
    """
    tree = LexborHTMLParser(html)
    scripts = len(tree.css("script:not([type='application/ld+json'])"))
    noscript = " ".join(_text(node) for node in tree.css("noscript"))
    widget = tree.css_first("[class*='g-recaptcha'], [class*='h-captcha'], #challenge-form")
    app_root = any(not _text(node) for node in tree.css(_APP_ROOT))
    head_meta: dict[str, JsonValue] = {}
    for node in tree.css("meta"):
        attributes = node.attributes
        name = (attributes.get("name") or attributes.get("property") or "").strip().lower()
        content = (attributes.get("content") or "").strip()
        if name and content and len(head_meta) < 40 and name not in head_meta:
            head_meta[name] = content[:500]
    canonical_node = tree.css_first("link[rel='canonical']")
    html_node = tree.css_first("html")
    title = _text(tree.css_first("title"))[:300]
    headings: list[JsonValue] = []
    for node in tree.css("h1, h2, h3"):
        if (text := _text(node)) and len(headings) < MAX_HEADINGS:
            headings.append({"level": int((node.tag or "h0")[1]), "text": text[:300]})

    links: list[JsonObject] = []
    emails: list[JsonObject] = []
    phones: list[JsonObject] = []
    for node in tree.css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        lowered = href.lower()
        if lowered.startswith("mailto:"):
            address = href[7:].split("?", 1)[0].strip()
            if address and not not_the_business(address):
                emails.append({"value": address, "origin": "mailto"})
        elif lowered.startswith("tel:"):
            number = href[4:].strip()
            if number:
                phones.append({"value": number, "origin": "tel_link"})
        elif (target := canonical(href, final_url)) is not None:
            rel = (node.attributes.get("rel") or "").lower()
            links.append(
                {"url": target, "text": _text(node)[:120], "nofollow": "nofollow" in rel.split()}
            )
    json_ld = _json_ld(tree)

    body = tree.body
    if body is not None:
        for node in body.css(_NOISE):
            node.decompose()
    visible = _text(body)
    for match in _EMAIL.finditer(visible):
        tld = match.group().rsplit(".", 1)[-1].lower()
        if tld not in _NOT_AN_EMAIL_TLD and not not_the_business(match.group()):
            emails.append({"value": match.group(), "origin": "text"})
    for match in _PHONE.finditer(visible):
        phones.append({"value": match.group(), "origin": "text"})
    main = tree.css_first("main, article, [role='main']")
    if main is None and body is not None:
        for node in body.css(_CHROME):
            node.decompose()
        main = body
    main_text = _text(main)[:MAX_MAIN_TEXT]
    return {
        "parser": PARSER,
        "parser_version": PARSER_VERSION,
        "url": url,
        "final_url": final_url,
        "rendered": rendered,
        "lang": (html_node.attributes.get("lang") or None) if html_node else None,
        "title": title or None,
        "meta": head_meta,
        "canonical": canonical(canonical_node.attributes.get("href") or "", final_url)
        if canonical_node
        else None,
        "headings": headings,
        "links": _unique(links, "url", MAX_LINKS),
        "emails": _unique(emails, "value", MAX_FINDINGS),
        "phones": _unique(phones, "value", MAX_FINDINGS),
        "json_ld": json_ld,
        "main_text": main_text,
        "content_sha256": content_hash(visible),
        "signals": {
            "text_chars": len(visible),
            "scripts": scripts,
            "empty_app_root": app_root,
            "js_required": bool(_JS_REQUIRED.search(f"{noscript} {visible[:2_000]}")),
            "unsupported_client": bool(_UNSUPPORTED_CLIENT.search(visible[:2_000])),
            "challenge": widget is not None
            or bool(_CHALLENGE.search(f"{title} {visible[:3_000]}")),
        },
    }


def content_hash(text: str) -> str:
    """Hash of the visible text with whitespace normalised: markup-only changes do not move it."""
    return sha256(" ".join(text.split()).encode("utf-8"))


def _signal(page: JsonObject, name: str) -> JsonValue:
    found = page.get("signals")
    return found.get(name) if isinstance(found, dict) else None


def is_challenge(page: JsonObject) -> bool:
    return _signal(page, "challenge") is True


def fallback_reason(page: JsonObject, status: int) -> str | None:
    """A verifiable reason to render this page in a browser, or None when HTTP was enough."""
    if is_challenge(page):
        return None  # a challenge is a block, never a reason to try harder
    if status == 406 or _signal(page, "unsupported_client") is True:
        return "http_functional_block"
    if _signal(page, "js_required") is True:
        return "js_required"
    text_chars, scripts = _signal(page, "text_chars"), _signal(page, "scripts")
    if not (isinstance(text_chars, int) and isinstance(scripts, int) and scripts > 0):
        return None
    empty_root = _signal(page, "empty_app_root") is True
    if text_chars < BLANK_PAGE_CHARS or (text_chars < EMPTY_SHELL_CHARS and empty_root):
        return "empty_shell"
    return None


def sitemap_locations(text: str) -> tuple[bool, list[str]]:
    """(is an index, `<loc>` values). Read by an HTML parser, so no XML entity is ever expanded."""
    tree = LexborHTMLParser(text)
    is_index = tree.css_first("sitemapindex") is not None
    locations = [_text(node) for node in tree.css("loc")[:MAX_SITEMAP_LOCS]]
    return is_index, [location for location in locations if location]
