"""Contact intelligence rules (phase 09): numbering plans, addresses, mail-domain answers, forms,
published people, statuses, contactability and suppression files. Pure functions: nothing here
reaches the network (the store tests guard that)."""

from datetime import UTC, datetime, timedelta

import dns.exception
import dns.name
import dns.resolver
import pytest

from research_engine.contacts import mail_dns, rules
from research_engine.crawl.extract import snapshot
from research_engine.domain import (
    ContactFreshness,
    ContactLevel,
    ContactRole,
    ContactType,
    DecisionMakerRelationship,
    MailDomainResult,
    SuppressionKind,
    ValidationStatus,
)
from research_engine.website.extract import page_facts

NOW = datetime(2026, 9, 1, tzinfo=UTC)
V = ValidationStatus


def signals(found: rules.PhoneCheck | rules.EmailCheck) -> dict[str, str | None]:
    return dict(found.signals)


# ---- phones --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "country", "e164", "line_type"),
    [
        ("(512) 555-2368", "US", "+15125552368", "not_determinable"),
        ("512.555.2368", "US", "+15125552368", "not_determinable"),
        ("Tel: 1-512-555-2368", "US", "+15125552368", "not_determinable"),
        ("1-800-555-2368", "US", "+18005552368", "toll_free"),
        ("+44 20 7946 0958", None, "+442079460958", "fixed_line"),
        ("+34 612 34 56 78", None, "+34612345678", "mobile"),
        ("+52 55 1234 5678", "US", "+525512345678", "not_determinable"),
    ],
)
def test_us_and_international_numbers_are_normalised_with_their_line_type(
    raw: str, country: str | None, e164: str, line_type: str
) -> None:
    found = rules.check_phone(raw, country)
    assert (found.e164, found.status) == (e164, V.STRUCTURALLY_VALID)
    assert signals(found)["line_type"] == line_type


def test_extensions_fictional_numbers_and_missing_context() -> None:
    extension = rules.check_phone("+1 (512) 555-2368 ext. 12", "US")
    assert (extension.e164, extension.extension) == ("+15125552368", "12")
    fictional = rules.check_phone("(512) 555-0101", "US")
    assert fictional.status is V.STRUCTURALLY_VALID
    assert "fictional_range" in signals(fictional)
    # a national number without a country is never placed in one
    unplaced = rules.check_phone("512-555-2368", None)
    assert (unplaced.e164, unplaced.status) == (None, V.UNKNOWN)
    assert "no_country_context" in signals(unplaced)


@pytest.mark.parametrize("raw", ["555-1234", "12345", "(112) 555-2368", "call us", ""])
def test_numbers_outside_the_numbering_plan_are_invalid(raw: str) -> None:
    assert rules.check_phone(raw, "US").status is V.INVALID


# ---- emails --------------------------------------------------------------------------------------


def test_address_statuses_follow_syntax_and_the_mail_domain_answer() -> None:
    website = {"shop.example"}
    assert (
        rules.check_email("owner@shop.example", MailDomainResult.MX, website).status
        is V.DOMAIN_VALID
    )
    for refused in (MailDomainResult.NXDOMAIN, MailDomainResult.NULL_MX, MailDomainResult.NO_MAIL):
        found = rules.check_email("owner@shop.example", refused, website)
        assert (found.status, found.method) == (V.INVALID, "mail_domain")
        assert signals(found)["mail_domain"] == refused.value
    # a timeout and an unchecked domain say nothing about the address
    timeout = rules.check_email("owner@shop.example", MailDomainResult.TIMEOUT, website)
    assert (timeout.status, timeout.method) == (V.STRUCTURALLY_VALID, "mail_domain")
    unchecked = rules.check_email("owner@shop.example", None, website)
    assert (unchecked.status, unchecked.method) == (V.STRUCTURALLY_VALID, "structure")
    assert signals(unchecked)["catch_all"] == "not_checked"
    for broken in ("not-an-address", "a@@shop.example", "a@shop", "a..b@shop.example"):
        assert rules.check_email(broken, MailDomainResult.MX, website).status is V.INVALID


def test_idn_role_free_and_disposable_addresses_are_signals() -> None:
    idn = rules.check_email(
        "info@b\N{LATIN SMALL LETTER U WITH DIAERESIS}cher.example",
        MailDomainResult.MX,
        {"xn--bcher-kva.example"},
    )
    assert idn.domain == "xn--bcher-kva.example"
    assert idn.status is V.DOMAIN_VALID
    assert signals(idn)["idn"] == "xn--bcher-kva.example"
    assert signals(idn)["role_address"] == "info"
    assert signals(idn)["domain_matches_website"] == "yes"
    free = rules.check_email("shop@gmail.com", MailDomainResult.MX, set())
    assert signals(free)["free_provider"] == "gmail.com"
    assert signals(free)["domain_matches_website"] == "no"
    disposable = rules.check_email("buyer@mailinator.com", MailDomainResult.MX, set())
    assert "disposable" in signals(disposable)


# ---- forms and statuses --------------------------------------------------------------------------


def test_forms_are_read_never_submitted() -> None:
    html = (
        "<html><body><main><h1>Contact us</h1>"
        "<form action='http://shop.example/send' method='post'>"
        "<label for='n'>Name</label><input id='n' name='name' required>"
        "<textarea name='message'></textarea><button>Send</button></form>"
        "<form action='/search'><input type='search' name='q'></form></main></body></html>"
    )
    facts = page_facts(html, "https://shop.example/contact", "https://shop.example/contact")
    found = rules.scan_page({"url": "https://shop.example/contact"}, facts, country="US")
    (form,) = [c for c in found.contacts if c.contact_type is ContactType.CONTACT_FORM]
    assert form.normalized == "http://shop.example/send"
    assert form.detail["fields"] == 2
    assert form.detail["insecure_action"] is True
    status, method, form_signals = rules.check_form({**form.detail, "page_status": 200})
    assert (status, method) == (V.STRUCTURALLY_VALID, "form_structure")
    assert ("insecure_action", None) in form_signals
    assert rules.check_form({"kind": "form", "fields": 0})[0] is V.INVALID
    assert rules.check_form({"kind": "form", "fields": 3, "page_status": 404})[0] is V.INVALID


def channel(**changes: object) -> rules.ChannelInput:
    values: dict[str, object] = {
        "contact_type": ContactType.PHONE,
        "base": V.STRUCTURALLY_VALID,
        "base_method": "numbering_plan",
        "signals": {},
        "freshness": ContactFreshness.FRESH,
        "current": True,
        "source_kinds": frozenset({"directory", "business_website"}),
        "own_site": True,
        "hypothesis": False,
        "suppressed": False,
        "review": None,
    }
    return rules.ChannelInput(**(values | changes))  # type: ignore[arg-type]


def test_statuses_never_say_more_than_their_evidence() -> None:
    status = rules.channel_status
    assert status(channel()) == (V.LIKELY_VALID, "sources_agree")
    assert status(channel(source_kinds=frozenset({"directory"})))[0] is V.STRUCTURALLY_VALID
    assert status(channel(signals={"fictional_range": None}))[0] is V.STRUCTURALLY_VALID
    assert status(channel(freshness=ContactFreshness.STALE)) == (V.STALE, "freshness")
    assert status(channel(current=False)) == (V.STALE, "freshness")
    assert status(channel(base=V.INVALID, base_method="numbering_plan"))[0] is V.INVALID
    assert status(channel(review="invalid")) == (V.INVALID, "manual_review")
    assert status(channel(review="confirmed", source_kinds=frozenset())) == (
        V.LIKELY_VALID,
        "manual_review",
    )
    assert status(channel(suppressed=True, review="confirmed")) == (
        V.SUPPRESSED,
        "suppression_list",
    )
    email = {
        "contact_type": ContactType.EMAIL,
        "base": V.DOMAIN_VALID,
        "base_method": "mail_domain",
        "source_kinds": frozenset({"business_website"}),
    }
    assert status(channel(**email, signals={"domain_matches_website": "yes"}))[0] is V.LIKELY_VALID
    assert status(channel(**email, signals={"domain_matches_website": "no"}))[0] is V.DOMAIN_VALID
    assert (
        status(channel(**email, signals={"domain_matches_website": "yes", "disposable": "x"}))[0]
        is V.DOMAIN_VALID
    )
    form = {"contact_type": ContactType.CONTACT_FORM, "base_method": "form_structure"}
    assert status(channel(**form)) == (V.LIKELY_VALID, "form_structure")
    assert status(channel(**form, signals={"insecure_action": None}))[0] is V.STRUCTURALLY_VALID
    assert status(channel(**form, own_site=False))[0] is V.STRUCTURALLY_VALID


def test_a_hypothesis_is_never_validated_nor_confirmed() -> None:
    hypothesis = {"contact_type": ContactType.EMAIL, "base": V.DOMAIN_VALID, "hypothesis": True}
    assert rules.channel_status(channel(**hypothesis, review="confirmed")) == (
        V.UNKNOWN,
        "pattern_hypothesis",
    )
    assert rules.channel_status(channel(**hypothesis, suppressed=True))[0] is V.UNKNOWN
    assert (
        rules.channel_status(channel(**hypothesis, freshness=ContactFreshness.STALE))[0] is V.STALE
    )
    assert rules.channel_status(channel(**(hypothesis | {"base": V.INVALID})))[0] is V.INVALID


def test_freshness_by_type() -> None:
    assert (
        rules.freshness(ContactType.PHONE, NOW - timedelta(days=300), NOW) is ContactFreshness.FRESH
    )
    assert (
        rules.freshness(ContactType.PHONE, NOW - timedelta(days=500), NOW) is ContactFreshness.AGING
    )
    assert (
        rules.freshness(ContactType.PHONE, NOW - timedelta(days=800), NOW) is ContactFreshness.STALE
    )
    assert (
        rules.freshness(ContactType.CONTACT_FORM, NOW - timedelta(days=300), NOW)
        is ContactFreshness.AGING
    )


# ---- extraction ----------------------------------------------------------------------------------


def test_a_crawled_page_gives_its_channels_with_where_they_were_published() -> None:
    html = (
        "<html lang='en'><head><title>Shop</title><script type='application/ld+json'>"
        '{"@type": "Plumber", "telephone": "+1 512-555-2368", "email": "info@shop.example",'
        '"address": {"@type": "PostalAddress", "streetAddress": "12 Main St",'
        '"postalCode": "78701"},'
        '"contactPoint": {"@type": "ContactPoint", "contactType": "billing",'
        '"telephone": "512-555-2369"},'
        '"sameAs": ["https://www.instagram.com/shopexample"],'
        '"employee": {"@type": "Person", "name": "Jordan Sample", "jobTitle": "Owner",'
        '"email": "jordan@shop.example"}}</script></head><body><main>'
        "<a href='tel:+15125552368'>Call</a> "
        "<a href='mailto:sales@shop.example?subject=hi'>Mail</a>"
        "<p>Or call 512 555 2370. Book at "
        "<a href='https://calendly.com/shop/visit'>our calendar</a>."
        "</p></main></body></html>"
    )
    url = "https://shop.example/"
    found = rules.scan_page(snapshot(url, url, html, rendered=False), None, country="US")
    by_value = {(c.contact_type, c.normalized): c for c in found.contacts}
    phone = by_value[(ContactType.PHONE, "+15125552368")]
    assert phone.confidence == 0.8
    assert phone.postal_code == "78701"
    assert phone.street_number == "12"
    assert by_value[(ContactType.PHONE, "+15125552369")].role_or_department == "billing"
    assert (ContactType.PHONE, "+15125552370") in by_value  # page text
    assert by_value[(ContactType.EMAIL, "sales@shop.example")].context.startswith("mailto link")
    assert by_value[(ContactType.EMAIL, "info@shop.example")].role_or_department == "info"
    assert by_value[(ContactType.EMAIL, "jordan@shop.example")].person == "jordan sample"
    social = by_value[(ContactType.SOCIAL_PROFILE, "https://instagram.com/shopexample")]
    assert social.platform == "instagram"
    booking = next(c for c in found.contacts if c.contact_type is ContactType.BOOKING_FORM)
    assert booking.detail["provider"] == "Calendly"
    (person,) = found.people
    assert (person.name, person.relationship, person.reasons) == (
        "Jordan Sample",
        DecisionMakerRelationship.OWNER,
        (),
    )


def test_people_are_read_only_next_to_a_published_role() -> None:
    text = (
        "Meet the team. Alex Example, Owner of the shop since 2001. "
        "General Manager: Jordan Sample. Founded by Casey Demo in 1999. "
        "Riley Placeholder, Receptionist. Call Us - Owner operated. "
        "Nuestra gerente general: Taylor Muestra."
    )
    found = {p.name: p for p in rules.people_in_text(text, "main_text")}
    assert set(found) == {"Alex Example", "Jordan Sample", "Casey Demo", "Taylor Muestra"}
    assert found["Alex Example"].relationship is DecisionMakerRelationship.OWNER
    assert found["Jordan Sample"].relationship is DecisionMakerRelationship.MANAGER
    assert found["Casey Demo"].reasons == ("historical_role",)
    assert "Alex Example, Owner" in found["Alex Example"].snippet
    # a person record carries a name, a published role and its fragment: nothing else is inferred
    assert set(rules.FoundPerson.__slots__) == {
        "name",
        "key",
        "role",
        "relationship",
        "field_path",
        "snippet",
        "confidence",
        "reasons",
    }


def test_address_patterns_and_hypotheses() -> None:
    assert rules.email_pattern("jordan.sample", "Jordan Sample") == "first.last"
    assert rules.email_pattern("jsample", "Jordan Sample") == "flast"
    assert rules.email_pattern("info", "Jordan Sample") is None
    assert (
        rules.hypothesis_address("first.last", "Casey Demo", "shop.example")
        == "casey.demo@shop.example"
    )
    assert rules.hypothesis_address("first", "Casey Demo", "shop.example") is None
    assert rules.hypothesis_address("first.last", "Cher", "shop.example") is None


# ---- contactability ------------------------------------------------------------------------------


def contact(**changes: object) -> rules.ContactabilityInput:
    values: dict[str, object] = {
        "contact_type": ContactType.PHONE,
        "status": V.LIKELY_VALID,
        "hypothesis": False,
        "role": ContactRole.GENERAL,
        "level": ContactLevel.BUSINESS,
        "relationship": None,
        "freshness": ContactFreshness.FRESH,
        "source_kinds": frozenset({"business_website"}),
        "fictional": False,
    }
    return rules.ContactabilityInput(**(values | changes))  # type: ignore[arg-type]


def test_contactability_explains_itself_and_ignores_hypotheses() -> None:
    result = rules.contactability(
        [
            contact(),
            contact(
                contact_type=ContactType.EMAIL,
                status=V.DOMAIN_VALID,
                level=ContactLevel.PERSON,
                role=ContactRole.PERSONAL,
                relationship=DecisionMakerRelationship.OWNER,
            ),
        ],
        business_suppressed=False,
    )
    components = {component.code: component for component in result.components}
    assert components["availability"].value == 0.75
    assert components["validation"].value == 1.0
    assert components["role"].detail == "owner"
    expected = sum(c.weight * c.value for c in result.components) / sum(rules.WEIGHTS.values())
    assert result.score == round(100 * expected)
    assert result.gates == []
    only_guesses = rules.contactability(
        [contact(hypothesis=True), contact(fictional=True)], business_suppressed=False
    )
    assert (only_guesses.score, only_guesses.gates, only_guesses.channels_counted) == (
        0,
        ["no_usable_channel"],
        0,
    )
    suppressed = rules.contactability([contact()], business_suppressed=True)
    assert (suppressed.score, suppressed.gates) == (None, ["business_suppressed"])
    invalid = rules.contactability(
        [contact(status=V.INVALID), contact(status=V.SUPPRESSED)], business_suppressed=False
    )
    assert invalid.channels_counted == 0


# ---- suppression files ---------------------------------------------------------------------------


def test_suppression_files_are_read_by_line_and_written_without_formulas() -> None:
    content = (
        "\N{ZERO WIDTH NO-BREAK SPACE}Kind,Value,Reason\n"
        "email,Owner@Shop.Example,asked us\n"
        "phone,(512) 555-2368,\n"
        "domain,https://www.shop.example/contact,\n"
        "domain,@other.example,\n"
        "bogus,1,\n"
        "phone,call me,\n"
        "business,not-an-id,\n"
        "\n"
    )
    rows, problems = rules.read_suppressions(content)
    assert [(row.row, row.kind, row.value, row.reason) for row in rows] == [
        (2, SuppressionKind.EMAIL, "owner@shop.example", "asked us"),
        (3, SuppressionKind.PHONE, "+15125552368", None),
        (4, SuppressionKind.DOMAIN, "shop.example", None),
        (5, SuppressionKind.DOMAIN, "other.example", None),
    ]
    assert [(problem.row, problem.code) for problem in problems] == [
        (6, "unknown_kind"),
        (7, "invalid_value"),
        (8, "invalid_value"),
    ]
    assert rules.read_suppressions("value\nx@y.example\n")[1][0].code == "missing_columns"
    written = rules.write_suppressions(
        [["phone", "+15125552368", '=HYPERLINK("x")', "manual", "t", ""]]
    )
    assert written.splitlines()[1] == 'phone,\'+15125552368,"\'=HYPERLINK(""x"")",manual,t,'
    again, problems = rules.read_suppressions(written)
    assert [(row.kind, row.value) for row in again] == [(SuppressionKind.PHONE, "+15125552368")]


# ---- mail-domain lookups -------------------------------------------------------------------------


class Exchange:
    def __init__(self, preference: int, name: str) -> None:
        self.preference = preference
        self.exchange = dns.name.root if name == "." else dns.name.from_text(name)


@pytest.fixture
def answers(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], object]:
    """What the fake resolver answers: records, or an exception to raise, by (domain, type)."""
    table: dict[tuple[str, str], object] = {}

    def resolve(self: object, domain: str, rdtype: str, **_: object) -> object:
        answer = table.get((domain, rdtype), dns.resolver.NoAnswer())  # type: ignore[no-untyped-call]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(dns.resolver.Resolver, "resolve", resolve)
    return table


def test_mail_domain_answers_map_to_results(answers: dict[tuple[str, str], object]) -> None:
    answers[("mx.example", "MX")] = [Exchange(20, "b.mx.example."), Exchange(10, "a.mx.example.")]
    answers[("nullmx.example", "MX")] = [Exchange(0, ".")]
    answers[("gone.example", "MX")] = dns.resolver.NXDOMAIN()  # type: ignore[no-untyped-call]
    answers[("host.example", "A")] = ["192.0.2.1"]
    answers[("slow.example", "MX")] = dns.exception.Timeout()  # type: ignore[no-untyped-call]
    answers[("broken.example", "MX")] = dns.resolver.NoNameservers()  # type: ignore[no-untyped-call]
    lookup = mail_dns.lookup_mail_domain
    assert lookup("mx.example") == (MailDomainResult.MX, ("a.mx.example", "b.mx.example"))
    assert lookup("nullmx.example") == (MailDomainResult.NULL_MX, ())
    assert lookup("gone.example") == (MailDomainResult.NXDOMAIN, ())
    assert lookup("host.example") == (MailDomainResult.IMPLICIT_MX, ("host.example",))
    assert lookup("nothing.example") == (MailDomainResult.NO_MAIL, ())
    assert lookup("slow.example") == (MailDomainResult.TIMEOUT, ())
    assert lookup("broken.example") == (MailDomainResult.ERROR, ())
