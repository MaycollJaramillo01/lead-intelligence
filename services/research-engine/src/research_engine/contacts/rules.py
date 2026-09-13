"""Contact intelligence rules (phase 09): pure, deterministic and versioned.

- Extraction: the phones, emails, contact and booking forms, booking links, social profiles and
  published people one crawled page shows, read from the crawler's parsed snapshot and from the
  page's facts (`website.extract`). Forms are only read, never submitted.
- Validation: numbering plan, address syntax, the answer of a mail-domain lookup, form structure,
  agreement between sources and freshness. Nothing here calls a number, sends a message, talks to a
  mail server or fetches anything.
- Contactability: an explainable subscore of the channels a business publishes.
- Suppression files: reading and writing the do-not-contact list as CSV.

A status never says more than its check: `structurally_valid` only means well formed,
`domain_valid` that the domain accepts mail, `likely_valid` that independent evidence agrees.
People are read from what a page publishes about them (a name next to a role); a name is never used
to infer anything else about a person.
"""

import csv
import io
import re
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat, PhoneNumberType
from pydantic import JsonValue

from research_engine.domain import (
    UUID_PATTERN,
    Contactability,
    ContactabilityComponent,
    ContactFreshness,
    ContactLevel,
    ContactRole,
    ContactType,
    DecisionMakerRelationship,
    MailDomainResult,
    SuppressionKind,
    ValidationStatus,
    sort_key,
)
from research_engine.resolution import normalize
from research_engine.resolution.normalize import Presence
from research_engine.website.extract import (
    LOCAL_BUSINESS_TYPES,
    ORGANIZATION_TYPES,
    SIGNATURES,
    PageFacts,
    clip,
)

EXTRACTOR_VERSION: Final = "1.0.0"
RULES_VERSION: Final = "1.0.0"
CONTACTABILITY_VERSION: Final = "1.0.0"
MAX_PEOPLE_PER_PAGE: Final = 20
SNIPPET: Final = 200

Signals = tuple[tuple[str, str | None], ...]


# ---- channel identity ----------------------------------------------------------------------------


def channel_key(contact_type: ContactType, normalized: str | None, raw: str) -> str:
    """The identity of a channel within a business (the key phase 06 dedupes on). A value that could
    not be normalised keeps its raw text, so a malformed number still shows, as invalid."""
    if normalized is None:
        return f"{contact_type.value}:raw:{' '.join(raw.casefold().split())}"[:400]
    if contact_type is ContactType.SOCIAL_PROFILE:
        social = normalize.social_profile(normalized)
        if social.handle:
            return f"social:{social.platform}:{social.handle}"[:400]
    return f"{contact_type.value}:{normalized.casefold()}"[:400]


# ---- phones --------------------------------------------------------------------------------------

#: Line types the numbering plan can tell. `FIXED_LINE_OR_MOBILE` (every NANP number) and `UNKNOWN`
#: cannot be told apart, so no line type is claimed for them.
LINE_TYPES: Final[Mapping[int, str]] = {
    PhoneNumberType.FIXED_LINE: "fixed_line",
    PhoneNumberType.MOBILE: "mobile",
    PhoneNumberType.TOLL_FREE: "toll_free",
    PhoneNumberType.PREMIUM_RATE: "premium_rate",
    PhoneNumberType.SHARED_COST: "shared_cost",
    PhoneNumberType.VOIP: "voip",
    PhoneNumberType.PERSONAL_NUMBER: "personal_number",
    PhoneNumberType.PAGER: "pager",
    PhoneNumberType.UAN: "uan",
    PhoneNumberType.VOICEMAIL: "voicemail",
}


@dataclass(frozen=True, slots=True)
class PhoneCheck:
    e164: str | None
    extension: str | None
    status: ValidationStatus
    signals: Signals


def check_phone(raw: str, country: str | None) -> PhoneCheck:
    """E.164 only with enough context: an explicit `+` country code, or the country of the business
    the number is published for. Without either a national number is `unknown`, never guessed."""
    text, _ = normalize.clean_text(raw)
    if text is None:
        return PhoneCheck(None, None, ValidationStatus.INVALID, (("empty", None),))
    explicit = text.lstrip().startswith("+")
    if not explicit and country is None:
        return PhoneCheck(None, None, ValidationStatus.UNKNOWN, (("no_country_context", None),))
    try:
        number = phonenumbers.parse(text, None if explicit else country)
    except NumberParseException:
        return PhoneCheck(None, None, ValidationStatus.INVALID, (("unparseable", None),))
    extension = number.extension or None
    ext: Signals = (("extension", extension),) if extension else ()
    if not phonenumbers.is_possible_number(number):
        return PhoneCheck(None, extension, ValidationStatus.INVALID, (*ext, ("not_possible", None)))
    e164 = phonenumbers.format_number(number, PhoneNumberFormat.E164)
    signals: list[tuple[str, str | None]] = [
        ("country", phonenumbers.region_code_for_number(number))
    ]
    signals += ext
    if not phonenumbers.is_valid_number(number):
        signals.append(("not_in_numbering_plan", None))
        return PhoneCheck(e164, extension, ValidationStatus.INVALID, tuple(signals))
    signals.append(
        ("line_type", LINE_TYPES.get(phonenumbers.number_type(number), "not_determinable"))
    )
    national = str(number.national_number)
    if number.country_code == 1 and national[3:6] == "555" and "0100" <= national[6:] <= "0199":
        signals.append(("fictional_range", None))  # reserved for fiction: never a real line
    return PhoneCheck(e164, extension, ValidationStatus.STRUCTURALLY_VALID, tuple(signals))


# ---- emails --------------------------------------------------------------------------------------

# ponytail: well-known disposable providers only; ship a maintained list if real data shows more
DISPOSABLE_DOMAINS: Final = frozenset({
    "mailinator.com", "guerrillamail.com", "guerrillamail.net", "sharklasers.com",
    "10minutemail.com", "10minutemail.net", "tempmail.com", "temp-mail.org", "yopmail.com",
    "yopmail.net",
    "throwawaymail.com", "trashmail.com", "getnada.com", "dispostable.com", "maildrop.cc",
    "mintemail.com", "fakeinbox.com", "mailnesia.com", "emailondeck.com", "moakt.com",
    "spamgourmet.com", "mytemp.email", "tempinbox.com", "burnermail.io", "mailcatch.com",
    "inboxkitten.com", "tempr.email", "discard.email", "mohmal.com", "harakirimail.com",
})  # fmt: skip
MAIL_ACCEPTED: Final = frozenset({MailDomainResult.MX, MailDomainResult.IMPLICIT_MX})
MAIL_REFUSED: Final = frozenset(
    {MailDomainResult.NULL_MX, MailDomainResult.NO_MAIL, MailDomainResult.NXDOMAIN}
)


@dataclass(frozen=True, slots=True)
class EmailCheck:
    address: str | None
    domain: str | None
    status: ValidationStatus
    method: str
    signals: Signals


def mail_domain_of(value: str) -> str | None:
    """The ASCII domain an address would be looked up under, or None when it is not an address."""
    return normalize.email(value).domain


def check_email(
    value: str, mail: MailDomainResult | None, website_domains: Collection[str]
) -> EmailCheck:
    """Syntax, then the answer about its domain when there is one. Role, free-provider, disposable
    and IDN are signals; catch-all cannot be known without talking to the mail server, so it is
    always `not_checked`. A timeout or an unchecked domain leaves the address
    `structurally_valid`."""
    found = normalize.email(value)
    if found.status is not Presence.PRESENT or found.address is None or found.domain is None:
        return EmailCheck(
            None, None, ValidationStatus.INVALID, "structure", (("syntax", "invalid"),)
        )
    registrable = normalize.registrable_domain(found.domain)
    signals: list[tuple[str, str | None]] = []
    if found.is_role and found.local:
        signals.append(("role_address", found.local.casefold().split("+", 1)[0]))
    if found.is_free_provider:
        signals.append(("free_provider", registrable))
    if registrable in DISPOSABLE_DOMAINS:
        signals.append(("disposable", registrable))
    if "xn--" in found.domain:
        signals.append(("idn", found.domain))
    signals += [
        ("domain_matches_website", "yes" if registrable in website_domains else "no"),
        ("catch_all", "not_checked"),
        ("mail_domain", mail.value if mail else "not_checked"),
    ]
    if mail in MAIL_ACCEPTED:
        status, method = ValidationStatus.DOMAIN_VALID, "mail_domain"
    elif mail in MAIL_REFUSED:
        status, method = ValidationStatus.INVALID, "mail_domain"
    else:
        status, method = (
            ValidationStatus.STRUCTURALLY_VALID,
            "structure" if mail is None else "mail_domain",
        )
    return EmailCheck(found.address, found.domain, status, method, tuple(signals))


# ---- forms, booking links and profiles -----------------------------------------------------------


def check_form(detail: Mapping[str, JsonValue]) -> tuple[ValidationStatus, str, Signals]:
    """What the stored page shows of a form: its fields, method, action and captcha, and the HTTP
    status of the page it was on. Never submitted and its action never requested."""
    fields = detail.get("fields")
    count = fields if isinstance(fields, int) else 0
    signals: list[tuple[str, str | None]] = [
        ("form_fields", str(count)),
        ("form_method", str(detail.get("method") or "get")),
        ("captcha", "yes" if detail.get("captcha") else "no"),
    ]
    if isinstance(detail.get("purpose"), str):
        signals.append(("form_purpose", str(detail["purpose"])))
    status = detail.get("page_status")
    if isinstance(status, int):
        signals.append(("page_status", str(status)))
    if isinstance(detail.get("provider"), str):
        signals.append(("provider", str(detail["provider"])))
    if count == 0 and detail.get("kind") != "link":
        return ValidationStatus.INVALID, "form_structure", (*signals, ("no_fields", None))
    if isinstance(status, int) and not 200 <= status < 300:
        return ValidationStatus.INVALID, "form_structure", tuple(signals)
    if detail.get("insecure_action"):
        signals.append(("insecure_action", None))
    return ValidationStatus.STRUCTURALLY_VALID, "form_structure", tuple(signals)


BOOKING_LINKS: Final = tuple(
    (signature.name, re.compile(signature.link, re.IGNORECASE))
    for signature in SIGNATURES
    if signature.category == "booking" and signature.link
)


# ---- freshness and status ------------------------------------------------------------------------

FRESH_DAYS: Final[Mapping[ContactType, int]] = {
    ContactType.PHONE: 365, ContactType.EMAIL: 365, ContactType.SOCIAL_PROFILE: 365,
    ContactType.CONTACT_FORM: 180, ContactType.BOOKING_FORM: 180,
}  # fmt: skip
MAX_AGE_DAYS: Final[Mapping[ContactType, int]] = {
    ContactType.PHONE: 730, ContactType.EMAIL: 730, ContactType.SOCIAL_PROFILE: 730,
    ContactType.CONTACT_FORM: 365, ContactType.BOOKING_FORM: 365,
}  # fmt: skip


def freshness(contact_type: ContactType, last_seen: datetime, now: datetime) -> ContactFreshness:
    age = (now - last_seen).total_seconds() / 86_400
    if age <= FRESH_DAYS[contact_type]:
        return ContactFreshness.FRESH
    return ContactFreshness.AGING if age <= MAX_AGE_DAYS[contact_type] else ContactFreshness.STALE


@dataclass(frozen=True, slots=True)
class ChannelInput:
    """What decides the status of one channel of one business."""

    contact_type: ContactType
    base: ValidationStatus
    base_method: str
    signals: Mapping[str, str | None]
    freshness: ContactFreshness
    #: False when every page that showed it was crawled again without it.
    current: bool
    #: Kinds of source (connector kinds) that publish it.
    source_kinds: frozenset[str]
    #: Published on the business's own website.
    own_site: bool
    hypothesis: bool
    suppressed: bool
    #: A person's decision: `confirmed`, `invalid` or None.
    review: str | None


def channel_status(channel: ChannelInput) -> tuple[ValidationStatus, str]:
    """(status, method), most restrictive first: suppression, invalid (a person's or a check's),
    hypothesis, stale, a person's confirmation, then what independent evidence supports."""
    if channel.suppressed and not channel.hypothesis:
        return ValidationStatus.SUPPRESSED, "suppression_list"
    if channel.review == "invalid":
        return ValidationStatus.INVALID, "manual_review"
    if channel.base is ValidationStatus.INVALID:
        return ValidationStatus.INVALID, channel.base_method
    stale = channel.freshness is ContactFreshness.STALE or not channel.current
    if channel.hypothesis:
        return (ValidationStatus.STALE if stale else ValidationStatus.UNKNOWN), "pattern_hypothesis"
    if stale:
        return ValidationStatus.STALE, "freshness"
    if channel.review == "confirmed":
        return ValidationStatus.LIKELY_VALID, "manual_review"
    kinds = len(channel.source_kinds)
    if channel.contact_type is ContactType.PHONE:
        if (
            channel.base is ValidationStatus.STRUCTURALLY_VALID
            and "fictional_range" not in channel.signals
            and kinds >= 2
        ):
            return ValidationStatus.LIKELY_VALID, "sources_agree"
    elif channel.contact_type is ContactType.EMAIL:
        matches = channel.signals.get("domain_matches_website") == "yes"
        if (
            channel.base is ValidationStatus.DOMAIN_VALID
            and "disposable" not in channel.signals
            and (matches or kinds >= 2)
        ):
            return ValidationStatus.LIKELY_VALID, "sources_agree"
    elif channel.base is ValidationStatus.STRUCTURALLY_VALID and channel.own_site:
        # a form or booking link on the live site, a profile the business's own site links to
        method = (
            "official_link"
            if channel.contact_type is ContactType.SOCIAL_PROFILE
            else "form_structure"
        )
        if "insecure_action" not in channel.signals:
            return ValidationStatus.LIKELY_VALID, method
    return channel.base, channel.base_method


# ---- contactability ------------------------------------------------------------------------------

STATUS_QUALITY: Final[Mapping[ValidationStatus, float]] = {
    ValidationStatus.LIKELY_VALID: 1.0,
    ValidationStatus.DOMAIN_VALID: 0.8,
    ValidationStatus.STRUCTURALLY_VALID: 0.6,
    ValidationStatus.OBSERVED: 0.4,
    ValidationStatus.UNKNOWN: 0.3,
    ValidationStatus.STALE: 0.1,
}
TYPE_AVAILABILITY: Final[Mapping[str, float]] = {
    "phone": 0.4, "email": 0.35, "form": 0.15, "social_profile": 0.1,
}  # fmt: skip
SOURCE_QUALITY: Final[Mapping[str, float]] = {
    "official_api": 1.0, "public_registry": 1.0, "business_website": 1.0, "directory": 0.8,
    "public_dataset": 0.8, "platform_api": 0.8, "file_import": 0.7, "manual_seed": 0.7,
    "web_search": 0.6, "synthetic": 0.5,
}  # fmt: skip
WEIGHTS: Final[Mapping[str, int]] = {
    "availability": 25, "validation": 30, "role": 15, "freshness": 15, "source": 15,
}  # fmt: skip
DECISION_ROLES: Final = frozenset({
    DecisionMakerRelationship.OWNER, DecisionMakerRelationship.FOUNDER,
    DecisionMakerRelationship.EXECUTIVE, DecisionMakerRelationship.PARTNER,
})  # fmt: skip
FORMULA: Final = (
    "score = round(100 x sum(weight x value) / sum(weight)) over availability 25, validation 30, "
    "role 15, freshness 15, source 15; only published or imported channels that are not invalid, "
    "suppressed or in the fictional range count; hypotheses never count; a suppressed business has "
    "no score"
)


@dataclass(frozen=True, slots=True)
class ContactabilityInput:
    contact_type: ContactType
    status: ValidationStatus
    hypothesis: bool
    role: ContactRole
    level: ContactLevel
    relationship: DecisionMakerRelationship | None
    freshness: ContactFreshness
    source_kinds: frozenset[str]
    fictional: bool


def _type_group(contact_type: ContactType) -> str:
    forms = (ContactType.CONTACT_FORM, ContactType.BOOKING_FORM)
    return "form" if contact_type in forms else contact_type.value


def _role_value(channel: ContactabilityInput) -> float:
    if channel.level is ContactLevel.PERSON:
        return 1.0 if channel.relationship in DECISION_ROLES else 0.7
    if channel.contact_type is ContactType.SOCIAL_PROFILE:
        return 0.2
    return {ContactRole.DEPARTMENT: 0.7, ContactRole.GENERAL: 0.6, ContactRole.ROLE_ADDRESS: 0.5,
            ContactRole.PERSONAL: 0.7}[channel.role]  # fmt: skip


def contactability(
    channels: Sequence[ContactabilityInput], *, business_suppressed: bool
) -> Contactability:
    """How reachable a business is through what it publishes. Every component says what it read."""
    if business_suppressed:
        return Contactability(
            score=None,
            version=CONTACTABILITY_VERSION,
            formula=FORMULA,
            components=[],
            gates=["business_suppressed"],
            channels_counted=0,
        )
    usable = [
        channel for channel in channels
        if not channel.hypothesis and not channel.fictional and channel.status in STATUS_QUALITY
    ]  # fmt: skip
    groups = sorted({_type_group(channel.contact_type) for channel in usable})
    direct = [
        channel
        for channel in usable
        if channel.contact_type in (ContactType.PHONE, ContactType.EMAIL)
    ]
    best_quality = max(
        direct or usable, key=lambda channel: STATUS_QUALITY[channel.status], default=None
    )
    best_role = max(usable, key=_role_value, default=None)
    freshness_value = {
        ContactFreshness.FRESH: 1.0,
        ContactFreshness.AGING: 0.5,
        ContactFreshness.STALE: 0.0,
    }
    best_fresh = max(usable, key=lambda channel: freshness_value[channel.freshness], default=None)
    kinds = sorted({kind for channel in usable for kind in channel.source_kinds})
    source_value = max((SOURCE_QUALITY.get(kind, 0.5) for kind in kinds), default=0.0)
    role_detail: str | None = None
    if best_role is not None:
        role_detail = best_role.role.value
        if best_role.level is ContactLevel.PERSON and best_role.relationship is not None:
            role_detail = best_role.relationship.value
    values: dict[str, tuple[float, str | None]] = {
        "availability": (
            round(min(1.0, sum(TYPE_AVAILABILITY[group] for group in groups)), 3),
            ", ".join(groups) or None,
        ),
        "validation": (
            STATUS_QUALITY[best_quality.status] if best_quality else 0.0,
            best_quality.status.value if best_quality else None,
        ),
        "role": (_role_value(best_role) if best_role else 0.0, role_detail),
        "freshness": (
            freshness_value[best_fresh.freshness] if best_fresh else 0.0,
            best_fresh.freshness.value if best_fresh else None,
        ),
        "source": (source_value, ", ".join(kinds) or None),
    }  # fmt: skip
    components = [
        ContactabilityComponent(
            code=code, weight=weight, value=values[code][0], detail=values[code][1]
        )
        for code, weight in WEIGHTS.items()
    ]
    total = sum(component.weight * component.value for component in components)
    score = round(100 * total / sum(WEIGHTS.values()))
    return Contactability(
        score=score, version=CONTACTABILITY_VERSION, formula=FORMULA, components=components,
        gates=[] if usable else ["no_usable_channel"], channels_counted=len(usable),
    )  # fmt: skip


# ---- extraction ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FoundContact:
    """A channel a page shows. `person` is the normalised name of the published person it belongs
    to; `postal_code` and `street_number` the address of the structured-data block it came from."""

    contact_type: ContactType
    raw: str
    normalized: str | None
    field_path: str
    context: str
    confidence: float
    platform: str | None = None
    role_or_department: str | None = None
    person: str | None = None
    postal_code: str | None = None
    street_number: str | None = None
    detail: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FoundPerson:
    name: str
    key: str
    role: str | None
    relationship: DecisionMakerRelationship
    field_path: str
    snippet: str
    confidence: float
    #: Doubts the page itself raises (`historical_role`, `no_role`).
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PageContacts:
    contacts: tuple[FoundContact, ...]
    people: tuple[FoundPerson, ...]


_ROLES: Final[tuple[tuple[DecisionMakerRelationship, str], ...]] = (
    (DecisionMakerRelationship.OWNER,
     r"co-?owner|owner(?:\s*(?:/|&|and|-)\s*operator)?|proprietor"
     r"|due(?:\N{LATIN SMALL LETTER N WITH TILDE}|n)[oa]|propietari[oa]"),
    (DecisionMakerRelationship.FOUNDER, r"co-?founder|founder|co-?fundador(?:a)?|fundador(?:a)?"),
    (DecisionMakerRelationship.EXECUTIVE,
     r"chief executive officer|ceo|chief operating officer|coo|chief financial officer|cfo"
     r"|vice president|president(?:e|a)?|principal"),
    (DecisionMakerRelationship.PARTNER, r"managing partner|partner|soci[oa]"),
    (DecisionMakerRelationship.MANAGER,
     r"general manager|office manager|operations manager|manager|director(?:a)?(?: general)?"
     r"|gerente(?: general)?|administrador(?:a)?"),
)  # fmt: skip
_ROLE_RE: Final = re.compile(
    r"(?<![\w-])(?:"
    + "|".join(f"(?P<{rel.value}>{pattern})" for rel, pattern in _ROLES)
    + r")(?![\w-])",
    re.IGNORECASE,
)
_SEPARATOR_BEFORE: Final = re.compile(
    r"\s*(?:,|\||:|-|\N{EN DASH}|\N{EM DASH})\s*(?:(?:and|y)\s+)?"
    r"(?:(?:the|our|el|la|nuestr[oa])\s+)?$",
    re.IGNORECASE,
)
_SEPARATOR_AFTER: Final = re.compile(r"^\s*(?::|-|\N{EN DASH}|\N{EM DASH})\s*")
_BY: Final = re.compile(
    r"(?<!\w)(?P<verb>founded|started|established|owned|run|operated|fundad[oa]|dirigid[oa])"
    r"(?:\s+(?:in|en)\s+\d{4})?\s+(?:by|por)\s+",
    re.IGNORECASE,
)
_TOKEN: Final = re.compile(r"[^\W\d_][^\W\d_'\N{RIGHT SINGLE QUOTATION MARK}\-]*\.?")
#: Words that are never part of a person's name in these patterns (folded).
_NOT_NAME: Final = frozenset({
    "a", "about", "and", "at", "by", "call", "company", "contact", "co", "corp", "de", "del", "el",
    "for", "from", "group", "home", "in", "inc", "la", "las", "llc", "los", "ltd", "meet", "of",
    "our", "service", "services", "team", "the", "us", "we", "with", "your", "y", "nuestro",
    "nuestra", "equipo", "empresa", "servicios", "llamenos", "contactenos", "synthetic", "test",
    "roofing", "plumbing", "cleaning", "construction", "repair", "bakery", "clinic", "dental",
    "landscaping", "grocery", "tax", "restaurant", "salon", "auto", "office", "general",
    *(term for _, pattern in _ROLES for term in re.findall(r"[a-z]+", pattern)),
})  # fmt: skip
_PERSON_TYPES: Final = frozenset({"Person"})
_PEOPLE_KEYS: Final[Mapping[str, DecisionMakerRelationship | None]] = {
    "founder": DecisionMakerRelationship.FOUNDER,
    "founders": DecisionMakerRelationship.FOUNDER,
    "employee": None,
    "employees": None,
    "member": None,
    "members": None,
}


def _name_tokens(text: str, *, from_end: bool) -> str | None:
    """The 2-4 capitalised words at the end (or start) of `text`, when they look like a name."""
    tokens = list(_TOKEN.finditer(text))
    picked: list[str] = []
    ordered = reversed(tokens) if from_end else iter(tokens)
    previous: re.Match[str] | None = None
    for token in ordered:
        if previous is not None:
            gap = (
                text[token.end() : previous.start()]
                if from_end
                else text[previous.end() : token.start()]
            )
            if gap.strip():
                break
        word = token.group()
        sentence_end = word.endswith(".") and len(word) > 2
        if from_end and sentence_end:
            break  # the end of an earlier sentence, not part of the name
        if not word[0].isupper() or sort_key(word).strip(".") in _NOT_NAME or len(picked) == 4:
            break
        picked.append(word.rstrip(".") if sentence_end else word)
        previous = token
        if sentence_end:
            break
    if from_end:
        picked.reverse()
    while picked and sort_key(picked[0]).strip(".") in _NOT_NAME:
        picked.pop(0)
    words = [word for word in picked if len(word.strip(".")) > 1]
    return " ".join(picked) if len(words) >= 2 else None


def _relationship(match: re.Match[str]) -> DecisionMakerRelationship:
    return next(
        DecisionMakerRelationship(name) for name, value in match.groupdict().items() if value
    )


def people_in_text(text: str, field_path: str) -> list[FoundPerson]:
    """`Name, Role`, `Role: Name` and `founded / owned by Name` phrases, in English and Spanish."""
    found: list[FoundPerson] = []
    for match in _ROLE_RE.finditer(text):
        before, after = (
            text[max(0, match.start() - 90) : match.start()],
            text[match.end() : match.end() + 90],
        )
        name = None
        if separator := _SEPARATOR_BEFORE.search(before):
            name = _name_tokens(before[: separator.start()], from_end=True)
        if name is None and (separator := _SEPARATOR_AFTER.match(after)):
            name = _name_tokens(after[separator.end() :], from_end=False)
        if name is not None:
            snippet = clip(text[max(0, match.start() - 80) : match.end() + 80], SNIPPET)
            found.append(FoundPerson(name, sort_key(name), match.group(), _relationship(match),
                                     field_path, snippet, 0.6, ()))  # fmt: skip
    for match in _BY.finditer(text):
        name = _name_tokens(text[match.end() : match.end() + 90], from_end=False)
        if name is None:
            continue
        verb = sort_key(match.group("verb"))
        founder = verb in ("founded", "started", "established", "fundado", "fundada")
        relationship = (
            DecisionMakerRelationship.FOUNDER if founder else DecisionMakerRelationship.OWNER
        )
        snippet = clip(text[max(0, match.start() - 40) : match.end() + 60], SNIPPET)
        # who founded a business long ago may not run it today
        found.append(FoundPerson(name, sort_key(name), None, relationship, field_path, snippet, 0.5,
                                 ("historical_role",) if founder else ()))  # fmt: skip
    return found


def _types(item: Mapping[str, object]) -> set[str]:
    value = item.get("@type")
    values = value if isinstance(value, list) else [value]
    return {str(v).rsplit("/", 1)[-1] for v in values if isinstance(v, str)}


def _strings(value: object) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return [v.strip() for v in values if isinstance(v, str) and v.strip()]


def _walk(
    value: object, path: str, parent: str | None = None
) -> Iterator[tuple[Mapping[str, object], str, str | None]]:
    if isinstance(value, list):
        for index, item in enumerate(value[:200]):
            yield from _walk(item, f"{path}[{index}]", parent)
    elif isinstance(value, dict):
        yield value, path, parent
        for key, item in value.items():
            if isinstance(item, dict | list) and key != "@context":
                yield from _walk(item, f"{path}.{key}", str(key))


def _relationship_of_title(title: str | None) -> DecisionMakerRelationship | None:
    match = _ROLE_RE.search(title or "")
    return _relationship(match) if match else None


def scan_page(
    payload: Mapping[str, object], facts: PageFacts | None, *, country: str | None
) -> PageContacts:
    """The channels and people one crawled page shows. `facts` (the page read again from its stored
    HTML) adds its forms; without them the page still gives its phones, emails, links and data."""
    url = str(payload.get("final_url") or payload.get("url") or "")
    found: list[FoundContact] = []
    people: list[FoundPerson] = []

    def add(
        contact_type: ContactType,
        raw: str,
        field_path: str,
        context: str,
        confidence: float,
        *,
        person: str | None = None,
        department: str | None = None,
        postal: str | None = None,
        street: str | None = None,
    ) -> None:
        if contact_type is ContactType.PHONE:
            normalized = check_phone(raw, country).e164
        else:
            address = normalize.email(raw)
            normalized = address.address
            if department is None and address.is_role and address.local:
                department = address.local.casefold().split("+", 1)[0]
        found.append(
            FoundContact(
                contact_type, raw[:320], normalized, field_path, context, confidence,
                role_or_department=department, person=person, postal_code=postal,
                street_number=street,
            )
        )  # fmt: skip

    for name, linked_origin in (("phones", "tel_link"), ("emails", "mailto")):
        items = payload.get(name)
        kind = ContactType.PHONE if name == "phones" else ContactType.EMAIL
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("value"), str):
                continue
            origin = str(item.get("origin") or "text")
            linked = origin == linked_origin
            where = ("tel link" if name == "phones" else "mailto link") if linked else "page text"
            add(kind, str(item["value"]), f"{name}[{origin}]", f"{where} on {url}",
                0.8 if linked else 0.6)  # fmt: skip

    links = payload.get("links")
    for link in links if isinstance(links, list) else []:
        href = link.get("url") if isinstance(link, dict) else None
        if not isinstance(href, str):
            continue
        social = normalize.social_profile(href)
        if social.url and social.handle:
            found.append(FoundContact(ContactType.SOCIAL_PROFILE, href[:500], social.url, "links",
                                      f"link on {url}", 0.7, platform=social.platform))  # fmt: skip
            continue
        provider = next((name for name, pattern in BOOKING_LINKS if pattern.search(href)), None)
        if provider is not None and (site := normalize.website(href)).url:
            found.append(FoundContact(ContactType.BOOKING_FORM, href[:500], site.url, "links",
                                      f"{provider} booking link on {url}", 0.7,
                                      detail={"kind": "link", "provider": provider}))  # fmt: skip

    blocks = payload.get("json_ld")
    for item, path, parent in _walk(blocks if isinstance(blocks, list) else [], "json_ld"):
        types = _types(item)
        if types & _PERSON_TYPES or (parent in _PEOPLE_KEYS and isinstance(item.get("name"), str)):
            names = _strings(item.get("name"))
            if not names or len(people) >= MAX_PEOPLE_PER_PAGE:
                continue
            name = names[0][:300]
            title = next(iter(_strings(item.get("jobTitle"))), None)
            relationship = _relationship_of_title(title) or (
                _PEOPLE_KEYS.get(parent or "") or DecisionMakerRelationship.UNKNOWN
            )
            if (
                title is not None
                and _relationship_of_title(title) is None
                and parent not in ("founder", "founders")
            ):
                continue  # a published role that is not a decision role: not a decision maker
            reasons = ("no_role",) if relationship is DecisionMakerRelationship.UNKNOWN else ()
            snippet = clip(f"{name} ({title or parent or 'Person'})", SNIPPET)
            people.append(
                FoundPerson(name, sort_key(name), title, relationship, path, snippet, 0.8, reasons)
            )
            key = sort_key(name)
            context = f"structured data (Person) on {url}"
            for phone in _strings(item.get("telephone")):
                add(ContactType.PHONE, phone, f"{path}.telephone", context, 0.8, person=key)
            for mail in _strings(item.get("email")):
                add(ContactType.EMAIL, mail, f"{path}.email", context, 0.8, person=key)
            continue
        is_business = bool(types & (LOCAL_BUSINESS_TYPES | ORGANIZATION_TYPES))
        is_point = "ContactPoint" in types or parent == "contactPoint"
        if not (is_business or is_point):
            continue
        address = item.get("address")
        postal = street = None
        if isinstance(address, dict):
            postal = next(iter(_strings(address.get("postalCode"))), None)
            if (line := next(iter(_strings(address.get("streetAddress"))), None)) and (
                number := re.match(r"\s*(\d+)", line)
            ):
                street = number.group(1)
        department = next(iter(_strings(item.get("contactType"))), None) if is_point else None
        where = (
            f"structured data ({department or next(iter(sorted(types)), 'Organization')}) on {url}"
        )
        for phone in _strings(item.get("telephone")):
            add(ContactType.PHONE, phone, f"{path}.telephone", where, 0.8, department=department,
                postal=postal, street=street)  # fmt: skip
        for mail in _strings(item.get("email")):
            add(ContactType.EMAIL, mail, f"{path}.email", where, 0.8, department=department,
                postal=postal, street=street)  # fmt: skip
        for profile in _strings(item.get("sameAs")):
            social = normalize.social_profile(profile)
            if social.url and social.handle:
                found.append(
                    FoundContact(ContactType.SOCIAL_PROFILE, profile[:500], social.url,
                                 f"{path}.sameAs", where, 0.8, platform=social.platform)
                )  # fmt: skip

    for index, form in enumerate(facts.forms if facts is not None else ()):
        if form.purpose in ("contact", "quote"):
            contact_type = ContactType.CONTACT_FORM
        elif form.purpose == "booking":
            contact_type = ContactType.BOOKING_FORM
        else:
            continue  # login, search, newsletter and other forms are not contact channels
        target = form.action or f"{facts.final_url if facts else url}#form"
        found.append(FoundContact(
            contact_type, target[:500], target, f"forms[{index}]",
            f"{form.purpose} form on {url} ({form.fields} fields, {form.method.upper()})", 0.7,
            detail={"kind": "form", "purpose": form.purpose, "fields": form.fields,
                    "required": form.required, "method": form.method, "captcha": form.captcha,
                    "insecure_action": form.insecure_action, "file_upload": form.file_upload},
        ))  # fmt: skip

    texts: list[tuple[str, str]] = []
    headings = payload.get("headings")
    for index, heading in enumerate(headings if isinstance(headings, list) else []):
        if isinstance(heading, dict) and isinstance(heading.get("text"), str):
            texts.append((str(heading["text"]), f"headings[{index}]"))
    if isinstance(payload.get("main_text"), str):
        texts.append((str(payload["main_text"]), "main_text"))
    for text, field_path in texts:
        people += people_in_text(text, field_path)

    by_key: dict[tuple[ContactType, str, str | None], FoundContact] = {}
    for contact in sorted(
        found,
        key=lambda contact: (
            -contact.confidence,
            contact.postal_code is None,
            contact.role_or_department is None,
        ),
    ):
        by_key.setdefault(
            (
                contact.contact_type,
                channel_key(contact.contact_type, contact.normalized, contact.raw),
                contact.person,
            ),
            contact,
        )
    unique_people: dict[str, FoundPerson] = {}
    for person in sorted(people, key=lambda person: -person.confidence):
        unique_people.setdefault(person.key, person)
    return PageContacts(
        tuple(by_key.values()), tuple(list(unique_people.values())[:MAX_PEOPLE_PER_PAGE])
    )


# ---- people and address patterns -----------------------------------------------------------------

EMAIL_PATTERNS: Final[Mapping[str, Callable[[str, str], str]]] = {
    "first.last": lambda first, last: f"{first}.{last}",
    "first_last": lambda first, last: f"{first}_{last}",
    "firstlast": lambda first, last: f"{first}{last}",
    "f.last": lambda first, last: f"{first[0]}.{last}",
    "flast": lambda first, last: f"{first[0]}{last}",
    "firstl": lambda first, last: f"{first}{last[0]}",
    "first": lambda first, last: first,
}
#: A first name alone is too common to build an address on.
HYPOTHESIS_PATTERNS: Final = tuple(pattern for pattern in EMAIL_PATTERNS if pattern != "first")


def name_parts(full_name: str) -> tuple[str, str] | None:
    tokens = re.findall(r"[a-z]+", sort_key(full_name))
    return (tokens[0], tokens[-1]) if len(tokens) >= 2 else None


def email_pattern(local: str, full_name: str) -> str | None:
    """The pattern an address's local part follows for a published name, when it follows one."""
    parts = name_parts(full_name)
    wanted = local.casefold().split("+", 1)[0]
    if parts is None:
        return None
    return next((name for name, build in EMAIL_PATTERNS.items() if build(*parts) == wanted), None)


def hypothesis_address(pattern: str, full_name: str, domain: str) -> str | None:
    parts = name_parts(full_name)
    if parts is None or pattern not in HYPOTHESIS_PATTERNS:
        return None
    return f"{EMAIL_PATTERNS[pattern](*parts)}@{domain}"


# ---- suppression files ---------------------------------------------------------------------------

CSV_COLUMNS: Final = ("kind", "value", "reason", "source", "created_at", "lifted_at")
MAX_IMPORT_ROWS: Final = 5000
_FORMULA_START: Final = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True, slots=True)
class SuppressionRow:
    row: int
    kind: SuppressionKind
    value: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class RowProblem:
    row: int
    code: str
    message: str


def normalize_suppression(kind: SuppressionKind, value: str) -> str | None:
    """The form a value is suppressed under, or None when it is not one of these things."""
    text = value.strip().removeprefix("'").strip()
    match kind:
        case SuppressionKind.EMAIL:
            return normalize.email(text).match_key
        case SuppressionKind.PHONE:
            check = check_phone(text, "US")
            return check.e164 if check.status is not ValidationStatus.UNKNOWN else None
        case SuppressionKind.DOMAIN:
            host = text.rsplit("@", 1)[-1]
            site = normalize.website(host)
            return site.registrable_domain if site.status is Presence.PRESENT else None
        case SuppressionKind.BUSINESS:
            lowered = text.lower()
            return lowered if re.fullmatch(UUID_PATTERN, lowered) else None


def _cell(cells: Sequence[str], at: int | None) -> str:
    return cells[at].strip() if at is not None and at < len(cells) else ""


def read_suppressions(content: str) -> tuple[list[SuppressionRow], list[RowProblem]]:
    """`kind,value[,reason]` rows with a header. Every row that cannot be used says why, by line."""
    reader = csv.reader(io.StringIO(content.removeprefix("\N{ZERO WIDTH NO-BREAK SPACE}")))
    header = [cell.strip().casefold() for cell in next(reader, [])]
    if "kind" not in header or "value" not in header:
        return [], [
            RowProblem(1, "missing_columns", "the first row must name the kind and value columns")
        ]
    kind_at, value_at = header.index("kind"), header.index("value")
    reason_at = header.index("reason") if "reason" in header else None
    rows: list[SuppressionRow] = []
    problems: list[RowProblem] = []
    for number, cells in enumerate(reader, start=2):
        if not any(cell.strip() for cell in cells):
            continue
        if len(rows) + len(problems) >= MAX_IMPORT_ROWS:
            problems.append(
                RowProblem(number, "too_many_rows", f"only {MAX_IMPORT_ROWS} rows are read")
            )
            break
        try:
            kind = SuppressionKind(_cell(cells, kind_at).casefold())
        except ValueError:
            problems.append(
                RowProblem(number, "unknown_kind", "kind must be email, phone, domain or business")
            )
            continue
        normalized = normalize_suppression(kind, _cell(cells, value_at))
        if normalized is None:
            problems.append(RowProblem(number, "invalid_value", f"not a valid {kind.value}"))
            continue
        rows.append(
            SuppressionRow(number, kind, normalized, _cell(cells, reason_at)[:2000] or None)
        )
    return rows, problems


def safe_cell(value: str) -> str:
    """A cell a spreadsheet will not run as a formula."""
    return f"'{value}" if value.startswith(_FORMULA_START) else value


def write_suppressions(rows: Sequence[Sequence[str]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    writer.writerows([safe_cell(cell) for cell in row] for row in rows)
    return out.getvalue()
