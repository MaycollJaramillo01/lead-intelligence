"""Pure, versioned normalisers: a raw value in, its normalised form and structure out (phase 06).

Nothing here touches the store, the network or a clock, and nothing overwrites a raw value: callers
keep the value as acquired next to what these functions return. The same input gives the same
output for a given `NORMALIZER_VERSION`; any change of behaviour bumps it, which makes the
resolution engine write new observations instead of silently reinterpreting old ones.

Missing values are never invented. Every result says how present its value was:
`present`, `missing` (empty or a null token), `unknown` (the source said it does not know),
`not_applicable` (the source said it does not apply) or `invalid` (there is a value, but it is not
one of these things).
"""

import html
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum
from functools import lru_cache
from typing import Final
from urllib.parse import parse_qsl, urlencode, urlsplit

NORMALIZER_VERSION: Final = "1.0.0"
TAXONOMY_VERSION: Final = "2026.1"


class Presence(StrEnum):
    PRESENT = "present"
    MISSING = "missing"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"
    INVALID = "invalid"


# ---- text --------------------------------------------------------------------------------------

_MISSING_TOKENS: Final = frozenset({"", "null", "none", "nil", "-", "--", "n/a", "na", "#n/a"})
_UNKNOWN_TOKENS: Final = frozenset({"unknown", "unk", "tbd", "?", "not available", "desconocido"})
_NOT_APPLICABLE_TOKENS: Final = frozenset({"not applicable", "no aplica"})
#: Latin-1/cp1252 renderings of UTF-8 bytes: "CafÃ©" is "Café" decoded with the wrong codec.
_MOJIBAKE: Final = re.compile(
    r"[\xc3\xc2\xe2][\x80-\xbf\u2018-\u203a\u0152\u0153\u0160\u0161\u017d\u017e]"
)
_INVISIBLE: Final = re.compile(r"[\u200b-\u200f\u2060\ufeff]")


def _repair_mojibake(text: str) -> str:
    if not _MOJIBAKE.search(text):
        return text
    for codec in ("cp1252", "latin-1"):
        try:
            repaired = text.encode(codec).decode("utf-8")
        except UnicodeError:
            continue
        if len(_MOJIBAKE.findall(repaired)) < len(_MOJIBAKE.findall(text)):
            return repaired
    return text


def clean_text(value: object) -> tuple[str | None, Presence]:
    """Unicode NFC, HTML entities, mojibake, control characters and whitespace; null tokens out."""
    if value is None or isinstance(value, bool | dict | list):
        return None, Presence.MISSING
    text = _repair_mojibake(html.unescape(str(value)))
    text = _INVISIBLE.sub("", unicodedata.normalize("NFC", text))
    text = "".join(ch if unicodedata.category(ch)[0] != "C" else " " for ch in text)
    text = " ".join(text.split())
    lowered = text.casefold()
    if lowered in _MISSING_TOKENS:
        return None, Presence.MISSING
    if lowered in _UNKNOWN_TOKENS:
        return None, Presence.UNKNOWN
    if lowered in _NOT_APPLICABLE_TOKENS:
        return None, Presence.NOT_APPLICABLE
    return text, Presence.PRESENT


def fold(text: str) -> str:
    """Comparison form: case-folded, accents removed, `&` spelled out, punctuation to spaces."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    plain = plain.replace("&", " and ").replace("'", "").replace("\u2019", "")
    plain = re.sub(r"\b(?:[a-z]\.){2,}", lambda match: match.group().replace(".", ""), plain)
    return " ".join(re.sub(r"[^\w]+", " ", plain).replace("_", " ").split())


# ---- business names ----------------------------------------------------------------------------

#: Legal forms removed from the end of a name key (never from the name as shown).
LEGAL_SUFFIXES: Final = (
    ("s", "de", "rl", "de", "cv"), ("sa", "de", "cv"), ("s", "de", "rl"), ("l", "l", "c"),
    ("p", "l", "l", "c"), ("pllc",), ("llc",), ("llp",), ("lllp",), ("lp",), ("lc",), ("ltd",),
    ("limited",), ("inc",), ("incorporated",), ("corp",), ("corporation",), ("co",),
    ("company",), ("pc",), ("pa",), ("plc",), ("gmbh",), ("sa",), ("srl",),
)  # fmt: skip
_DBA: Final = re.compile(
    r"\s+(?:d\s*/?\s*b\s*/?\s*a|doing business as|a\s*/?\s*k\s*/?\s*a)\.?\s+", re.I
)
_TOKEN_ALIASES: Final = {
    "and": "and", "y": "and", "bros": "brothers", "intl": "international", "natl": "national",
    "svc": "service", "svcs": "services", "mfg": "manufacturing", "ctr": "center",
    "assoc": "associates", "mgmt": "management", "dept": "department", "centre": "center",
}  # fmt: skip
#: Words that describe a trade, a place or a legal form rather than one business. A name made only
#: of these ("Cleaning Services", "Auto Repair Shop") cannot identify a business on its own.
GENERIC_TOKENS: Final = frozenset(
    [
        "a",
        "an",
        "and",
        "of",
        "the",
        "at",
        "in",
        "on",
        "for",
        "by",
        "to",
        "de",
        "del",
        "la",
        "las",
        "el",
        "los",
        "y",
        "en",
        "service",
        "services",
        "solutions",
        "group",
        "enterprises",
        "enterprise",
        "company",
        "companies",
        "co",
        "corp",
        "inc",
        "llc",
        "center",
        "professional",
        "professionals",
        "pro",
        "pros",
        "quality",
        "best",
        "express",
        "premier",
        "general",
        "united",
        "national",
        "american",
        "america",
        "usa",
        "us",
        "international",
        "global",
        "local",
        "family",
        "home",
        "city",
        "metro",
        "area",
        "cleaning",
        "clean",
        "janitorial",
        "maid",
        "construction",
        "contractor",
        "contractors",
        "contracting",
        "builders",
        "building",
        "roofing",
        "roofer",
        "plumbing",
        "plumber",
        "electric",
        "electrical",
        "electrician",
        "hvac",
        "heating",
        "cooling",
        "air",
        "conditioning",
        "painting",
        "painter",
        "landscaping",
        "landscape",
        "lawn",
        "garden",
        "tree",
        "auto",
        "automotive",
        "car",
        "repair",
        "mechanic",
        "body",
        "collision",
        "tire",
        "tires",
        "restaurant",
        "restaurants",
        "grill",
        "cafe",
        "bakery",
        "pastry",
        "market",
        "grocery",
        "store",
        "shop",
        "shops",
        "mart",
        "supermarket",
        "deli",
        "pizza",
        "salon",
        "beauty",
        "barber",
        "nails",
        "spa",
        "hair",
        "dental",
        "dentist",
        "clinic",
        "medical",
        "health",
        "care",
        "insurance",
        "tax",
        "taxes",
        "accounting",
        "bookkeeping",
        "legal",
        "law",
        "realty",
        "real",
        "estate",
        "properties",
        "trucking",
        "transport",
        "transportation",
        "logistics",
        "moving",
        "storage",
        "security",
        "staffing",
        "consulting",
        "management",
        "associates",
        "partners",
        "brothers",
        "sons",
        "international",
        "servicios",
        "limpieza",
        "construccion",
        "techos",
        "plomeria",
        "jardineria",
        "taller",
        "restaurante",
        "panaderia",
        "tienda",
        "mercado",
        "carniceria",
        "taqueria",
        "salon",
        "estetica",
        "seguros",
        "abogados",
    ]
)
_GENERIC_FUZZY_MIN_LENGTH: Final = 5
_GENERIC_FUZZY_RATIO: Final = 0.85


@lru_cache(maxsize=65_536)
def is_generic_token(token: str) -> bool:
    """A generic word, or a small misspelling of one ("rofing"). Cached: tokens repeat a lot."""
    if token in GENERIC_TOKENS or token.isdigit() or len(token) == 1:
        return True
    if len(token) < _GENERIC_FUZZY_MIN_LENGTH:
        return False
    for word in GENERIC_TOKENS:
        if len(word) < _GENERIC_FUZZY_MIN_LENGTH or abs(len(word) - len(token)) > 2:
            continue
        matcher = SequenceMatcher(None, token, word)
        # the cheap upper bounds rule out almost every word before the real ratio is computed
        if (
            matcher.real_quick_ratio() >= _GENERIC_FUZZY_RATIO
            and matcher.quick_ratio() >= _GENERIC_FUZZY_RATIO
            and matcher.ratio() >= _GENERIC_FUZZY_RATIO
        ):
            return True
    return False


@dataclass(frozen=True, slots=True)
class NameResult:
    raw: str | None
    #: The name as shown, tidied but not reinterpreted.
    display: str | None
    #: Comparison key: folded, aliases spelled out, legal form and leading article removed.
    key: str | None
    tokens: tuple[str, ...]
    #: Tokens that are not generic words: what actually tells two businesses apart.
    distinctive: tuple[str, ...]
    legal_suffix: str | None
    #: When the source wrote "Legal Name dba Trade Name", the trade name...
    dba: str | None
    #: ...and the legal name before it.
    legal: str | None
    status: Presence

    @property
    def is_generic(self) -> bool:
        return self.status is Presence.PRESENT and not self.distinctive


def _name_key(text: str) -> tuple[tuple[str, ...], str | None]:
    tokens = [_TOKEN_ALIASES.get(token, token) for token in fold(text).split()]
    suffix: str | None = None
    stripped = True
    while stripped and len(tokens) > 1:
        stripped = False
        for form in LEGAL_SUFFIXES:
            if len(tokens) > len(form) and tuple(tokens[-len(form) :]) == form:
                suffix = suffix or "".join(form)
                tokens = tokens[: -len(form)]
                stripped = True
                break
    if len(tokens) > 1 and tokens[0] == "the":
        tokens = tokens[1:]
    return tuple(tokens), suffix


def business_name(value: object) -> NameResult:
    text, status = clean_text(value)
    if text is None:
        return NameResult(value if isinstance(value, str) else None, None, None, (), (), None, None,
                          None, status)  # fmt: skip
    legal, dba = text, None
    if (parts := _DBA.split(text, maxsplit=1)) and len(parts) == 2 and all(parts):
        legal, dba = parts[0].strip(), parts[1].strip()
    tokens, suffix = _name_key(dba or legal)
    if not tokens:
        return NameResult(str(value), text, None, (), (), None, dba, None, Presence.INVALID)
    if suffix is None and dba is not None:
        suffix = _name_key(legal)[1]
    distinctive = tuple(token for token in tokens if not is_generic_token(token))
    return NameResult(str(value), text, " ".join(tokens), tokens, distinctive, suffix, dba,
                      legal if dba else None, Presence.PRESENT)  # fmt: skip


# ---- phones ------------------------------------------------------------------------------------


class PhoneKind(StrEnum):
    GEOGRAPHIC = "geographic"
    TOLL_FREE = "toll_free"
    PREMIUM = "premium"
    #: 555-0100 to 555-0199: reserved for fiction. Structurally valid, never a real line.
    FICTIONAL = "fictional"
    INTERNATIONAL = "international"


_EXTENSION: Final = re.compile(
    r"\s*(?:,|;)?\s*(?:ext\.?|extension|ex\.?|x|#)\s*(\d{1,6})\s*$", re.I
)
_LABEL_PREFIX: Final = re.compile(
    r"^(?:tel(?:ephone)?|phone|ph|call|cell|mobile|office|main|tel[eé]fono|t|p)\s*[:.]\s*", re.I
)
_VANITY: Final = re.compile(r"^\+?[\d\s().-]*[A-Za-z][A-Za-z\d\s().-]*$")
_KEYPAD: Final = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "22233344455566677778889999")
_TOLL_FREE: Final = frozenset({"800", "833", "844", "855", "866", "877", "888"})


@dataclass(frozen=True, slots=True)
class PhoneResult:
    raw: str | None
    e164: str | None
    #: `(512) 555-0101` for NANP numbers.
    national: str | None
    extension: str | None
    kind: PhoneKind | None
    status: Presence


def phone(value: object, *, country: str = "US") -> PhoneResult:
    """E.164 for NANP numbers (the only country this build validates) and for explicit `+` numbers.

    A number without a country code outside the NANP cannot be placed, so it is `invalid` rather
    than guessed. Letters of vanity numbers are read on the keypad.
    """
    text, status = clean_text(value)
    raw = value if isinstance(value, str) else (None if value is None else str(value))
    if text is None:
        return PhoneResult(raw, None, None, None, None, status)
    extension = None
    if match := _EXTENSION.search(text):
        extension, text = match.group(1), text[: match.start()]
    text = _LABEL_PREFIX.sub("", text.strip())
    explicit = text.startswith("+")
    letters = sum(ch.isalpha() for ch in text)
    vanity = _VANITY.match(text) and 1 <= letters <= 7 and sum(ch.isdigit() for ch in text) >= 3
    digits = re.sub(r"\D", "", text.upper().translate(_KEYPAD) if vanity else text)
    if explicit and not digits.startswith("1"):
        if 8 <= len(digits) <= 15:
            return PhoneResult(raw, f"+{digits}", None, extension, PhoneKind.INTERNATIONAL,
                               Presence.PRESENT)  # fmt: skip
        return PhoneResult(raw, None, None, extension, None, Presence.INVALID)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10 or country != "US":
        return PhoneResult(raw, None, None, extension, None, Presence.INVALID)
    area, exchange, line = digits[:3], digits[3:6], digits[6:]
    if area[0] in "01" or exchange[0] in "01" or area[1:] == "11" or exchange[1:] == "11":
        return PhoneResult(raw, None, None, extension, None, Presence.INVALID)
    if area in _TOLL_FREE:
        kind = PhoneKind.TOLL_FREE
    elif area == "900":
        kind = PhoneKind.PREMIUM
    elif exchange == "555" and "0100" <= line <= "0199":
        kind = PhoneKind.FICTIONAL
    else:
        kind = PhoneKind.GEOGRAPHIC
    return PhoneResult(raw, f"+1{digits}", f"({area}) {exchange}-{line}", extension, kind,
                       Presence.PRESENT)  # fmt: skip


# ---- emails ------------------------------------------------------------------------------------

_LOCAL: Final = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}$")
_LABEL: Final = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
ROLE_LOCALS: Final = frozenset(
    [
        "info",
        "contact",
        "contacts",
        "sales",
        "admin",
        "administrator",
        "office",
        "hello",
        "hi",
        "support",
        "service",
        "services",
        "billing",
        "accounts",
        "accounting",
        "team",
        "help",
        "marketing",
        "hr",
        "jobs",
        "careers",
        "booking",
        "bookings",
        "reservations",
        "orders",
        "order",
        "noreply",
        "no-reply",
        "donotreply",
        "webmaster",
        "postmaster",
        "inquiries",
        "enquiries",
        "inquiry",
        "mail",
        "general",
        "frontdesk",
        "reception",
        "customerservice",
        "care",
        "ventas",
        "contacto",
        "informacion",
        "oficina",
    ]
)
FREE_EMAIL_DOMAINS: Final = frozenset(
    [
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "ymail.com",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "msn.com",
        "aol.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "protonmail.com",
        "proton.me",
        "gmx.com",
        "mail.com",
        "zoho.com",
        "comcast.net",
        "att.net",
        "sbcglobal.net",
        "verizon.net",
        "bellsouth.net",
        "cox.net",
        "charter.net",
        "earthlink.net",
    ]
)
#: Domains whose addresses a page shows but that are never the business's own: template
#: placeholders, domain-sale notices and the booking or site platforms it publishes through (D-106).
# ponytail: the ones real lists met; an address several businesses publish is caught by
# `exports.best_contacts` whatever its domain
NOT_THE_BUSINESS_EMAIL_DOMAINS: Final = frozenset(
    [
        "example.com",
        "example.org",
        "example.net",
        "emailaddress.com",
        "yourdomain.com",
        "domain.com",
        "mysite.com",
        "yoursite.com",
        "yourwebsite.com",
        "mailservice.com",
        "godaddy.com",
        "wix.com",
        "wixpress.com",
        "domainmarket.com",
        "hugedomains.com",
        "dan.com",
        "sedo.com",
        "afternic.com",
        "booksy.com",
        "vagaro.com",
        "squareup.com",
        "styleseat.com",
        "schedulicity.com",
        "fresha.com",
        "glossgenius.com",
        "mindbodyonline.com",
        "setmore.com",
        "acuityscheduling.com",
        "genbook.com",
        "zenoti.com",
        "toasttab.com",
        "opentable.com",
        "thryv.com",
        "ueni.com",
    ]
)


def not_the_business(address: str) -> bool:
    """An address at a placeholder, domain-sale or platform domain (D-106)."""
    domain = email(address).domain
    return domain is not None and registrable_domain(domain) in NOT_THE_BUSINESS_EMAIL_DOMAINS


def ascii_host(host: str) -> str | None:
    """Lower-case ASCII (punycode) form of a host name, or None when it is not a valid one."""
    host = host.strip().rstrip(".").lower()
    try:
        encoded = host.encode("idna").decode("ascii") if not host.isascii() else host
    except UnicodeError:
        return None
    labels = encoded.split(".")
    if len(labels) < 2 or not all(_LABEL.match(label) for label in labels):
        return None
    if not (labels[-1].isalpha() or labels[-1].startswith("xn--")) or len(labels[-1]) < 2:
        return None
    return encoded


@dataclass(frozen=True, slots=True)
class EmailResult:
    raw: str | None
    #: Local part exactly as written (it may be case-sensitive), domain in lower-case ASCII.
    address: str | None
    #: Case-insensitive comparison key. Dots and `+tags` are kept: they mean different mailboxes on
    #: most providers, and rewriting them would merge people who are not the same.
    match_key: str | None
    local: str | None
    domain: str | None
    is_role: bool
    is_free_provider: bool
    status: Presence


def email(value: object) -> EmailResult:
    text, status = clean_text(value)
    raw = value if isinstance(value, str) else None
    if text is None:
        return EmailResult(raw, None, None, None, None, False, False, status)
    text = re.sub(r"^mailto:", "", text, flags=re.I).split("?", 1)[0]
    text = text.strip().strip("<>").rstrip(".,;:)").strip()
    if text.count("@") != 1:
        return EmailResult(raw, None, None, None, None, False, False, Presence.INVALID)
    local, _, host = text.partition("@")
    domain = ascii_host(host)
    valid_local = bool(_LOCAL.match(local)) and not (
        local.startswith(".") or local.endswith(".") or ".." in local
    )
    if domain is None or not valid_local:
        return EmailResult(raw, None, None, None, None, False, False, Presence.INVALID)
    address = f"{local}@{domain}"
    return EmailResult(
        raw,
        address,
        address.casefold(),
        local,
        domain,
        local.casefold().split("+", 1)[0] in ROLE_LOCALS,
        domain in FREE_EMAIL_DOMAINS,
        Presence.PRESENT,
    )


# ---- websites ----------------------------------------------------------------------------------

#: Public suffixes of more than one label that a US-focused dataset meets in practice.
# ponytail: a subset of the Public Suffix List; ship the full list if foreign domains matter
MULTI_LABEL_SUFFIXES: Final = frozenset(
    [
        "co.uk",
        "org.uk",
        "ac.uk",
        "gov.uk",
        "me.uk",
        "com.au",
        "net.au",
        "org.au",
        "com.mx",
        "org.mx",
        "gob.mx",
        "net.mx",
        "com.br",
        "net.br",
        "org.br",
        "co.nz",
        "co.za",
        "com.ar",
        "com.co",
        "com.pe",
        "com.ve",
        "com.ec",
        "com.gt",
        "com.sv",
        "com.hn",
        "com.ni",
        "com.do",
        "com.pa",
        "com.pr",
        "com.cu",
        "co.cr",
        "co.jp",
        "co.in",
        "com.cn",
        "com.sg",
        "com.hk",
        "com.tw",
        "co.kr",
        "com.ph",
    ]
)
#: Hosts where each subdomain is a different tenant, so the tenant is the registrable part.
HOSTED_SITE_SUFFIXES: Final = frozenset(
    [
        "wixsite.com",
        "squarespace.com",
        "godaddysites.com",
        "business.site",
        "weebly.com",
        "wordpress.com",
        "blogspot.com",
        "myshopify.com",
        "square.site",
        "carrd.co",
        "github.io",
        "netlify.app",
        "vercel.app",
        "webflow.io",
        "site123.me",
        "jimdosite.com",
        "webnode.com",
        "yolasite.com",
        "ueniweb.com",
        "mystrikingly.com",
    ]
)
#: A URL on these is a profile or listing page, not the business's own site.
PROFILE_PLATFORMS: Final = frozenset(
    [
        "facebook.com",
        "fb.com",
        "instagram.com",
        "linkedin.com",
        "x.com",
        "twitter.com",
        "tiktok.com",
        "youtube.com",
        "youtu.be",
        "yelp.com",
        "google.com",
        "goo.gl",
        "g.page",
        "linktr.ee",
        "nextdoor.com",
        "bbb.org",
        "yellowpages.com",
        "angi.com",
        "houzz.com",
        "thumbtack.com",
        "mapquest.com",
        "manta.com",
        "wa.me",
        "whatsapp.com",
        "pinterest.com",
    ]
)
TRACKING_PARAMETERS: Final = frozenset(
    [
        "gclid",
        "dclid",
        "gbraid",
        "wbraid",
        "fbclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "_ga",
        "_gl",
        "igshid",
        "yclid",
        "ttclid",
        "twclid",
        "li_fat_id",
        "s_kwcid",
        "ref",
        "ref_src",
        "srsltid",
    ]
)
_DEFAULT_DOCUMENTS: Final = re.compile(r"/(?:index|default|home)\.(?:html?|php|aspx?|jsp)$", re.I)
_SCHEME: Final = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)


@dataclass(frozen=True, slots=True)
class WebsiteResult:
    raw: str | None
    #: Scheme and host lower-cased, `www.`, default port, default document, fragment, tracking
    #: parameters and the trailing slash removed; remaining parameters sorted.
    url: str | None
    host: str | None
    registrable_domain: str | None
    subdomain: str | None
    #: A profile or listing page (Facebook, Yelp...): its domain does not identify the business.
    is_platform: bool
    status: Presence


def registrable_domain(host: str) -> str:
    labels = host.split(".")
    for width in (3, 2):
        suffix = ".".join(labels[-width:])
        if len(labels) > width and suffix in HOSTED_SITE_SUFFIXES:
            return ".".join(labels[-width - 1 :])
    if len(labels) >= 3 and ".".join(labels[-2:]) in MULTI_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def website(value: object) -> WebsiteResult:
    text, status = clean_text(value)
    raw = value if isinstance(value, str) else None
    if text is None:
        return WebsiteResult(raw, None, None, None, None, False, status)
    if not _SCHEME.match(text):
        if "@" in text or " " in text:
            return WebsiteResult(raw, None, None, None, None, False, Presence.INVALID)
        text = f"https://{text}"
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return WebsiteResult(raw, None, None, None, None, False, Presence.INVALID)
    scheme = parts.scheme.lower()
    host = ascii_host(parts.hostname or "") if scheme in ("http", "https") else None
    if host is None:
        return WebsiteResult(raw, None, None, None, None, False, Presence.INVALID)
    host = host.removeprefix("www.")
    if host.count(".") == 0:
        return WebsiteResult(raw, None, None, None, None, False, Presence.INVALID)
    netloc = host if port in (None, 80 if scheme == "http" else 443) else f"{host}:{port}"
    path = _DEFAULT_DOCUMENTS.sub("/", re.sub(r"/{2,}", "/", parts.path or "/"))
    path = path.rstrip("/") or ""
    query = sorted(
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(("utm_", "hsa_")) and key.lower() not in TRACKING_PARAMETERS
    )
    url = f"{scheme}://{netloc}{path}" + (f"?{urlencode(query)}" if query else "")
    registrable = registrable_domain(host)
    subdomain = host[: -len(registrable) - 1] if host != registrable else None
    return WebsiteResult(raw, url, host, registrable, subdomain, registrable in PROFILE_PLATFORMS,
                         Presence.PRESENT)  # fmt: skip


# ---- social profiles ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SocialResult:
    raw: str | None
    platform: str | None
    #: Stable handle on that platform (`company/acme`, `@acme`, `id:1234`), lower-cased where the
    #: platform treats handles case-insensitively.
    handle: str | None
    url: str | None
    status: Presence


_PLATFORM_HOSTS: Final = {
    "facebook.com": "facebook", "fb.com": "facebook", "instagram.com": "instagram",
    "x.com": "x", "twitter.com": "x", "linkedin.com": "linkedin", "youtube.com": "youtube",
    "youtu.be": "youtube", "tiktok.com": "tiktok", "yelp.com": "yelp", "wa.me": "whatsapp",
    "whatsapp.com": "whatsapp", "pinterest.com": "pinterest", "nextdoor.com": "nextdoor",
}  # fmt: skip
_NOT_A_PROFILE: Final = frozenset(
    [
        "sharer",
        "sharer.php",
        "share",
        "dialog",
        "plugins",
        "intent",
        "home",
        "search",
        "hashtag",
        "explore",
        "accounts",
        "p",
        "reel",
        "reels",
        "stories",
        "watch",
        "events",
        "groups",
        "login",
        "signup",
        "tr",
        "i",
        "send",
    ]
)


def social_profile(value: object) -> SocialResult:
    site = website(value)
    raw = site.raw
    if site.status is not Presence.PRESENT or site.registrable_domain is None:
        return SocialResult(raw, None, None, None, site.status)
    platform = _PLATFORM_HOSTS.get(site.registrable_domain)
    if platform is None:
        return SocialResult(raw, None, None, None, Presence.NOT_APPLICABLE)
    parts = urlsplit(site.url or "")
    segments = [segment for segment in parts.path.split("/") if segment]
    query = dict(parse_qsl(parts.query))
    handle: str | None = None
    if platform == "facebook" and segments[:1] == ["profile.php"] and query.get("id", "").isdigit():
        handle = f"id:{query['id']}"
    elif platform == "facebook" and segments[:1] == ["pages"] and len(segments) >= 3:
        handle = f"id:{segments[2]}" if segments[2].isdigit() else segments[1].lower()
    elif platform == "linkedin" and len(segments) >= 2 and segments[0] in ("company", "in"):
        handle = f"{segments[0]}/{segments[1].lower()}"
    elif platform == "youtube" and len(segments) >= 2 and segments[0] in ("channel", "c", "user"):
        handle = f"{segments[0]}/{segments[1]}" if segments[0] == "channel" else segments[1].lower()
    elif platform == "yelp" and len(segments) >= 2 and segments[0] == "biz":
        handle = f"biz/{segments[1].lower()}"
    elif platform == "whatsapp" and segments and segments[0].isdigit():
        handle = segments[0]
    elif segments and segments[0].lower() not in _NOT_A_PROFILE and platform != "linkedin":
        handle = segments[0].lower()
    if handle is None:
        return SocialResult(raw, platform, None, None, Presence.NOT_APPLICABLE)
    host = {"x": "x.com", "whatsapp": "wa.me"}.get(platform, f"{platform}.com")
    path = handle.replace("id:", "profile.php?id=") if handle.startswith("id:") else handle
    return SocialResult(raw, platform, handle, f"https://{host}/{path}", Presence.PRESENT)


# ---- places ------------------------------------------------------------------------------------

US_REGIONS: Final = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california",
    "CO": "colorado", "CT": "connecticut", "DE": "delaware", "DC": "district of columbia",
    "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho", "IL": "illinois",
    "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky", "LA": "louisiana",
    "ME": "maine", "MD": "maryland", "MA": "massachusetts", "MI": "michigan", "MN": "minnesota",
    "MS": "mississippi", "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico", "NY": "new york",
    "NC": "north carolina", "ND": "north dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon",
    "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina", "SD": "south dakota",
    "TN": "tennessee", "TX": "texas", "UT": "utah", "VT": "vermont", "VA": "virginia",
    "WA": "washington", "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming",
    "PR": "puerto rico", "VI": "virgin islands", "GU": "guam", "AS": "american samoa",
    "MP": "northern mariana islands",
}  # fmt: skip
_REGION_BY_NAME: Final = {name: code for code, name in US_REGIONS.items()}
_PLACE_PREFIXES: Final = {"st": "saint", "ste": "sainte", "ft": "fort", "mt": "mount", "pt": "port"}


def us_region(value: object) -> tuple[str | None, Presence]:
    """USPS two-letter code of a US state, district or territory, from its code or its name."""
    text, status = clean_text(value)
    if text is None:
        return None, status
    folded = fold(text)
    if folded.upper() in US_REGIONS:
        return folded.upper(), Presence.PRESENT
    code = _REGION_BY_NAME.get(folded)
    return (code, Presence.PRESENT) if code else (None, Presence.INVALID)


@dataclass(frozen=True, slots=True)
class PlaceResult:
    raw: str | None
    display: str | None
    key: str | None
    status: Presence


def _place(value: object, drop_suffix: re.Pattern[str] | None = None) -> PlaceResult:
    text, status = clean_text(value)
    raw = value if isinstance(value, str) else None
    if text is None:
        return PlaceResult(raw, None, None, status)
    display = text.title() if text.isupper() or text.islower() else text
    if drop_suffix is not None:
        display = drop_suffix.sub("", display).strip() or display
    tokens = fold(display).split()
    if tokens and tokens[0] in _PLACE_PREFIXES:
        tokens[0] = _PLACE_PREFIXES[tokens[0]]
    key = " ".join(tokens)
    return PlaceResult(raw, display, key or None, Presence.PRESENT if key else Presence.INVALID)


def locality(value: object) -> PlaceResult:
    return _place(value)


_COUNTY: Final = re.compile(r"\s+(?:county|parish|borough|census area|municipality|co\.?)$", re.I)
_METRO: Final = re.compile(r"\s+(?:metro(?:politan)?(?: area)?|msa|metro area)$", re.I)


def county(value: object) -> PlaceResult:
    return _place(value, _COUNTY)


def metro(value: object) -> PlaceResult:
    return _place(value, _METRO)


# ---- US addresses ------------------------------------------------------------------------------

#: USPS Publication 28 abbreviations for the suffixes and directionals seen most often.
STREET_SUFFIXES: Final = {
    "street": "St", "st": "St", "avenue": "Ave", "ave": "Ave", "av": "Ave", "boulevard": "Blvd",
    "blvd": "Blvd", "road": "Rd", "rd": "Rd", "drive": "Dr", "dr": "Dr", "lane": "Ln", "ln": "Ln",
    "court": "Ct", "ct": "Ct", "place": "Pl", "pl": "Pl", "parkway": "Pkwy", "pkwy": "Pkwy",
    "highway": "Hwy", "hwy": "Hwy", "circle": "Cir", "cir": "Cir", "terrace": "Ter", "ter": "Ter",
    "trail": "Trl", "trl": "Trl", "way": "Way", "square": "Sq", "sq": "Sq", "expressway": "Expy",
    "freeway": "Fwy", "center": "Ctr", "plaza": "Plz", "point": "Pt", "loop": "Loop",
    "pike": "Pike", "alley": "Aly", "crossing": "Xing", "heights": "Hts", "junction": "Jct",
}  # fmt: skip
DIRECTIONALS: Final = {
    "north": "N", "n": "N", "south": "S", "s": "S", "east": "E", "e": "E", "west": "W", "w": "W",
    "northeast": "NE", "ne": "NE", "northwest": "NW", "nw": "NW", "southeast": "SE", "se": "SE",
    "southwest": "SW", "sw": "SW",
}  # fmt: skip
UNIT_DESIGNATORS: Final = {
    "suite": "Ste", "ste": "Ste", "apartment": "Apt", "apt": "Apt", "unit": "Unit",
    "room": "Rm", "rm": "Rm", "floor": "Fl", "fl": "Fl", "building": "Bldg", "bldg": "Bldg",
    "space": "Spc", "spc": "Spc", "lot": "Lot", "#": "#",
}  # fmt: skip
_UNIT: Final = re.compile(
    r"(?:,\s*|\s+)(?P<designator>suite|ste|apartment|apt|unit|room|rm|floor|fl|building|bldg|"
    r"space|spc|lot|#)\.?\s*(?P<identifier>[A-Za-z0-9-]{1,10})\s*$",
    re.I,
)
_STATE_ZIP: Final = re.compile(
    r"^(?P<region>[A-Za-z .]{2,30}?)\s*(?P<zip>\d{4,5})(?:-(?P<zip4>\d{4}))?$"
)
_ZIP: Final = re.compile(r"^(?P<zip>\d{4,5})(?:-(?P<zip4>\d{4}))?$")
#: A unit written as a comma-separated part of its own: "100 Example Ave, Ste 4, Austin, TX".
_UNIT_PART: Final = re.compile(
    r"^(?:suite|ste|apartment|apt|unit|room|rm|floor|fl|building|bldg|space|spc|lot|#)\.?\s*"
    r"[A-Za-z0-9-]{1,10}$",
    re.I,
)
_COUNTRY: Final = re.compile(r"^(?:usa|us|u\.s\.a?\.?|united states(?: of america)?)$", re.I)


@dataclass(frozen=True, slots=True)
class AddressResult:
    raw: str | None
    #: `100 N Example Ave`: USPS abbreviations, the rest as written.
    street: str | None
    street_number: str | None
    #: Folded street name without the number: `n example ave`.
    street_key: str | None
    unit: str | None
    #: The unit identifier alone (`200` for `Ste 200` and `#200`).
    unit_key: str | None
    locality: str | None
    locality_key: str | None
    region: str | None
    postal_code: str | None
    zip4: str | None
    country_code: str | None
    latitude: float | None
    longitude: float | None
    status: Presence


def _postal(value: object) -> tuple[str | None, str | None]:
    text, _ = clean_text(value)
    if text is None or (match := _ZIP.match(text.replace(" ", ""))) is None:
        return None, None
    # a four-digit ZIP is a five-digit one whose leading zero a spreadsheet dropped (NJ, MA...)
    return match.group("zip").zfill(5), match.group("zip4")


def _street(text: str) -> tuple[str, str | None, str | None]:
    tokens = text.replace(",", " ").split()
    shown: list[str] = []
    for index, token in enumerate(tokens):
        lowered = token.casefold().strip(".")
        if lowered in DIRECTIONALS and len(tokens) > 2:
            shown.append(DIRECTIONALS[lowered])
        elif lowered in STREET_SUFFIXES and index > 0:
            shown.append(STREET_SUFFIXES[lowered])
        else:
            shown.append(token)
    street = " ".join(shown)
    number = shown[0] if shown and re.fullmatch(r"\d+[A-Za-z]?(?:-\d+)?", shown[0]) else None
    rest = shown[1:] if number else shown
    return street, number, fold(" ".join(rest)) or None


def us_address(
    raw: object = None,
    *,
    street: object = None,
    unit: object = None,
    locality_value: object = None,
    region: object = None,
    postal_code: object = None,
    country_code: object = None,
    latitude: object = None,
    longitude: object = None,
) -> AddressResult:
    """A US address from a one-line form, separate components, or both (components win).

    The parser is deliberately conservative: a part it cannot place stays in `street` rather than
    being guessed into a city or a state.
    """
    text, raw_status = clean_text(raw)
    parsed: dict[str, str | None] = {}
    if text is not None:
        parts = [part.strip() for part in text.split(",") if part.strip()]
        if parts and _COUNTRY.match(parts[-1]):
            parsed["country"] = "US"
            parts.pop()
        if parts and (match := _STATE_ZIP.match(parts[-1])) and us_region(match["region"])[0]:
            parsed |= {"region": match["region"], "zip": match["zip"], "zip4": match["zip4"]}
            parts.pop()
        elif parts and (match := _ZIP.match(parts[-1])):
            parsed |= {"zip": match["zip"], "zip4": match["zip4"]}
            parts.pop()
            if parts and us_region(parts[-1])[0]:
                parsed["region"] = parts.pop()
        elif parts and len(parts) > 1 and us_region(parts[-1])[0]:
            parsed["region"] = parts.pop()
        placed = "region" in parsed or "zip" in parsed

        def take_unit() -> None:
            if len(parts) > 1 and _UNIT_PART.match(parts[-1]):
                parsed["unit"] = parts.pop()

        take_unit()  # "100 Example Ave, Ste 4"
        # "Austin, TX": what is left before a state is a city, unless it starts like a street
        if len(parts) > 1 or (len(parts) == 1 and placed and not parts[0][:1].isdigit()):
            parsed["locality"] = parts.pop()
        take_unit()  # "100 Example Ave, Ste 4, Austin, TX"
        if parts:
            parsed["street"] = ", ".join(parts)
    street_text, _ = clean_text(street if street is not None else parsed.get("street"))
    unit_text, _ = clean_text(unit if unit is not None else parsed.get("unit"))
    if street_text is not None and unit_text is None and (match := _UNIT.search(street_text)):
        unit_text = f"{match['designator']} {match['identifier']}"
        street_text = street_text[: match.start()].strip(" ,") or None
    shown_street, number, street_key = _street(street_text) if street_text else (None, None, None)
    unit_shown = unit_key = None
    if unit_text is not None:
        pieces = unit_text.replace("#", "# ").split(maxsplit=1)
        designator = UNIT_DESIGNATORS.get(pieces[0].casefold().strip("."))
        identifier = pieces[1] if designator and len(pieces) > 1 else unit_text
        unit_shown = f"{designator} {identifier}" if designator else identifier
        unit_key = fold(identifier).replace(" ", "") or None
    place = locality(locality_value if locality_value is not None else parsed.get("locality"))
    region_code, _ = us_region(region if region is not None else parsed.get("region"))
    zip5, zip4 = _postal(postal_code if postal_code is not None else parsed.get("zip"))
    if zip4 is None and parsed.get("zip4"):
        zip4 = parsed["zip4"]
    country, _ = clean_text(country_code if country_code is not None else parsed.get("country"))
    if country is not None:
        country = "US" if _COUNTRY.match(country) else country.upper()
        country = country if re.fullmatch(r"[A-Z]{2}", country) else None
    lat = _coordinate(latitude, 90)
    lon = _coordinate(longitude, 180)
    if lat is None or lon is None:
        lat = lon = None
    found = any((shown_street, place.display, region_code, zip5, lat is not None))
    missing = raw_status if text is None else Presence.INVALID
    status = Presence.PRESENT if found else missing
    return AddressResult(
        raw=raw if isinstance(raw, str) else None,
        street=shown_street,
        street_number=number,
        street_key=street_key,
        unit=unit_shown,
        unit_key=unit_key,
        locality=place.display,
        locality_key=place.key,
        region=region_code,
        postal_code=zip5,
        zip4=zip4,
        country_code=country,
        latitude=lat,
        longitude=lon,
        status=status,
    )


def _coordinate(value: object, bound: float) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value))
    except ValueError:
        return None
    return number if -bound <= number <= bound else None


# ---- categories --------------------------------------------------------------------------------

#: Canonical vocabulary, versioned by TAXONOMY_VERSION. Aliases are folded (no accents, lower-case);
#: the longest alias found as whole words wins, so "plumbing heating and air conditioning
#: contractors" is plumbing and not HVAC. Source labels are always kept next to the key.
TAXONOMY: Final = {
    "bakery": ("bakery", "bakeries", "panaderia", "pasteleria", "bake shop", "commercial bakeries",
               "retail bakeries"),
    "restaurant": ("restaurant", "restaurants", "restaurante", "taqueria", "diner", "eatery",
                   "full service restaurants", "limited service restaurants", "cafe"),
    "grocery": ("grocery", "groceries", "supermarket", "supermarkets", "mercado", "carniceria",
                "tienda de abarrotes", "grocery stores", "convenience store"),
    "auto_repair": ("auto repair", "automotive repair", "mechanic", "car repair", "taller mecanico",
                    "auto body", "collision repair", "automotive mechanical and electrical repair"),
    "dental": ("dental", "dentist", "dentists", "dental clinic", "orthodontist",
               "offices of dentists"),
    "medical_clinic": ("medical clinic", "physician", "urgent care", "offices of physicians",
                       "clinica medica"),
    "landscaping": ("landscaping", "landscaper", "lawn care", "lawn service", "jardineria",
                    "landscaping services"),
    "cleaning": ("cleaning", "cleaning services", "janitorial", "janitorial services",
                 "maid service", "limpieza", "housekeeping"),
    "roofing": ("roofing", "roofer", "roofing contractors", "techos"),
    "plumbing": ("plumbing", "plumber", "plomeria",
                 "plumbing heating and air conditioning contractors"),
    "electrical": ("electrician", "electrical contractor", "electrical contractors",
                   "electrical services", "electricista"),
    "hvac": ("hvac", "heating and air", "air conditioning", "heating and cooling"),
    "construction": ("construction", "general contractor", "builders", "construccion",
                     "residential building construction",
                     "commercial and institutional building construction"),
    "painting": ("painting", "painter", "painting contractors", "pintura"),
    "tax_accounting": ("tax preparation", "tax services", "accounting", "accountant",
                       "bookkeeping", "cpa", "tax preparation services", "contabilidad",
                       "impuestos"),
    "legal": ("law firm", "lawyer", "attorney", "abogado", "abogados", "offices of lawyers"),
    "insurance": ("insurance", "insurance agency", "seguros",
                  "insurance agencies and brokerages"),
    "real_estate": ("real estate", "realtor", "realty", "bienes raices",
                    "offices of real estate agents and brokers"),
    "beauty_salon": ("beauty salon", "hair salon", "barber", "barbershop", "estetica", "barberia",
                     "nail salon", "beauty salons"),
    "trucking": ("trucking", "freight", "general freight trucking", "logistics"),
    "staffing": ("staffing", "employment agency", "temporary help services"),
    "it_services": ("it services", "computer services", "web design",
                    "computer systems design services"),
    "security": ("security services", "security guards", "guard services",
                 "investigation guard and armored car services"),
    "manufacturing": ("manufacturing", "manufacturer", "fabrication"),
}  # fmt: skip
_ALIASES: Final = sorted(
    ((alias, key) for key, aliases in TAXONOMY.items() for alias in aliases),
    key=lambda item: (-len(item[0]), item[0]),
)


@dataclass(frozen=True, slots=True)
class CategoryResult:
    raw: str | None
    label: str | None
    taxonomy_key: str | None
    taxonomy_version: str | None
    status: Presence


def category(value: object) -> CategoryResult:
    text, status = clean_text(value)
    raw = value if isinstance(value, str) else None
    if text is None:
        return CategoryResult(raw, None, None, None, status)
    folded = f" {fold(text)} "
    for alias, key in _ALIASES:
        if f" {alias} " in folded:
            return CategoryResult(raw, text, key, TAXONOMY_VERSION, Presence.PRESENT)
    return CategoryResult(raw, text, None, None, Presence.PRESENT)
