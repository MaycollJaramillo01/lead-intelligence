"""Mail-domain lookups (phase 09): the only network access of contact validation.

One DNS question per domain (MX, then A/AAAA when there is no MX), bounded by a timeout. Never an
SMTP conversation, never a probe of a mailbox: a domain that accepts mail says nothing about whether
an address exists, and the statuses say exactly that. Callers cache the answers (`store`).
"""

from collections.abc import Callable
from typing import Final

import dns.exception
import dns.name
import dns.resolver

from research_engine.domain import MailDomainResult

TIMEOUT_S: Final = 3.0

#: A lookup: domain in, what it answered and its mail exchanges out.
Lookup = Callable[[str], tuple[MailDomainResult, tuple[str, ...]]]


def lookup_mail_domain(
    domain: str, timeout_s: float = TIMEOUT_S
) -> tuple[MailDomainResult, tuple[str, ...]]:
    """Asks the system resolver. Timeouts and resolver failures are answers too: they say nothing
    about the domain, so they never invalidate an address."""
    resolver = dns.resolver.Resolver()
    resolver.timeout = timeout_s
    resolver.lifetime = timeout_s
    try:
        answer = resolver.resolve(domain, "MX", search=False)
    except dns.resolver.NXDOMAIN:
        return MailDomainResult.NXDOMAIN, ()
    except dns.resolver.NoAnswer:
        return _implicit_mx(resolver, domain)
    except dns.exception.Timeout:
        return MailDomainResult.TIMEOUT, ()
    except dns.exception.DNSException:
        return MailDomainResult.ERROR, ()
    exchanges = sorted(
        (int(getattr(record, "preference", 0)), getattr(record, "exchange", dns.name.root))
        for record in answer
    )
    if all(exchange == dns.name.root for _, exchange in exchanges):
        return MailDomainResult.NULL_MX, ()
    return MailDomainResult.MX, tuple(
        exchange.to_text(omit_final_dot=True) for _, exchange in exchanges
        if exchange != dns.name.root
    )  # fmt: skip


def _implicit_mx(
    resolver: dns.resolver.Resolver, domain: str
) -> tuple[MailDomainResult, tuple[str, ...]]:
    for rdtype in ("A", "AAAA"):
        try:
            resolver.resolve(domain, rdtype, search=False)
        except dns.resolver.NoAnswer:
            continue
        except dns.resolver.NXDOMAIN:
            return MailDomainResult.NXDOMAIN, ()
        except dns.exception.Timeout:
            return MailDomainResult.TIMEOUT, ()
        except dns.exception.DNSException:
            return MailDomainResult.ERROR, ()
        return MailDomainResult.IMPLICIT_MX, (domain,)
    return MailDomainResult.NO_MAIL, ()
