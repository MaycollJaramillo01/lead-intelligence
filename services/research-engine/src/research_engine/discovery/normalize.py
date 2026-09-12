"""Light tidying shared by the source adapters. Deliberately shallow.

Phase 04 only has to show a candidate and notice that it looks like one already seen. Real
normalisation and entity resolution are phase 06, so nothing here overwrites a raw value: the tidied
form lives next to it and the payload keeps whatever the source sent.
"""

import re
from urllib.parse import urlsplit

from research_engine.domain import sort_key

_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_DOMAIN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9-]+)+$")
_DIGITS = re.compile(r"\D+")

MAX_DEDUP_KEY = 300


def clean(value: object) -> str | None:
    """Collapses whitespace and turns blanks into None, whatever the source gave us."""
    if value is None or isinstance(value, bool | dict | list):
        return None
    text = " ".join(str(value).split())
    return text or None


def domain_of(value: str | None) -> str | None:
    """The registrable host of a URL or a bare domain, lower-cased and without `www.`."""
    text = clean(value)
    if text is None:
        return None
    if not _SCHEME.match(text):
        if "@" in text or ":" in text or " " in text:
            return None  # an address or a scheme we do not understand is not a domain
        text = f"https://{text}"
    host = urlsplit(text).hostname
    if host is None:
        return None
    host = host.lower().removeprefix("www.")
    return host if _DOMAIN.match(host) else None


def website_of(value: str | None) -> str | None:
    """The URL as given, with a scheme so it can be opened. None when it is not usable."""
    text = clean(value)
    if text is None or domain_of(text) is None:
        return None
    return text if _SCHEME.match(text) else f"https://{text}"


def digits_of(value: str | None) -> str | None:
    """Just the digits of a phone number: enough to spot the same number written differently."""
    text = clean(value)
    if text is None:
        return None
    digits = _DIGITS.sub("", text)
    return digits or None


def dedup_key(
    *,
    name: str,
    locality: str | None = None,
    region: str | None = None,
    domain: str | None = None,
    external_id: str | None = None,
) -> str:
    """A cheap identity guess, never an entity resolution.

    A stable id from the source wins; otherwise the tidied name plus the strongest place signal we
    have. Two candidates sharing a key are *probably* the same business - the engine only links them
    and says so, it never merges them.
    """
    if external_id:
        return f"id:{external_id}"[:MAX_DEDUP_KEY]
    if domain:
        return f"domain:{domain}"[:MAX_DEDUP_KEY]
    place = sort_key(" ".join(filter(None, (locality, region))))
    return f"name:{sort_key(name)}|{place}"[:MAX_DEDUP_KEY]
