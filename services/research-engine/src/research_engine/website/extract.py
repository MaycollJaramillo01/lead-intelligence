"""Website intelligence facts (phase 08): what one acquired page shows, read again from its stored
bytes with a versioned extractor.

Pure and deterministic: the same HTML always gives the same facts, in document order. Nothing here
judges the facts (rules.py does), and nothing is fetched, rendered, clicked or submitted: forms are
only read. Technologies are matched by documented signatures on what the page itself carries, so a
technology that leaves no trace in the HTML is not detected - which never means it is absent.
"""

import itertools
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

import orjson
from selectolax.lexbor import LexborHTMLParser, LexborNode

from research_engine.crawl.urls import canonical, site_of
from research_engine.domain import sort_key
from research_engine.latino.rules import spanish_share

EXTRACTOR_VERSION: Final = "1.0.0"
MAX_LINKS: Final = 300
MAX_ITEMS: Final = 20
MAX_HEADINGS: Final = 60
MAX_RAW_HTML: Final = 3_000_000
MAX_INLINE_SCRIPT: Final = 400_000
SNIPPET: Final = 160


# ---- signatures: technologies and widgets ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Signature:
    """How a technology shows in a page. Each pattern is a regex over one kind of trace; a named
    group `version` captures a version when the trace carries one."""

    id: str
    name: str
    category: str
    script: str | None = None  # script src attributes
    inline: str | None = None  # inline script text
    markup: str | None = None  # the raw HTML
    meta: str | None = None  # <meta name="generator">
    frame: str | None = None  # iframe src attributes
    link: str | None = None  # anchor href attributes


SIGNATURES: Final[tuple[Signature, ...]] = (
    # site builders and CMS
    Signature("wordpress", "WordPress", "cms", markup=r"/wp-(?:content|includes)/",
              meta=r"^WordPress(?:\s+(?P<version>[\d.]+))?"),
    Signature("wix", "Wix", "cms", markup=r"static\.wixstatic\.com|wix-bolt|_wixCIDX",
              meta=r"^Wix\.com"),
    Signature("squarespace", "Squarespace", "cms",
              markup=r"static1\.squarespace\.com|Static\.SQUARESPACE_CONTEXT", meta=r"Squarespace"),
    Signature("shopify", "Shopify", "ecommerce", script=r"cdn\.shopify\.com",
              markup=r"Shopify\.theme|cdn\.shopify\.com"),
    Signature("webflow", "Webflow", "cms", markup=r"data-wf-site=|webflow\.js", meta=r"^Webflow"),
    Signature("godaddy_builder", "GoDaddy Website Builder", "cms",
              markup=r"img\d?\.wsimg\.com", meta=r"Starfield|GoDaddy"),
    Signature("weebly", "Weebly", "cms", markup=r"editmysite\.com|weebly\.com"),
    Signature("duda", "Duda", "cms", markup=r"irp(?:-cdn)?\.multiscreensite\.com|dmAPI"),
    Signature("joomla", "Joomla", "cms", meta=r"^Joomla"),
    Signature("drupal", "Drupal", "cms", meta=r"^Drupal(?:\s+(?P<version>[\d.]+))?",
              markup=r"/sites/default/files/"),
    Signature("woocommerce", "WooCommerce", "ecommerce", markup=r"woocommerce"),
    # frameworks and libraries
    Signature("nextjs", "Next.js", "framework", markup=r"__NEXT_DATA__|/_next/static/"),
    Signature("nuxt", "Nuxt", "framework", markup=r"__NUXT__|/_nuxt/"),
    Signature("gatsby", "Gatsby", "framework", markup=r"___gatsby"),
    Signature("angular", "Angular", "framework", markup=r'ng-version="(?P<version>[\d.]+)"'),
    Signature("react", "React", "framework",
              markup=r"data-reactroot|react(?:-dom)?(?:\.production)?(?:\.min)?\.js"),
    Signature("jquery", "jQuery", "library",
              script=r"jquery[.-]?(?P<version>\d+\.\d+(?:\.\d+)?)?(?:\.min)?\.js"),
    # analytics and tag management
    Signature("google_analytics_4", "Google Analytics 4", "analytics",
              script=r"googletagmanager\.com/gtag/js\?id=G-[A-Z0-9]+",
              inline=r"gtag\(\s*['\"]config['\"]\s*,\s*['\"]G-[A-Z0-9]+"),
    Signature("universal_analytics", "Universal Analytics", "analytics",
              script=r"google-analytics\.com/(?:analytics|ga)\.js",
              inline=r"\bUA-\d{4,10}-\d{1,4}\b"),
    Signature("google_tag_manager", "Google Tag Manager", "tag_manager",
              script=r"googletagmanager\.com/gtm\.js", inline=r"\bGTM-[A-Z0-9]{4,}\b",
              frame=r"googletagmanager\.com/ns\.html"),
    Signature("microsoft_clarity", "Microsoft Clarity", "analytics", script=r"clarity\.ms/tag",
              inline=r"clarity\.ms/tag"),
    Signature("hotjar", "Hotjar", "analytics", script=r"static\.hotjar\.com",
              inline=r"static\.hotjar\.com"),
    Signature("matomo", "Matomo", "analytics", script=r"matomo\.js|piwik\.js",
              inline=r"_paq\.push"),
    Signature("plausible", "Plausible", "analytics", script=r"plausible\.io/js"),
    # advertising and call tracking
    Signature("meta_pixel", "Meta Pixel", "advertising",
              script=r"connect\.facebook\.net/[^\"']*/fbevents\.js",
              inline=r"fbq\(\s*['\"]init['\"]"),
    Signature("google_ads", "Google Ads", "advertising",
              script=r"googletagmanager\.com/gtag/js\?id=AW-\d+", inline=r"\bAW-\d{6,}\b"),
    Signature("tiktok_pixel", "TikTok Pixel", "advertising", script=r"analytics\.tiktok\.com",
              inline=r"analytics\.tiktok\.com"),
    Signature("linkedin_insight", "LinkedIn Insight Tag", "advertising",
              script=r"snap\.licdn\.com/li\.lms-analytics", inline=r"_linkedin_partner_id"),
    Signature("microsoft_ads", "Microsoft Advertising UET", "advertising", script=r"bat\.bing\.com",
              inline=r"bat\.bing\.com"),
    Signature("callrail", "CallRail", "call_tracking", script=r"cdn\.callrail\.com"),
    # booking and scheduling
    Signature("calendly", "Calendly", "booking", script=r"calendly\.com", frame=r"calendly\.com",
              link=r"calendly\.com/"),
    Signature("acuity", "Acuity Scheduling", "booking",
              script=r"acuityscheduling\.com|squarespacescheduling\.com",
              frame=r"acuityscheduling\.com|squarespacescheduling\.com",
              link=r"acuityscheduling\.com|squarespacescheduling\.com"),
    Signature("square_appointments", "Square Appointments", "booking",
              link=r"squareup\.com/appointments|square\.site/book",
              frame=r"squareup\.com/appointments"),
    Signature("housecall_pro", "Housecall Pro", "booking", script=r"housecallpro\.com",
              link=r"book\.housecallpro\.com"),
    Signature("jobber", "Jobber", "booking", script=r"getjobber\.com", link=r"getjobber\.com"),
    Signature("servicetitan", "ServiceTitan", "booking", script=r"servicetitan\.com",
              link=r"servicetitan\.com"),
    Signature("setmore", "Setmore", "booking", link=r"setmore\.com", frame=r"setmore\.com"),
    Signature("vagaro", "Vagaro", "booking", link=r"vagaro\.com", frame=r"vagaro\.com"),
    Signature("booksy", "Booksy", "booking", link=r"booksy\.com", frame=r"booksy\.com"),
    Signature("mindbody", "Mindbody", "booking", link=r"mindbodyonline\.com",
              frame=r"mindbodyonline\.com"),
    Signature("opentable", "OpenTable", "booking", link=r"opentable\.com",
              script=r"opentable\.com/widget"),
    Signature("zocdoc", "Zocdoc", "booking", link=r"zocdoc\.com", script=r"zocdoc\.com"),
    # chat and messaging
    Signature("intercom", "Intercom", "chat", script=r"widget\.intercom\.io|intercomcdn\.com",
              inline=r"widget\.intercom\.io"),
    Signature("drift", "Drift", "chat", script=r"js\.driftt\.com", inline=r"js\.driftt\.com"),
    Signature("tawk", "tawk.to", "chat", script=r"embed\.tawk\.to", inline=r"embed\.tawk\.to"),
    Signature("livechat", "LiveChat", "chat", script=r"cdn\.livechatinc\.com",
              inline=r"cdn\.livechatinc\.com"),
    Signature("zendesk_chat", "Zendesk Chat", "chat", script=r"static\.zdassets\.com|zopim",
              inline=r"static\.zdassets\.com|zopim"),
    Signature("hubspot", "HubSpot", "chat", script=r"js\.hs-scripts\.com|js\.usemessages\.com"),
    Signature("tidio", "Tidio", "chat", script=r"code\.tidio\.co"),
    Signature("crisp", "Crisp", "chat", inline=r"client\.crisp\.chat",
              script=r"client\.crisp\.chat"),
    Signature("podium", "Podium", "chat", script=r"connect\.podium\.com"),
    Signature("facebook_chat", "Facebook Customer Chat", "chat", markup=r"fb-customerchat"),
    # forms
    Signature("hubspot_forms", "HubSpot Forms", "forms", script=r"js\.hsforms\.net"),
    Signature("typeform", "Typeform", "forms", script=r"embed\.typeform\.com",
              frame=r"typeform\.com", link=r"typeform\.com/to/"),
    Signature("jotform", "Jotform", "forms", script=r"jotform\.(?:com|us)",
              frame=r"jotform\.(?:com|us)"),
    Signature("gravity_forms", "Gravity Forms", "forms", markup=r"gform_wrapper"),
    Signature("contact_form_7", "Contact Form 7", "forms", markup=r"wpcf7"),
    Signature("wpforms", "WPForms", "forms", markup=r"wpforms-form"),
    Signature("google_forms", "Google Forms", "forms", frame=r"docs\.google\.com/forms",
              link=r"docs\.google\.com/forms|forms\.gle/"),
    # reviews and maps
    Signature("elfsight", "Elfsight widgets", "reviews", script=r"apps\.elfsight\.com"),
    Signature("trustindex", "Trustindex", "reviews", script=r"cdn\.trustindex\.io"),
    Signature("birdeye", "Birdeye", "reviews", script=r"birdeye\.com"),
    Signature("google_maps", "Google Maps", "maps",
              frame=r"google\.[a-z.]+/maps/embed|maps\.google\.", script=r"maps\.googleapis\.com"),
    Signature("mapbox", "Mapbox", "maps", script=r"api\.mapbox\.com"),
    # captcha
    Signature("recaptcha", "reCAPTCHA", "security", script=r"google\.com/recaptcha",
              markup=r"g-recaptcha"),
    Signature("hcaptcha", "hCaptcha", "security", script=r"hcaptcha\.com", markup=r"h-captcha"),
    Signature("turnstile", "Cloudflare Turnstile", "security",
              script=r"challenges\.cloudflare\.com/turnstile", markup=r"cf-turnstile"),
)  # fmt: skip

TRACES: Final = ("script", "inline", "markup", "meta", "frame", "link")
_COMPILED: Final = tuple(
    (
        signature,
        {
            trace: re.compile(pattern, re.IGNORECASE)
            for trace in TRACES
            if (pattern := getattr(signature, trace)) is not None
        },
    )
    for signature in SIGNATURES
)
SIGNATURE_BY_ID: Final = {signature.id: signature for signature in SIGNATURES}


# ---- lexicons (English and Spanish, matched on accent-folded lower-case text) -------------------

CTA_KINDS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = tuple(
    (kind, re.compile(pattern))
    for kind, pattern in (
        ("call", r"\b(?:call(?: us)?(?: now| today)?|llam[ae]n?(?:nos)?(?: ahora| hoy)?|"
                 r"phone us)\b"),
        ("quote", r"\b(?:(?:get|request|free)(?: an?| your)?(?: free)? (?:quote|estimate)s?|"
                  r"cotiza\w*|presupuesto\w*|estimado gratis)\b"),
        ("booking", r"\b(?:book(?: now| online| an appointment| a visit)?|schedule(?: now| online|"
                    r" (?:a|an|your) (?:service|appointment|visit|consultation|call))?|"
                    r"make an appointment|request an appointment|reserv[ae]r?|agend[ae]r?|"
                    r"(?:haga|pida|solicite) (?:una )?cita)\b"),
        ("contact", r"\b(?:contact us|get in touch|send (?:us )?a message|message us|"
                    r"contactenos|contactanos|escribenos)\b"),
        ("buy", r"\b(?:shop now|buy now|order (?:now|online)|add to cart|comprar|ordenar)\b"),
        ("directions", r"\b(?:get directions|directions|como llegar)\b"),
    )
)  # fmt: skip

PAGE_KINDS: Final[dict[str, tuple[str, ...]]] = {
    "about": ("about", "about us", "our story", "who we are", "our team", "nosotros",
              "quienes somos", "sobre nosotros"),
    "contact": ("contact", "contact us", "get in touch", "contacto", "contactenos", "contactanos"),
    "services": ("services", "our services", "what we do", "servicios", "nuestros servicios"),
    "faq": ("faq", "faqs", "frequently asked questions", "preguntas frecuentes"),
    "privacy": ("privacy", "privacy policy", "politica de privacidad", "aviso de privacidad"),
    "terms": ("terms", "terms of service", "terms of use", "terms and conditions",
              "terminos y condiciones", "terminos"),
    "locations": ("locations", "our locations", "service areas", "areas we serve", "ubicaciones",
                  "sucursales"),
    "reviews": ("reviews", "testimonials", "resenas", "testimonios"),
}  # fmt: skip
_KIND_PATTERNS: Final = {
    kind: "|".join(re.escape(term) for term in terms) for kind, terms in PAGE_KINDS.items()
}

TRUST_SIGNALS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = tuple(
    (kind, re.compile(pattern))
    for kind, pattern in (
        ("testimonials", r"\b(?:testimonials?|what (?:our )?(?:customers|clients) "
                         r"(?:say|are saying)|customer reviews|client reviews|testimonios|resenas|"
                         r"opiniones de (?:nuestros )?clientes)\b"),
        ("license", r"\b(?:licensed|licen[cs]e (?:#|no\.?|number)|lic\.? ?#|licenciad[oa]s?)\b"),
        ("insured", r"\b(?:fully insured|insured|bonded|asegurad[oa]s?)\b"),
        ("guarantee", r"\b(?:guarantee[sd]?|warrant(?:y|ies)|garantia|garantizad[oa]s?)\b"),
        ("portfolio", r"\b(?:our work|portfolio|our projects|before (?:and|&) after|gallery|"
                      r"proyectos|galeria|nuestros trabajos)\b"),
        ("experience", r"\b(?:since (?:19|20)\d{2}|\d{1,3}\+? years (?:of experience|in business)|"
                       r"desde (?:19|20)\d{2}|\d{1,3} anos de experiencia)\b"),
        ("award", r"\b(?:award[- ]winning|best of \w+|accredited business|a\+ rating|"
                  r"certified|certificad[oa]s?)\b"),
    )
)  # fmt: skip

_DAY: Final = (
    r"(?:mon|tue|wed|thu|fri|sat|sun|lunes|martes|miercoles|jueves|viernes|sabado|domingo)"
)
_TIME: Final = r"\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?"
_HOURS: Final = re.compile(
    rf"\b(?:{_DAY}[a-z]*\.?\s*(?:-|to|a|through|thru)\s*{_DAY}[a-z]*|{_TIME}\s*(?:-|to|a)\s*{_TIME}|"
    r"open 24 hours|24/7|abierto 24 horas)"
)
_SERVICE_AREA: Final = re.compile(
    r"\b(?:proudly serving|we proudly serve|we serve|service areas?|areas we serve|"
    r"serving [^.!?]{0,60}\b(?:area|areas|county|counties|and surrounding|metro)\b|servimos a|"
    r"areas? de servicio|zonas? de servicio)[^.!?]{0,120}"
)
_STREET: Final = re.compile(
    r"\b\d{2,6}\s+(?:[A-Za-z0-9][\w.'-]*\s+){0,4}(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|"
    r"Dr|Drive|Ln|Lane|Way|Ct|Court|Pkwy|Parkway|Hwy|Highway|Pl|Place|Ter|Terrace|Cir|Circle)\b\.?"
)
_STATE_ZIP: Final = re.compile(r"\b[A-Z]{2},?\s+\d{5}(?:-\d{4})?\b")
_PHONE: Final = re.compile(r"(?<![\w+])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
_EMAIL: Final = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z]{2,24})+")
_NOT_AN_EMAIL_TLD: Final = frozenset({"png", "jpg", "jpeg", "gif", "webp", "svg"})
_COPYRIGHT: Final = re.compile(
    r"(?:\N{COPYRIGHT SIGN}|&copy;|\(c\)|copyright)\s*(?:(?:19|20)\d{2}\s*[-\N{EN DASH}]\s*)?"
    r"((?:19|20)\d{2})",
    re.IGNORECASE,
)
_QUESTION: Final = re.compile(
    r"^(?:how|what|why|when|who|where|which|can|do|does|is|are|should|will|como|que|por que|"
    r"cuando|cuanto|donde|quien|puedo|necesito)\b"
)
_ZOOM_OFF: Final = re.compile(
    r"user-scalable\s*=\s*(?:no|0)|maximum-scale\s*=\s*1(?:\.0+)?(?![\d.])"
)
_BUTTON_CLASS: Final = re.compile(r"\b(?:btn|button|cta)\b")
_HEADER_CLASS: Final = re.compile(r"\b(?:hero|banner|masthead|top-?bar)")
_SOCIAL_HOSTS: Final = ("facebook.com", "instagram.com", "twitter.com", "x.com", "linkedin.com",
                        "youtube.com", "tiktok.com", "yelp.com", "nextdoor.com", "pinterest.com",
                        "g.page", "business.google.com")  # fmt: skip
_MAP_HREF: Final = re.compile(
    r"google\.[a-z.]+/maps|maps\.google\.|goo\.gl/maps|maps\.app\.goo\.gl|maps\.apple\.com|"
    r"bing\.com/maps",
    re.IGNORECASE,
)
_MESSAGING: Final = re.compile(
    r"^(?:sms:|whatsapp:)|wa\.me/|api\.whatsapp\.com|m\.me/|messenger\.com", re.IGNORECASE
)
LOCAL_BUSINESS_TYPES: Final = frozenset({
    "LocalBusiness", "ProfessionalService", "HomeAndConstructionBusiness", "GeneralContractor",
    "RoofingContractor", "Plumber", "Electrician", "HVACBusiness", "HousePainter", "Locksmith",
    "MovingCompany", "AutomotiveBusiness", "AutoRepair", "AutoBodyShop", "AutoDealer",
    "FoodEstablishment", "Restaurant", "Bakery", "CafeOrCoffeeShop", "BarOrPub",
    "FastFoodRestaurant",
    "Store", "GroceryStore", "ClothingStore", "HardwareStore", "HealthAndBeautyBusiness",
    "BeautySalon", "HairSalon", "NailSalon", "DaySpa", "MedicalBusiness", "Dentist", "Physician",
    "MedicalClinic", "Pharmacy", "Optician", "LegalService", "Attorney", "Notary",
    "FinancialService", "AccountingService", "InsuranceAgency", "RealEstateAgent",
    "ChildCare", "DryCleaningOrLaundry", "EmergencyService", "SportsActivityLocation",
    "ExerciseGym", "TravelAgency", "LodgingBusiness", "EntertainmentBusiness", "AnimalShelter",
    "VeterinaryCare", "SelfStorage", "TattooParlor", "EmploymentAgency",
})  # fmt: skip
ORGANIZATION_TYPES: Final = frozenset({"Organization", "Corporation", "NGO", "OnlineBusiness"})
REQUIRED_LOCAL_PROPERTIES: Final = ("name", "address", "telephone")


# ---- facts ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Detected:
    """A technology or trust signal seen on a page: which trace showed it and the fragment."""

    id: str
    field: str
    snippet: str
    version: str | None = None


@dataclass(frozen=True, slots=True)
class Link:
    url: str
    text: str
    in_header: bool


@dataclass(frozen=True, slots=True)
class FormFacts:
    purpose: str
    action: str | None
    method: str
    fields: int
    required: int
    unlabeled: int
    types: tuple[str, ...]
    captcha: bool
    file_upload: bool
    insecure_action: bool
    submit: str | None


@dataclass(frozen=True, slots=True)
class Cta:
    kind: str
    text: str
    target: str | None
    primary: bool


@dataclass(frozen=True, slots=True)
class SchemaFacts:
    blocks: int
    invalid_blocks: int
    types: tuple[str, ...]
    local_business: bool
    organization: bool
    faq: bool
    missing_properties: tuple[str, ...]
    same_as: int
    area_served: bool
    opening_hours: bool
    telephones: tuple[str, ...]
    aggregate_rating: bool
    microdata_types: tuple[str, ...]
    invalid_snippet: str | None


@dataclass(frozen=True, slots=True)
class PageFacts:
    url: str
    final_url: str
    https: bool
    lang: str | None
    #: `es` or `en` from the words themselves, else from `lang`; null when neither tells.
    language: str | None
    title: str | None
    description: str | None
    canonical: str | None
    robots: str | None
    viewport: str | None
    favicon: bool
    open_graph: tuple[str, ...]
    hreflang: tuple[tuple[str, str], ...]
    generator: str | None
    headings: tuple[tuple[int, str], ...]
    h1: int
    heading_skips: int
    question_headings: int
    words: int
    kinds: tuple[str, ...]
    internal_links: tuple[Link, ...]
    tel_links: tuple[str, ...]
    mailto_links: tuple[str, ...]
    messaging_links: tuple[str, ...]
    map_links: tuple[str, ...]
    social_links: tuple[str, ...]
    phones: tuple[str, ...]
    emails: tuple[str, ...]
    addresses: tuple[str, ...]
    hours: str | None
    service_area: str | None
    copyright_year: int | None
    forms: tuple[FormFacts, ...]
    ctas: tuple[Cta, ...]
    images: int
    images_missing_alt: int
    empty_links: int
    zoom_disabled: bool
    mixed_content: tuple[str, ...]
    scripts: int
    technologies: tuple[Detected, ...]
    trust: tuple[Detected, ...]
    schema: SchemaFacts


def _text(node: LexborNode | None) -> str:
    return " ".join((node.text(deep=True, separator=" ", strip=True) if node else "").split())


def clip(text: str, limit: int = SNIPPET) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "\N{HORIZONTAL ELLIPSIS}"


def _unique[T](values: Iterable[T], limit: int = MAX_ITEMS) -> tuple[T, ...]:
    seen: set[T] = set()
    out: list[T] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
            if len(out) >= limit:
                break
    return tuple(out)


def _attr(node: LexborNode, name: str) -> str:
    return (node.attributes.get(name) or "").strip()


def _ancestors(node: LexborNode) -> Iterator[LexborNode]:
    parent = node.parent
    while parent is not None:
        yield parent
        parent = parent.parent


def fold(text: str) -> str:
    """Accent- and case-insensitive text, which the lexicons are written for."""
    return sort_key(text)


def _in_header(node: LexborNode) -> bool:
    for parent in _ancestors(node):
        if parent.tag in ("header", "nav") or _HEADER_CLASS.search(
            fold(f"{_attr(parent, 'class')} {_attr(parent, 'id')}")
        ):
            return True
    return False


def phone_digits(value: str) -> str | None:
    """The ten digits of a North American number, or None."""
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def kinds_of(url: str, title: str | None = None, heading: str | None = None) -> tuple[str, ...]:
    """What a page is about, from its path and the start of its title, heading or link text."""
    words = fold(re.sub(r"[/_\-.]+", " ", urlsplit(url).path))
    head = fold(f"{title or ''} | {heading or ''}")
    return tuple(
        kind
        for kind, pattern in _KIND_PATTERNS.items()
        if re.search(rf"\b(?:{pattern})\b", words) or re.search(rf"(?:^|\| )(?:{pattern})\b", head)
    )


def _detect(
    html: str, scripts: list[str], inline: str, generator: str | None, frames: list[str],
    hrefs: list[str],
) -> tuple[Detected, ...]:  # fmt: skip
    sources = {
        "script": scripts,
        "inline": [inline],
        "markup": [html],
        "meta": [generator] if generator else [],
        "frame": frames,
        "link": hrefs,
    }
    found: list[Detected] = []
    for signature, patterns in _COMPILED:
        for trace, pattern in patterns.items():
            match = next((m for text in sources[trace] if (m := pattern.search(text))), None)
            if match is None:
                continue
            start = max(0, match.start() - 40)
            fragment = clip(match.string[start : match.end() + 40])
            found.append(Detected(signature.id, trace, fragment, match.groupdict().get("version")))
            break
    return tuple(found)


def _schema(tree: LexborHTMLParser) -> SchemaFacts:
    blocks = invalid = 0
    invalid_snippet: str | None = None
    items: list[dict[str, object]] = []

    def walk(value: object) -> None:
        if isinstance(value, list):
            for item in value[:200]:
                walk(item)
        elif isinstance(value, dict) and len(items) < 500:
            items.append(value)
            for key in ("@graph", "mainEntity", "itemListElement"):
                walk(value.get(key))

    for node in tree.css('script[type="application/ld+json"]')[:30]:
        raw = (node.text(deep=True) or "").strip()
        blocks += 1
        try:
            walk(orjson.loads(raw[:200_000]))
        except orjson.JSONDecodeError:
            invalid += 1
            invalid_snippet = invalid_snippet or clip(raw, 120)

    def types(item: dict[str, object]) -> set[str]:
        value = item.get("@type")
        values = value if isinstance(value, list) else [value]
        return {str(v).rsplit("/", 1)[-1] for v in values if isinstance(v, str)}

    local = [item for item in items if types(item) & LOCAL_BUSINESS_TYPES]
    missing: list[str] = []
    if local:
        for prop in REQUIRED_LOCAL_PROPERTIES:
            value = local[0].get(prop)
            if prop == "address" and isinstance(value, dict):
                value = value.get("streetAddress") or value.get("addressLocality")
            if not value:
                missing.append(prop)
    telephones = _unique(
        digits
        for item in items
        if isinstance(phone := item.get("telephone"), str)
        and (digits := phone_digits(phone)) is not None
    )

    def questions(item: dict[str, object]) -> list[object]:
        entity = item.get("mainEntity")
        return entity if isinstance(entity, list) else [entity]

    faq = any(
        "FAQPage" in types(item)
        and any(isinstance(entry, dict) and "Question" in types(entry) for entry in questions(item))
        for item in items
    )

    def profiles(item: dict[str, object]) -> int:
        links = item.get("sameAs")
        return len(links) if isinstance(links, list) else int(isinstance(links, str))

    same_as = max((profiles(item) for item in items), default=0)
    microdata = _unique(
        _attr(node, "itemtype").rsplit("/", 1)[-1]
        for node in tree.css("[itemtype]")
        if "schema.org" in _attr(node, "itemtype")
    )
    return SchemaFacts(
        blocks=blocks,
        invalid_blocks=invalid,
        types=_unique((kind for item in items for kind in sorted(types(item))), 40),
        local_business=bool(local),
        organization=any(types(item) & ORGANIZATION_TYPES for item in items),
        faq=faq,
        missing_properties=tuple(missing),
        same_as=same_as,
        area_served=any(item.get("areaServed") for item in local),
        opening_hours=any(
            item.get("openingHours") or item.get("openingHoursSpecification") for item in local
        ),
        telephones=telephones,
        aggregate_rating=any(item.get("aggregateRating") for item in items),
        microdata_types=microdata,
        invalid_snippet=invalid_snippet,
    )


def _labelled(node: LexborNode, labels: set[str]) -> bool:
    if _attr(node, "aria-label") or _attr(node, "aria-labelledby") or _attr(node, "title"):
        return True
    if (identifier := _attr(node, "id")) and identifier in labels:
        return True
    return any(parent.tag == "label" for parent in _ancestors(node))


def _form_purpose(types: tuple[str, ...], fields: int, words: str) -> str:
    if "password" in types:
        return "login"
    if "search" in types or (re.search(r"\b(?:search|buscar)\b", words) and fields <= 2):
        return "search"
    if re.search(r"\b(?:quote|estimate|cotiza\w*|presupuesto)\b", words):
        return "quote"
    if (
        "date" in types
        or "time" in types
        or re.search(r"\b(?:appointment|book|schedule|reserv\w*|cita|agend\w*)\b", words)
    ):
        return "booking"
    if (
        fields <= 2
        and "email" in types
        and re.search(r"\b(?:subscribe|newsletter|suscrib\w*|boletin)\b", words)
    ):
        return "newsletter"
    if "textarea" in types or re.search(r"\b(?:message|contact|mensaje|contacto)\b", words):
        return "contact"
    return "other"


def _forms(tree: LexborHTMLParser, final_url: str, https: bool) -> tuple[FormFacts, ...]:
    labels = {_attr(label, "for") for label in tree.css("label[for]")}
    captcha_page = tree.css_first(".g-recaptcha, .h-captcha, .cf-turnstile") is not None
    forms: list[FormFacts] = []
    for form in tree.css("form")[:MAX_ITEMS]:
        fields = [
            node
            for node in form.css("input, select, textarea")
            if node.tag != "input"
            or _attr(node, "type").lower() not in ("hidden", "submit", "button", "image", "reset")
        ]
        types = _unique(
            (_attr(node, "type").lower() or "text") if node.tag == "input" else (node.tag or "")
            for node in fields
        )
        words = fold(
            " ".join(
                [_text(form)]
                + [_attr(node, name) for node in fields for name in ("name", "placeholder", "id")]
            )
        )
        action_raw = _attr(form, "action")
        action = canonical(action_raw, final_url) if action_raw else None
        submit = form.css_first("button, input[type='submit']")
        submit_text = (_text(submit) or _attr(submit, "value")) if submit is not None else ""
        forms.append(
            FormFacts(
                purpose=_form_purpose(types, len(fields), words),
                action=action,
                method=(_attr(form, "method") or "get").lower(),
                fields=len(fields),
                required=sum(1 for node in fields if "required" in node.attributes),
                unlabeled=sum(1 for node in fields if not _labelled(node, labels)),
                types=types,
                captcha=captcha_page,
                file_upload="file" in types,
                insecure_action=https and action is not None and action.startswith("http://"),
                submit=clip(submit_text, 60) or None,
            )
        )
    return tuple(forms)


def _cta_kind(text: str, href: str) -> str | None:
    if href.lower().startswith("tel:"):
        return "call"
    folded = fold(text)
    return next((kind for kind, pattern in CTA_KINDS if pattern.search(folded)), None)


def _language(visible: str, lang: str | None) -> str | None:
    share = spanish_share(visible)
    if share is not None and share >= 0.6:
        return "es"
    if share is not None and share <= 0.4:
        return "en"
    code = (lang or "")[:2].lower()
    return code if code in ("es", "en") else None


def page_facts(html: str, url: str, final_url: str, *, technologies: bool = True) -> PageFacts:
    """The facts of one page. `final_url` is where it was served from, after its redirects.
    `technologies=False` skips their detection, three quarters of the time a page takes, for a
    reader that never looks at them (contact extraction)."""
    tree = LexborHTMLParser(html)
    site = site_of(final_url)
    https = urlsplit(final_url).scheme == "https"
    meta: dict[str, str] = {}
    for node in tree.css("meta"):
        key = (_attr(node, "name") or _attr(node, "property")).lower()
        if key and key not in meta:
            meta[key] = _attr(node, "content")
    rels = [(node, _attr(node, "rel").lower()) for node in tree.css("link[rel]")]
    canonical_href = next((_attr(node, "href") for node, rel in rels if rel == "canonical"), None)
    hreflang = _unique(
        (_attr(node, "hreflang").lower(), canonical(_attr(node, "href"), final_url) or "")
        for node, rel in rels
        if rel == "alternate" and _attr(node, "hreflang")
    )
    html_node = tree.css_first("html")
    lang = (_attr(html_node, "lang") or None) if html_node is not None else None
    title = _text(tree.css_first("title")) or None
    headings: list[tuple[int, str]] = []
    for node in tree.css("h1, h2, h3, h4, h5, h6"):
        if (text := _text(node)) and len(headings) < MAX_HEADINGS:
            headings.append((int((node.tag or "h0")[1]), clip(text, 120)))

    scripts = [_attr(node, "src") for node in tree.css("script[src]")]
    frames = [_attr(node, "src") for node in tree.css("iframe[src]")]
    inline = " ".join(
        node.text(deep=True) or ""
        for node in tree.css("script:not([src])")
        if _attr(node, "type").lower() != "application/ld+json"
    )[:MAX_INLINE_SCRIPT]

    internal: list[Link] = []
    tel: list[str] = []
    mailto: list[str] = []
    messaging: list[str] = []
    maps = [src for src in frames if _MAP_HREF.search(src)]
    social: list[str] = []
    hrefs: list[str] = []
    ctas: list[Cta] = []
    empty_links = 0
    for node in tree.css("a[href], button, input[type='submit'], input[type='button']"):
        href = _attr(node, "href")
        text = (
            _text(node) or _attr(node, "aria-label") or _attr(node, "value") or _attr(node, "title")
        )
        if node.tag == "a":
            hrefs.append(href)
            if not text and not any(_attr(image, "alt") for image in node.css("img")):
                empty_links += 1
            lowered = href.lower()
            if lowered.startswith("tel:"):
                if number := href[4:].strip():
                    tel.append(number)
            elif lowered.startswith("mailto:"):
                if address := href[7:].split("?", 1)[0].strip():
                    mailto.append(address)
            elif _MESSAGING.search(href):
                messaging.append(href)
            elif (target := canonical(href, final_url)) is not None:
                host = urlsplit(target).netloc.lower().removeprefix("www.")
                if _MAP_HREF.search(target):
                    maps.append(target)
                elif any(host == name or host.endswith(f".{name}") for name in _SOCIAL_HOSTS):
                    social.append(target)
                elif site_of(target) == site and len(internal) < MAX_LINKS:
                    internal.append(Link(target, clip(text, 80), _in_header(node)))
        if (kind := _cta_kind(text, href)) is not None:
            primary = (
                node.tag in ("button", "input")
                or bool(_BUTTON_CLASS.search(fold(_attr(node, "class"))))
                or (kind != "contact" and _in_header(node))
            )
            target_url = canonical(href, final_url) if href else None
            ctas.append(Cta(kind, clip(text or href, 60), target_url, primary))

    images = tree.css("img")
    mixed: list[str] = []
    if https:
        for node in tree.css(
            "script[src], img[src], iframe[src], source[src], video[src], audio[src], "
            "embed[src], link[rel][href]"
        ):
            if node.tag == "link" and "stylesheet" not in _attr(node, "rel").lower():
                continue
            if (value := _attr(node, "src") or _attr(node, "href")).lower().startswith("http://"):
                mixed.append(value)
    detected = (
        _detect(html[:MAX_RAW_HTML], scripts, inline, meta.get("generator"), frames, hrefs)
        if technologies
        else ()
    )
    schema = _schema(tree)
    forms = _forms(tree, final_url, https)
    script_count = len(tree.css("script"))

    # from here on the tree loses its scripts and styles: only visible text is read
    body = tree.body
    if body is not None:
        for node in body.css("script, style, noscript, template, svg"):
            node.decompose()
    visible = _text(body)
    folded = fold(visible)
    main = tree.css_first("main, article, [role='main']") or body
    phones = _unique(
        digits for m in _PHONE.finditer(visible) if (digits := phone_digits(m.group()))
    )
    emails = _unique(
        m.group().lower()
        for m in _EMAIL.finditer(visible)
        if m.group().rsplit(".", 1)[-1].lower() not in _NOT_AN_EMAIL_TLD
    )
    addresses = _unique(clip(m.group(), 80) for m in _STREET.finditer(visible)) or _unique(
        clip(m.group(), 40) for m in _STATE_ZIP.finditer(visible)
    )
    years = [int(m.group(1)) for m in _COPYRIGHT.finditer(visible)]
    trust = tuple(
        Detected(kind, "text", clip(folded[max(0, m.start() - 40) : m.end() + 40]))
        for kind, pattern in TRUST_SIGNALS
        if (m := pattern.search(folded)) is not None
    )
    first_heading = next((text for level, text in headings if level == 1), None)
    return PageFacts(
        url=url,
        final_url=final_url,
        https=https,
        lang=lang,
        language=_language(visible, lang),
        title=clip(title, 300) if title else None,
        description=clip(meta["description"], 400) if meta.get("description") else None,
        canonical=canonical(canonical_href, final_url) if canonical_href else None,
        robots=meta.get("robots") or None,
        viewport=meta.get("viewport") or None,
        favicon=any("icon" in rel.split() for _, rel in rels),
        open_graph=tuple(
            sorted(key for key, value in meta.items() if key.startswith("og:") and value)
        ),
        hreflang=hreflang,
        generator=meta.get("generator") or None,
        headings=tuple(headings),
        h1=sum(1 for level, _ in headings if level == 1),
        heading_skips=sum(1 for (a, _), (b, _) in itertools.pairwise(headings) if b > a + 1),
        question_headings=sum(
            1 for _, text in headings if text.endswith("?") or _QUESTION.search(fold(text))
        ),
        words=len(_text(main).split()),
        kinds=kinds_of(final_url, title, first_heading),
        internal_links=tuple(internal),
        tel_links=_unique(tel),
        mailto_links=_unique(mailto),
        messaging_links=_unique(messaging),
        map_links=_unique(maps),
        social_links=_unique(social),
        phones=phones,
        emails=emails,
        addresses=addresses,
        hours=next((clip(m.group(), 80) for m in _HOURS.finditer(folded)), None),
        service_area=next((clip(m.group(), 120) for m in _SERVICE_AREA.finditer(folded)), None),
        copyright_year=max(years) if years else None,
        forms=forms,
        ctas=_unique(ctas, 40),
        images=len(images),
        images_missing_alt=sum(1 for image in images if "alt" not in image.attributes),
        empty_links=empty_links,
        zoom_disabled=bool(_ZOOM_OFF.search((meta.get("viewport") or "").lower())),
        mixed_content=_unique(mixed),
        scripts=script_count,
        technologies=detected,
        trust=trust,
        schema=schema,
    )
