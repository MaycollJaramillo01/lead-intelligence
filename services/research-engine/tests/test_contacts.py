"""Contact intelligence against the store (phase 09): extraction from listings and crawled pages,
consolidation across sources, validation without contacting anyone, freshness and conflicts,
hypotheses, published people and their review, suppressions in lists and exports, actions, jobs
and performance. Synthetic test data only, and a guard makes any network access fail."""

import socket
import sqlite3
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import NoReturn

import dns.resolver
import pytest

from research_engine import repository
from research_engine.contacts import engine, store
from research_engine.contacts.mail_dns import Lookup
from research_engine.domain import (
    Business,
    BusinessListQuery,
    BusinessSort,
    ContactAction,
    ContactActionRequest,
    ContactChannel,
    ContactDetail,
    ContactFreshness,
    ContactLevel,
    ContactListQuery,
    ContactOrigin,
    ContactPage,
    ContactRole,
    ContactType,
    DecisionMakerDecision,
    DecisionMakerListQuery,
    DecisionMakerRelationship,
    DecisionMakerReview,
    DecisionMakerReviewRequest,
    Job,
    JobStatus,
    LatinoStatus,
    MailDomainResult,
    SuppressionChangeRequest,
    SuppressionExportRequest,
    SuppressionImportRequest,
    SuppressionKind,
    SuppressionListQuery,
    ValidationStatus,
    utc_now,
)
from research_engine.latino import engine as latino_engine
from research_engine.latino import store as latino_store
from research_engine.resolution import engine as resolution_engine
from research_engine.resolution import store as resolution_store
from research_engine.testdata import (
    CONTACT_PEOPLE,
    CONTACT_SITES,
    ContactFixture,
    SyntheticContext,
    insert_candidates,
    insert_site_pages,
    seed_contacts,
    synthetic_mail_lookup,
    synthetic_page_html,
)

V = ValidationStatus
PUBLISHED = CONTACT_SITES["published"]
MOVED = CONTACT_SITES["moved"]


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Contact intelligence never reaches out in tests: a name lookup, a connection or a DNS query
    fails the test. (Nothing here calls, emails or submits either: there is no code that could.)"""

    def refuse(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("contact intelligence touched the network during a test")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(dns.resolver.Resolver, "resolve", refuse)


@pytest.fixture
def seeded(conn: sqlite3.Connection, synthetic: SyntheticContext, data_dir: Path) -> ContactFixture:
    return seed_contacts(conn, synthetic, data_dir)


def business_of(conn: sqlite3.Connection, candidate_id: str) -> str:
    (record_id,) = conn.execute(
        "SELECT business_id FROM entity_records WHERE candidate_id = ?", (candidate_id,)
    ).fetchone()
    business = repository.get(conn, Business, record_id)
    return business.merged_into_id or business.id


def detail(conn: sqlite3.Connection, fixture: ContactFixture, situation: str) -> ContactDetail:
    return store.get_contact_detail(conn, business_of(conn, fixture.candidates[situation]))


def by_key(found: ContactDetail) -> dict[str, ContactChannel]:
    return {channel.key: channel for channel in found.channels}


def contacts(conn: sqlite3.Connection, project_id: str, **query: object) -> ContactPage:
    values: dict[str, object] = {
        "project_id": project_id,
        "contact_type": None,
        "validation_status": None,
        "role": None,
        "freshness": None,
        "include_hypotheses": False,
        "include_suppressed": False,
        "limit": 500,
        "cursor": None,
    }
    return store.list_contacts(conn, ContactListQuery.model_validate(values | query))


def act(
    conn: sqlite3.Connection,
    business_id: str,
    key: str,
    action: ContactAction,
    reason: str | None = "Checked by a test",
    *,
    check: bool = False,
    lookup: Lookup | None = None,
) -> None:
    request = ContactActionRequest(
        business_id=business_id, key=key, action=action, reason=reason, check_mail_domains=check
    )
    store.act(conn, request, actor="test", lookup=lookup)


def run(
    conn: sqlite3.Connection,
    data_dir: Path,
    project_id: str,
    lookup: Lookup | None = synthetic_mail_lookup,
    *,
    check: bool = True,
) -> str:
    resolved, _ = resolution_store.plan_runs(conn, project_id, actor="test")
    assert resolution_engine.run_resolution(conn, resolved[0], lambda: False)
    created, _ = store.plan_runs(conn, project_id, check, actor="test")
    assert engine.run_contacts(conn, data_dir, created[0], lambda: False, lookup)
    return created[0]


def signal(channel: ContactChannel, code: str) -> str | None:
    return {item.code: item.value for item in channel.signals}[code]


# ---- extraction, consolidation and validation ----------------------------------------------------


def test_published_channels_are_consolidated_across_sources_and_validated(
    conn: sqlite3.Connection, seeded: ContactFixture
) -> None:
    found = detail(conn, seeded, "published")
    channels = by_key(found)
    phone = channels["phone:+15125550171"]
    assert {source.provenance.connector_key for source in phone.sources} == {
        "synthetic.fixture",
        "web.crawler",
    }
    assert phone.source_count >= 2
    assert phone.level is ContactLevel.LOCATION
    # two kinds of source agree, but the number is in the range reserved for fiction
    assert phone.validation_status is V.STRUCTURALLY_VALID
    assert "fictional_range" in {item.code for item in phone.signals}
    info = channels[f"email:info@{PUBLISHED}"]
    assert (info.validation_status, info.validation_method, info.role) == (
        V.LIKELY_VALID,
        "sources_agree",
        ContactRole.ROLE_ADDRESS,
    )
    assert signal(info, "mail_domain") == "mx"
    assert signal(info, "catch_all") == "not_checked"
    assert channels[f"email:sales@{PUBLISHED}"].role_or_department == "sales"
    manager = channels[f"email:jordan.sample@{PUBLISHED}"]
    assert (manager.level, manager.role) == (ContactLevel.PERSON, ContactRole.PERSONAL)
    form = next(item for item in found.channels if item.contact_type is ContactType.CONTACT_FORM)
    assert form.value == f"https://{PUBLISHED}/contact/send"
    assert (form.validation_status, form.validation_method) == (V.LIKELY_VALID, "form_structure")
    assert signal(form, "form_fields") == "3"
    assert signal(form, "page_status") == "200"
    booking = next(item for item in found.channels if item.contact_type is ContactType.BOOKING_FORM)
    assert booking.validation_status is V.LIKELY_VALID
    assert signal(booking, "provider") == "Calendly"
    social = next(
        item for item in found.channels if item.contact_type is ContactType.SOCIAL_PROFILE
    )
    assert (social.platform, social.validation_method) == ("facebook", "official_link")
    for channel in found.channels:
        for source in channel.sources:
            assert source.provenance.source_url or source.provenance.external_id
            assert source.observed_at <= channel.last_seen_at
    assert found.contactability.score
    assert found.contactability.channels_counted >= 5
    # a form is read from the stored page: nothing was requested, let alone submitted
    (sent,) = conn.execute(
        "SELECT count(*) FROM crawl_urls WHERE url LIKE '%/contact/send%'"
    ).fetchone()
    assert sent == 0


def test_old_and_disagreeing_numbers_stay_visible_with_their_sources(
    conn: sqlite3.Connection, seeded: ContactFixture
) -> None:
    found = detail(conn, seeded, "moved")
    channels = by_key(found)
    old, new = channels["phone:+13125550181"], channels["phone:+13125550182"]
    crawled = [source for source in old.sources if source.provenance.connector_key == "web.crawler"]
    assert crawled
    assert not any(source.current for source in crawled)
    assert all(source.current for source in new.sources)
    conflict = next(item for item in found.conflicts if item.code == "values_disagree")
    assert conflict.contact_type is ContactType.PHONE
    assert len(conflict.values) == 2
    later = store.get_contact_detail(conn, found.root_business_id, utc_now() + timedelta(days=800))
    assert by_key(later)["phone:+13125550182"].validation_status is V.STALE
    assert by_key(later)["phone:+13125550182"].freshness is ContactFreshness.STALE


def test_a_channel_its_site_no_longer_shows_is_stale(
    conn: sqlite3.Connection, synthetic: SyntheticContext, data_dir: Path
) -> None:
    site = "synthetic-bakery-0703.example"
    earlier = replace(synthetic, now=synthetic.now - timedelta(days=10))
    (candidate,) = insert_candidates(
        conn,
        earlier,
        [
            {
                "name": "Synthetic Bakery 0703",
                "city": "Austin",
                "state": "TX",
                "zip": "78701",
                "website": f"https://{site}/",
            }
        ],
    )
    for ctx, text in (
        (earlier, f"Write to orders@{site}. Test data."),
        (synthetic, "Orders in person only. Test data."),
    ):
        insert_site_pages(
            conn,
            ctx,
            site,
            {"/": synthetic_page_html("en", "Bakery", text)},
            candidate_id=candidate,
            data_dir=data_dir,
        )
    run(conn, data_dir, synthetic.project_id)
    found = store.get_contact_detail(conn, business_of(conn, candidate))
    orders = next(item for item in found.channels if item.value == f"orders@{site}")
    assert (orders.validation_status, orders.validation_method) == (V.STALE, "freshness")


# ---- hypotheses, people and names ----------------------------------------------------------------


def test_hypotheses_are_kept_apart_never_validated_nor_exported(
    conn: sqlite3.Connection, seeded: ContactFixture, synthetic: SyntheticContext
) -> None:
    found = detail(conn, seeded, "published")
    hypotheses = {channel.value: channel for channel in found.hypotheses}
    assert set(hypotheses) == {f"alex.example@{PUBLISHED}", f"casey.demo@{PUBLISHED}"}
    for channel in hypotheses.values():
        assert (channel.origin, channel.validation_status, channel.is_primary) == (
            ContactOrigin.HYPOTHESIS,
            V.UNKNOWN,
            False,
        )
        assert "pattern first.last" in (channel.sources[0].published_context or "")
    assert all(channel.origin is not ContactOrigin.HYPOTHESIS for channel in found.channels)
    assert found.contactability.channels_counted == len(
        [
            c
            for c in found.channels
            if c.validation_status not in (V.INVALID, V.SUPPRESSED)
            and "fictional_range" not in {s.code for s in c.signals}
        ]
    )
    project = synthetic.project_id
    assert all(
        item.origin is not ContactOrigin.HYPOTHESIS for item in contacts(conn, project).items
    )
    assert any(
        item.origin is ContactOrigin.HYPOTHESIS
        for item in contacts(conn, project, include_hypotheses=True).items
    )
    assert all(
        item.origin is not ContactOrigin.HYPOTHESIS for item in store.export_channels(conn, project)
    )
    assert any(
        item.origin is ContactOrigin.HYPOTHESIS
        for item in store.export_channels(conn, project, include_hypotheses=True)
    )
    key = f"email:alex.example@{PUBLISHED}"
    with pytest.raises(store.ContactError, match="never confirmed"):
        act(conn, found.root_business_id, key, ContactAction.CONFIRM)
    with pytest.raises(store.ContactError, match="published or imported"):
        act(conn, found.root_business_id, key, ContactAction.MARK_PRIMARY)
    for statement in (
        "UPDATE contact_points SET validation_status = 'likely_valid' WHERE origin = 'hypothesis'",
        "UPDATE contact_points SET is_primary = 1 WHERE origin = 'hypothesis'",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="hypothesis"):
            conn.execute(statement)


def test_published_people_carry_their_evidence_and_doubtful_ones_wait_for_review(
    conn: sqlite3.Connection, seeded: ContactFixture, synthetic: SyntheticContext
) -> None:
    found = detail(conn, seeded, "published")
    people = {view.decision_maker.full_name_raw: view for view in found.decision_makers}
    assert set(people) == {
        CONTACT_PEOPLE["owner"],
        CONTACT_PEOPLE["manager"],
        CONTACT_PEOPLE["office"],
    }
    owner = people[CONTACT_PEOPLE["owner"]]
    assert owner.decision_maker.relationship is DecisionMakerRelationship.OWNER
    assert owner.provenance.source_url == f"https://{PUBLISHED}/about"
    assert "Alex Example, Owner" in (owner.decision_maker.published_context or "")
    assert people[CONTACT_PEOPLE["manager"]].channel_keys == [f"email:jordan.sample@{PUBLISHED}"]
    assert owner.decision_maker.review_status is DecisionMakerReview.UNREVIEWED

    query = DecisionMakerListQuery(
        project_id=synthetic.project_id,
        review_status=DecisionMakerReview.AMBIGUOUS,
        limit=50,
        cursor=None,
    )
    founder = next(
        item
        for item in store.list_decision_makers(conn, query).items
        if item.decision_maker.full_name_raw == CONTACT_PEOPLE["founder"]
    )
    assert founder.reasons == ["historical_role"]

    def review(maker_id: str, decision: DecisionMakerDecision) -> DecisionMakerReview:
        request = DecisionMakerReviewRequest(
            decision_maker_id=maker_id, decision=decision, reason="Checked on the site (test)"
        )
        return store.review_decision_maker(conn, request, actor="test").decision_maker.review_status

    assert (
        review(founder.decision_maker.id, DecisionMakerDecision.CONFIRM)
        is DecisionMakerReview.CONFIRMED
    )
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT DISTINCT review_status FROM decision_makers WHERE full_name_normalized = ?",
            ("morgan placeholder",),
        )
    ] == [("confirmed",)]
    assert (
        review(founder.decision_maker.id, DecisionMakerDecision.RESET)
        is DecisionMakerReview.AMBIGUOUS
    )
    assert (
        review(owner.decision_maker.id, DecisionMakerDecision.REJECT)
        is DecisionMakerReview.REJECTED
    )
    assert f"alex.example@{PUBLISHED}" not in {
        c.value for c in detail(conn, seeded, "published").hypotheses
    }


def test_published_names_never_feed_latino_evidence(
    conn: sqlite3.Connection, synthetic: SyntheticContext, data_dir: Path
) -> None:
    site = "synthetic-bakery-0705.example"
    (candidate,) = insert_candidates(
        conn,
        synthetic,
        [
            {
                "name": "Synthetic Bakery 0705",
                "city": "Miami",
                "state": "FL",
                "zip": "33130",
                "website": f"https://{site}/",
            }
        ],
    )
    text = "Maria Garcia Lopez, Owner, bakes every morning. Test data only."
    insert_site_pages(
        conn,
        synthetic,
        site,
        {"/about": synthetic_page_html("en", "About", text)},
        candidate_id=candidate,
        data_dir=data_dir,
    )
    run(conn, data_dir, synthetic.project_id)
    business = business_of(conn, candidate)
    people = store.get_contact_detail(conn, business).decision_makers
    assert [view.decision_maker.full_name_raw for view in people] == ["Maria Garcia Lopez"]
    planned, _ = latino_store.plan_runs(conn, synthetic.project_id, actor="test")
    assert latino_engine.run_latino(conn, planned[0], lambda: False)
    shown = latino_store.get_latino_detail(conn, business).effective
    assert (shown.assessment.status if shown else LatinoStatus.UNKNOWN) is LatinoStatus.UNKNOWN
    (contact_evidence,) = conn.execute(
        "SELECT count(*) FROM evidence_items e JOIN extractions x ON x.id = e.extraction_id "
        "WHERE x.extractor LIKE 'contacts.%'"
    ).fetchone()
    assert contact_evidence == 0


# ---- suppressions --------------------------------------------------------------------------------


def business_row_phone(conn: sqlite3.Connection, project_id: str, business_id: str) -> str | None:
    query = BusinessListQuery(
        project_id=project_id,
        search=None,
        status=None,
        latino_status=None,
        latino_min_confidence=None,
        latino_source_kind=None,
        latino_review=None,
        sort=BusinessSort.NAME,
        limit=500,
        cursor=None,
    )
    page = repository.list_businesses(conn, query)
    return next(item.primary_phone for item in page.items if item.id == business_id)


def test_suppressions_reach_every_view_export_and_status_and_can_be_lifted(
    conn: sqlite3.Connection, seeded: ContactFixture, synthetic: SyntheticContext
) -> None:
    project = synthetic.project_id
    hello = next(c for c in detail(conn, seeded, "moved").channels if c.value == f"hello@{MOVED}")
    assert hello.validation_status is V.SUPPRESSED
    assert hello.suppression is not None
    assert hello.suppression.reason == "Asked not to be contacted (synthetic test data)"
    assert all(item.key != hello.key for item in contacts(conn, project).items)
    assert any(
        item.key == hello.key and item.suppressed
        for item in contacts(conn, project, include_suppressed=True).items
    )
    assert all(item.key != hello.key for item in store.export_channels(conn, project))

    business = detail(conn, seeded, "published").root_business_id
    assert business_row_phone(conn, project, business) == "+15125550171"
    add = SuppressionChangeRequest(
        suppression_id=None,
        kind=SuppressionKind.PHONE,
        value="(512) 555-0171",
        reason="Asked on the phone (test)",
    )
    entry = store.change_suppression(conn, add, actor="test")
    phone_rows = (
        "SELECT DISTINCT validation_status, do_not_contact FROM contact_points "
        "WHERE contact_type = 'phone' AND value_normalized = '+15125550171' AND deleted_at IS NULL"
    )
    assert [tuple(row) for row in conn.execute(phone_rows)] == [("suppressed", 1)]
    assert business_row_phone(conn, project, business) is None
    with pytest.raises(store.ContactError, match="already"):
        store.change_suppression(conn, add, actor="test")

    lift = SuppressionChangeRequest(
        suppression_id=entry.id, kind=None, value=None, reason="Consent given again (test)"
    )
    assert store.change_suppression(conn, lift, actor="test").lifted_at is not None
    assert [tuple(row) for row in conn.execute(phone_rows)] == [("observed", 0)]
    assert (
        by_key(store.get_contact_detail(conn, business))["phone:+15125550171"].validation_status
        is V.STRUCTURALLY_VALID
    )
    assert business_row_phone(conn, project, business) == "+15125550171"
    with pytest.raises(store.ContactError, match="already lifted"):
        store.change_suppression(conn, lift, actor="test")

    whole = SuppressionChangeRequest(
        suppression_id=None,
        kind=SuppressionKind.BUSINESS,
        value=business,
        reason="Opted out (test)",
    )
    store.change_suppression(conn, whole, actor="test")
    blocked = store.get_contact_detail(conn, business)
    assert blocked.business_suppression is not None
    assert blocked.contactability.score is None
    assert {channel.validation_status for channel in blocked.channels} == {V.SUPPRESSED}
    assert business_row_phone(conn, project, business) is None
    with pytest.raises(sqlite3.IntegrityError, match="permanent"):
        conn.execute("DELETE FROM contact_suppressions")
    with pytest.raises(sqlite3.IntegrityError, match="lifted, once"):
        conn.execute("UPDATE contact_suppressions SET reason = 'rewritten'")


def test_suppression_files_import_and_export_safely(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    content = (
        "kind,value,reason\n"
        "email,Owner@Shop.Example,=cmd|' /C calc'!A0\n"
        "phone,(512) 555-2368,\n"
        "domain,shop.example,asked in person\n"
        "email,owner@shop.example,again\n"
        "bogus,x,\n"
        "business,01900000-0000-7000-8000-000000000999,\n"
    )
    request = SuppressionImportRequest(filename="dnc.csv", content=content)
    result = store.import_suppressions(conn, request, actor="test")
    assert (result.rows, result.added, result.already_suppressed) == (6, 3, 1)
    assert [(problem.row, problem.code) for problem in result.problems] == [
        (6, "unknown_kind"),
        (7, "unknown_business"),
    ]
    query = SuppressionListQuery(kind=None, include_lifted=False, limit=2, cursor=None)
    first = store.list_suppressions(conn, query)
    second = store.list_suppressions(conn, query.model_copy(update={"cursor": first.next_cursor}))
    assert first.total == 3
    assert len(first.items) + len(second.items) == 3
    assert {entry.source for entry in [*first.items, *second.items]} == {"import:dnc.csv"}
    exported = store.export_suppressions(conn, SuppressionExportRequest(include_lifted=False))
    assert exported.rows == 3
    assert "'=cmd" in exported.content
    assert not any(line.split(",")[2].startswith("=") for line in exported.content.splitlines()[1:])
    again = store.import_suppressions(
        conn,
        SuppressionImportRequest(filename=exported.filename, content=exported.content),
        actor="test",
    )
    assert (again.added, again.already_suppressed, again.problems) == (0, 3, [])


# ---- mail domains --------------------------------------------------------------------------------


def test_mail_domains_are_cached_bounded_and_a_timeout_never_replaces_an_answer(
    conn: sqlite3.Connection,
) -> None:
    calls: list[str] = []
    answers = {"shop.example": MailDomainResult.MX}

    def lookup(domain: str) -> tuple[MailDomainResult, tuple[str, ...]]:
        calls.append(domain)
        result = answers.get(domain, MailDomainResult.NXDOMAIN)
        return result, (f"mx.{domain}",) if result is MailDomainResult.MX else ()

    now = utc_now()
    found = store.mail_results(conn, ["shop.example", "gone.example"], now, lookup)
    assert found == (
        {"shop.example": MailDomainResult.MX, "gone.example": MailDomainResult.NXDOMAIN},
        2,
        0,
    )
    assert store.mail_results(conn, ["shop.example"], now, lookup)[1:] == (0, 0)
    assert calls == ["gone.example", "shop.example"]
    # offline: only what is cached, never a lookup
    assert store.mail_results(conn, ["shop.example", "new.example"], now, None) == (
        {"shop.example": MailDomainResult.MX},
        0,
        0,
    )
    answers["shop.example"] = MailDomainResult.TIMEOUT
    expired = now + timedelta(days=8)
    assert store.mail_results(conn, ["shop.example"], expired, lookup) == (
        {"shop.example": MailDomainResult.MX},
        1,
        1,
    )
    assert store.mail_results(conn, ["other.example"], now, lookup, budget=[0])[1] == 0
    assert store.mail_results(conn, ["shop.example"], now, lookup, force=True)[1] == 1


def test_offline_runs_and_timeouts_leave_addresses_structurally_valid(
    conn: sqlite3.Connection, synthetic: SyntheticContext, data_dir: Path
) -> None:
    site = "synthetic-florist-0704.example"
    (candidate,) = insert_candidates(
        conn,
        synthetic,
        [
            {
                "name": "Synthetic Florist 0704",
                "city": "Austin",
                "state": "TX",
                "zip": "78701",
                "website": f"https://{site}/",
            }
        ],
    )
    insert_site_pages(
        conn,
        synthetic,
        site,
        {"/": synthetic_page_html("en", "Florist", f"Write to orders@{site}.")},
        candidate_id=candidate,
        data_dir=data_dir,
    )

    def refuse(domain: str) -> NoReturn:
        raise AssertionError(f"a run that may not look domains up looked up {domain}")

    def email() -> ContactChannel:
        found = store.get_contact_detail(conn, business_of(conn, candidate))
        return next(item for item in found.channels if item.contact_type is ContactType.EMAIL)

    job = run(conn, data_dir, synthetic.project_id, refuse, check=False)
    assert (email().validation_status, signal(email(), "mail_domain")) == (
        V.STRUCTURALLY_VALID,
        "not_checked",
    )
    assert store.get_contact_run(conn, job).stats.mail_domains_checked == 0

    def slow(domain: str) -> tuple[MailDomainResult, tuple[str, ...]]:
        return MailDomainResult.TIMEOUT, ()

    job = run(conn, data_dir, synthetic.project_id, slow)
    assert (email().validation_status, signal(email(), "mail_domain")) == (
        V.STRUCTURALLY_VALID,
        "timeout",
    )
    assert store.get_contact_run(conn, job).stats.mail_domain_failures == 1


# ---- actions, lists and jobs ---------------------------------------------------------------------


def test_actions_are_reviewed_written_at_once_and_revalidate(
    conn: sqlite3.Connection, seeded: ContactFixture
) -> None:
    business = detail(conn, seeded, "published").root_business_id

    def statuses() -> dict[str, tuple[ValidationStatus, str | None]]:
        found = store.get_contact_detail(conn, business)
        return {
            item.key: (item.validation_status, item.validation_method) for item in found.channels
        }

    info = f"email:info@{PUBLISHED}"
    act(conn, business, info, ContactAction.MARK_INVALID, "The mailbox bounced (test)")
    assert statuses()[info] == (V.INVALID, "manual_review")
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT DISTINCT validation_status FROM contact_points "
            "WHERE lower(value_normalized) = ? AND deleted_at IS NULL",
            (f"info@{PUBLISHED}",),
        )
    ] == [("invalid",)]
    act(conn, business, info, ContactAction.CLEAR_REVIEW, "A second look (test)")
    assert statuses()[info] == (V.LIKELY_VALID, "sources_agree")
    with pytest.raises(store.ContactError, match="no review"):
        act(conn, business, info, ContactAction.CLEAR_REVIEW)

    phone = "phone:+15125550171"
    with pytest.raises(store.ContactError, match="reason"):
        act(conn, business, phone, ContactAction.CONFIRM, None)
    act(conn, business, phone, ContactAction.CONFIRM, "Called by a colleague (test)")
    assert statuses()[phone] == (V.LIKELY_VALID, "manual_review")

    form = next(
        c.key
        for c in store.get_contact_detail(conn, business).channels
        if c.contact_type is ContactType.CONTACT_FORM
    )
    act(conn, business, form, ContactAction.MARK_PRIMARY, None)
    act(
        conn,
        business,
        f"email:sales@{PUBLISHED}",
        ContactAction.MARK_PRIMARY,
        "Sales answers (test)",
    )
    shown = store.get_contact_detail(conn, business)
    primary = {c.key for c in shown.channels if c.is_primary}
    assert {f"email:sales@{PUBLISHED}", form} <= primary
    assert f"email:info@{PUBLISHED}" not in primary

    calls: list[str] = []

    def refused(domain: str) -> tuple[MailDomainResult, tuple[str, ...]]:
        calls.append(domain)
        return MailDomainResult.NXDOMAIN, ()

    act(conn, business, info, ContactAction.VALIDATE, None, check=True, lookup=refused)
    assert calls == [PUBLISHED]
    assert statuses()[info] == (V.INVALID, "mail_domain")

    act(conn, business, phone, ContactAction.SUPPRESS, "Asked us to stop calling (test)")
    assert statuses()[phone] == (V.SUPPRESSED, "suppression_list")
    with pytest.raises(store.ContactError, match="phone or an email"):
        act(conn, business, form, ContactAction.SUPPRESS)
    with pytest.raises(repository.NotFoundError):
        act(conn, business, "phone:+19999999999", ContactAction.CONFIRM)


def test_contact_lists_filter_by_channel_quality_role_and_freshness_and_page_stably(
    conn: sqlite3.Connection, seeded: ContactFixture, synthetic: SyntheticContext
) -> None:
    project = synthetic.project_id
    everything = contacts(conn, project, include_suppressed=True)
    phones = contacts(conn, project, contact_type=ContactType.PHONE)
    assert phones.items
    assert {item.contact_type for item in phones.items} == {ContactType.PHONE}
    likely = contacts(conn, project, validation_status=V.LIKELY_VALID)
    assert likely.items
    assert {item.validation_status for item in likely.items} == {V.LIKELY_VALID}
    roles = {item.value for item in contacts(conn, project, role=ContactRole.ROLE_ADDRESS).items}
    assert {f"info@{PUBLISHED}", f"sales@{PUBLISHED}"} <= roles
    assert contacts(conn, project, freshness=ContactFreshness.STALE).total == 0
    seen, cursor = [], None
    while True:
        page = contacts(conn, project, include_suppressed=True, limit=3, cursor=cursor)
        assert page.total == everything.total
        seen += page.items
        if (cursor := page.next_cursor) is None:
            break
    assert [(item.business_id, item.key) for item in seen] == [
        (item.business_id, item.key) for item in everything.items
    ]
    with pytest.raises(repository.InvalidCursorError):
        contacts(conn, project, cursor=repository.encode_cursor("people", "x", seen[0].business_id))


def test_contact_runs_resume_and_repeat_without_writing_twice(
    conn: sqlite3.Connection, seeded: ContactFixture, synthetic: SyntheticContext, data_dir: Path
) -> None:
    def counts() -> tuple[int, ...]:
        return tuple(
            conn.execute(
                "SELECT (SELECT count(*) FROM contact_points), "
                "(SELECT count(*) FROM contact_validations), "
                "(SELECT count(*) FROM decision_makers), (SELECT count(*) FROM extractions)"
            ).fetchone()
        )

    before = counts()
    created, listed = store.plan_runs(conn, synthetic.project_id, True, actor="test")
    assert listed == []
    with pytest.raises(store.ContactError, match="already open"):
        store.plan_runs(conn, synthetic.project_id, True, actor="test")
    answers = iter([False, True])
    stop: Callable[[], bool] = lambda: next(answers, False)  # noqa: E731
    assert not engine.run_contacts(conn, data_dir, created[0], stop, synthetic_mail_lookup)
    assert repository.get(conn, Job, created[0]).status is JobStatus.RUNNING
    assert engine.run_contacts(conn, data_dir, created[0], lambda: False, synthetic_mail_lookup)
    finished = store.get_contact_run(conn, created[0])
    assert finished.status is JobStatus.SUCCEEDED
    assert finished.check_mail_domains
    assert (finished.stats.changed, finished.stats.contacts_found, finished.stats.hypotheses) == (
        0,
        0,
        0,
    )
    assert finished.stats.mail_domains_checked == 0  # every answer was still cached
    assert counts() == before
    first = store.get_contact_run(conn, seeded.job_id)
    assert first.stats.contacts_found > 0
    assert first.stats.hypotheses == 2
    assert first.stats.ambiguous_people >= 1
    assert first.stats.suppressed >= 1


def test_performance_on_many_contact_pages(
    conn: sqlite3.Connection, synthetic: SyntheticContext, data_dir: Path
) -> None:
    sites = [f"synthetic-shop-{index:04d}.example" for index in range(150)]
    rows = [
        {
            "name": f"Synthetic Shop {index:04d}",
            "city": "Austin",
            "state": "TX",
            "zip": "78701",
            "phone": f"(512) 555-{100 + index % 100:04d}",
            "website": f"https://{site}/",
        }
        for index, site in enumerate(sites)
    ]
    candidates = insert_candidates(conn, synthetic, rows)
    for index, (site, candidate) in enumerate(zip(sites, candidates, strict=True)):
        text = (
            f"Call (512) 555-{100 + index % 100:04d} or write to info@{site}. "
            f"Alex Example, Owner. Test data."
        )
        insert_site_pages(
            conn,
            synthetic,
            site,
            {"/": synthetic_page_html("en", "Shop", text)},
            candidate_id=candidate,
            data_dir=data_dir,
        )
    started = time.perf_counter()
    run(conn, data_dir, synthetic.project_id)
    elapsed = time.perf_counter() - started
    started = time.perf_counter()
    page = contacts(conn, synthetic.project_id, limit=50)
    listed = time.perf_counter() - started
    assert page.total >= 300
    assert len(page.items) == 50
    assert elapsed < 120, elapsed
    assert listed < 10, listed
