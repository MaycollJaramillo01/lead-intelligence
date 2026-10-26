"""URLs: one canonical form, the site they belong to, what kind of page they look like, and the
policies that keep a crawl out of traps, private areas and binary files.

Canonical form is what dedup and robots rules see: http(s) only, lower-case IDNA host, no default
port, no credentials, no fragment, dot segments resolved, tracking parameters dropped and the rest
sorted. It changes nothing a server would answer differently to, except the order of parameters.
"""

import fnmatch
import re
from typing import Final
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

from research_engine.domain import CrawlSettings, PageKind, sort_key

MAX_URL_LENGTH: Final = 2048
MAX_QUERY_PARAMS: Final = 3
MAX_SEGMENTS: Final = 8
_TRACKING: Final = frozenset(
    {"gclid", "gbraid", "wbraid", "dclid", "fbclid", "msclkid", "yclid", "igshid", "mc_cid",
     "mc_eid", "_ga", "_gl", "_hsenc", "_hsmi", "hsctatrk", "mkt_tok", "ref_src", "srsltid"}
)  # fmt: skip
_SAFE_PATH: Final = "/%:@!$&'()*+,;=-._~"
_ESCAPE: Final = re.compile(r"%[0-9a-fA-F]{2}")
_BINARY: Final = re.compile(
    r"\.(pdf|zip|rar|7z|gz|tgz|bz2|exe|msi|dmg|apk|iso|bin|jpe?g|png|gif|webp|avif|svg|ico|bmp|"
    r"tiff?|heic|mp[34]|m4[av]|mov|avi|wmv|webm|ogg|wav|flac|woff2?|ttf|otf|eot|css|js|json|"
    r"xml|rss|atom|docx?|xlsx?|pptx?|csv|ics|vcf)$",
    re.IGNORECASE,
)
_PRIVATE: Final = re.compile(
    r"(^|/)(log-?in|log-?out|sign-?in|sign-?out|sign-?up|register|registro|account|my-account|"
    r"mi-cuenta|cart|carrito|checkout|basket|wishlist|admin|wp-admin|wp-login\.php|dashboard|"
    r"password|reset-password)(/|$|\.)",
    re.IGNORECASE,
)
_TRAP_PATH: Final = re.compile(
    r"(calendar|calendario|/events?/\d{4}|/\d{4}/\d{1,2}/\d{1,2}(/|$)|/page/\d{2,}|/feed/?$|"
    r"/wp-json/|/cdn-cgi/|/xmlrpc\.php|/tag/|/search(/|$))",
    re.IGNORECASE,
)
_TRAP_KEYS: Final = frozenset(
    {"date", "day", "month", "year", "ical", "sort", "order", "orderby", "filter", "view",
     "replytocom", "sessionid", "sid", "phpsessid", "jsessionid", "s", "q", "search",
     "add-to-cart", "share", "print", "format", "output"}
)  # fmt: skip
#: Words that say what a page is, in English and Spanish, compared accent- and case-insensitively.
_KIND_WORDS: Final = (
    (PageKind.CONTACT, ("contact", "contacto", "contactanos", "contactenos", "get in touch",
                        "location", "locations", "ubicacion", "visit us", "find us")),
    (PageKind.ABOUT, ("about", "nosotros", "quienes somos", "sobre nosotros", "acerca",
                      "our story", "our team", "equipo", "historia", "who we are")),
    (PageKind.SERVICES, ("services", "servicios", "service", "what we do", "solutions",
                         "soluciones", "our work", "menu", "products", "productos")),
)  # fmt: skip
_PRIORITY: Final = {
    PageKind.HOME: 100,
    PageKind.CONTACT: 90,
    PageKind.ABOUT: 80,
    PageKind.SERVICES: 70,
    PageKind.OTHER: 20,
}
ROBOTS_PRIORITY: Final = 1_000
SITEMAP_PRIORITY: Final = 900


def _normal_path(path: str) -> str:
    segments: list[str] = []
    parts = path.split("/")
    for part in parts:
        if part == "..":
            if segments:
                segments.pop()
        elif part != ".":
            segments.append(part)
    joined = "/".join(segments)
    if parts[-1] in (".", "..") and not joined.endswith("/"):
        joined += "/"
    joined = quote(joined if joined.startswith("/") else "/" + joined, safe=_SAFE_PATH)
    return _ESCAPE.sub(lambda match: match.group().upper(), joined)


def canonical(url: str, base: str | None = None) -> str | None:
    """The canonical absolute http(s) URL, or None when the value cannot be one."""
    text = "".join(ch for ch in url.strip() if ch >= " " and ch != "\x7f")
    if not text:
        return None
    try:
        parts = urlsplit(urljoin(base, text) if base else text)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or parts.username or parts.password or not parts.hostname:
        return None
    try:
        host = parts.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if not host or ".." in host:
        return None
    netloc = host if ":" not in host else f"[{host}]"
    if port is not None and port != (443 if scheme == "https" else 80):
        netloc += f":{port}"
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in _TRACKING and not key.lower().startswith("utm_")
        ),
        quote_via=quote,
    )
    result = urlunsplit((scheme, netloc, _normal_path(parts.path), query, ""))
    return result if len(result) <= MAX_URL_LENGTH else None


_INDEX_FILE: Final = re.compile(r"/(?:index|default)\.(?:html?|php|aspx?)$", re.IGNORECASE)


def page_key(url: str) -> str:
    """What a site has already seen: one page whatever its scheme, a leading `www.`, a trailing
    slash or an index file. Fetching `http://www.x.com/` then `https://x.com/index.html` spent the
    page budget on the home page twice and left out the About page."""
    parts = urlsplit(url)
    path = _INDEX_FILE.sub("/", parts.path).rstrip("/") or "/"
    return urlunsplit(("", parts.netloc.removeprefix("www."), path, parts.query, ""))


def host_of(url: str) -> str:
    return urlsplit(url).hostname or ""


def site_of(url: str) -> str:
    """The site a URL belongs to: host and non-default port, without a leading `www.`."""
    return urlsplit(url).netloc.removeprefix("www.")


def in_scope(url: str, site: str, include_subdomains: bool) -> bool:
    netloc = urlsplit(url).netloc
    if netloc in (site, f"www.{site}"):
        return True
    return include_subdomains and netloc.endswith(f".{site}")


def seed_url(value: str) -> str | None:
    """An address as an operator or a source writes it: a scheme is optional, https assumed."""
    text = value.strip()
    return canonical(text if "://" in text else f"https://{text}")


def path_and_query(url: str) -> str:
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


def page_kind(url: str, anchor: str | None = None) -> PageKind:
    path = urlsplit(url).path
    if path in ("", "/") or re.fullmatch(r"/(index|home|inicio)(\.\w+)?/?", path, re.I):
        return PageKind.HOME
    words = sort_key(re.sub(r"[/_\-.]+", " ", path) + " " + (anchor or ""))
    for kind, terms in _KIND_WORDS:
        if any(re.search(rf"\b{re.escape(term)}\b", words) for term in terms):
            return kind
    return PageKind.OTHER


def priority(kind: PageKind, depth: int) -> int:
    return max(0, _PRIORITY[kind] - 10 * depth)


def skip_reason(url: str, settings: CrawlSettings) -> str | None:
    """Why a discovered page URL is not worth fetching, or None when it is."""
    parts = urlsplit(url)
    path = parts.path
    if _BINARY.search(path):
        return "binary_resource"
    if _PRIVATE.search(path):
        return "private_area"
    segments = [segment for segment in path.split("/") if segment]
    params = parse_qsl(parts.query, keep_blank_values=True)
    if (
        _TRAP_PATH.search(path)
        or len(segments) > MAX_SEGMENTS
        or len(params) > MAX_QUERY_PARAMS
        or any(key.lower() in _TRAP_KEYS for key, _ in params)
        or any(segments.count(segment) >= 3 for segment in set(segments))
    ):
        return "trap"
    if any(fnmatch.fnmatchcase(path, rule) for rule in settings.exclude_paths):
        return "excluded"
    if settings.include_paths and not any(
        fnmatch.fnmatchcase(path, rule) for rule in settings.include_paths
    ):
        return "not_included"
    return None
