"""Latino evidence rules (phase 07): labelled fixtures for what is evidence and what never is, and
how evidence becomes a status. Pure: no store and no clock. Synthetic test data only."""

import itertools
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from research_engine.domain import (
    EvidenceFreshness,
    EvidenceRole,
    EvidenceStatus,
    LatinoStatus,
    LatinoTrack,
)
from research_engine.latino import rules

NOW = datetime(2026, 6, 1, tzinfo=UTC)
MARKET = sorted(claim for claim, rule in rules.CLAIMS.items() if rule.track is LatinoTrack.MARKET)
OWNERSHIP_CLAIMS = {"ownership_statement", "ownership_mention"}
_ids = itertools.count(1)


def page(
    text: str = "",
    *,
    title: str | None = None,
    headings: tuple[str, ...] = (),
    lang: str | None = "en",
    links: tuple[dict[str, str], ...] = (),
) -> dict[str, object]:
    """A crawled page the way `crawl.extract.snapshot` stores it (only what the rules read)."""
    return {
        "title": title,
        "lang": lang,
        "meta": {},
        "headings": [{"level": 2, "text": heading} for heading in headings],
        "main_text": text,
        "links": list(links),
        "json_ld": [],
    }


def claims(findings: list[rules.Finding]) -> set[str]:
    return {finding.claim for finding in findings}


def fact(
    claim: str,
    *,
    kind: str = "business_website",
    source: str = "site:synthetic-bakery.example",
    days_old: int = 10,
    status: EvidenceStatus = EvidenceStatus.ACTIVE,
    organizational: bool = False,
    expires_in_days: int | None = None,
) -> rules.Fact:
    expires = None if expires_in_days is None else (NOW + timedelta(days=expires_in_days)).date()
    return rules.Fact(
        evidence_id=f"evidence-{next(_ids):05d}",
        claim=claim,
        status=status,
        source_kind=kind,
        source_key=source,
        observed_at=NOW - timedelta(days=days_old),
        organizational=organizational,
        expires_on=expires,
    )


def evaluate(*facts: rules.Fact) -> rules.Evaluation:
    return rules.evaluate(list(facts), NOW)


def warnings(evaluation: rules.Evaluation) -> set[str]:
    return {note.code for note in evaluation.warnings}


# ---- explicit declarations ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("found_in", "expected"),
    [
        (page("We are a proud Latino-owned family business. Test data."), "ownership_statement"),
        (page("Somos una empresa de propiedad latina desde 2001."), "ownership_statement"),
        (page(headings=("Latina-owned bakery on Example Ave",)), "ownership_statement"),
        (
            page("The bakery is Hispanic\N{NON-BREAKING HYPHEN}owned and operated."),
            "ownership_mention",
        ),
        (page("Owned and operated by two Mexican-American sisters."), "ownership_mention"),
    ],
)
def test_an_explicit_self_declaration_is_ownership_evidence(
    found_in: dict[str, object], expected: str
) -> None:
    found = rules.scan_page(found_in)
    assert claims(found) == {expected}
    assert found[0].snippet  # the minimal fragment that shows it is kept
    assert len(found[0].snippet) <= rules.SNIPPET_CHARS


@pytest.mark.parametrize(
    "text",
    [
        "We support Latino-owned businesses in Austin.",
        "Find Hispanic-owned restaurants near you.",
        "Our directory lists Latina-owned vendors across Texas.",
        "Apoyamos negocios de propiedad latina en la ciudad.",
    ],
)
def test_talking_about_other_latino_owned_businesses_is_not_a_claim(text: str) -> None:
    assert not claims(rules.scan_page(page(text))) & OWNERSHIP_CLAIMS


def test_a_self_declaration_alone_is_high_confidence_but_never_verified() -> None:
    declared = evaluate(fact("ownership_statement"))
    assert declared.status is LatinoStatus.HIGH_CONFIDENCE_LATINO_OWNED
    assert "self_declared" in declared.cannot_conclude
    untrusted = evaluate(fact("ownership_statement", kind="web_search", source="search:1"))
    assert untrusted.status is LatinoStatus.PROBABLE_LATINO_OWNED
    assert "untrusted_source" in warnings(untrusted)
    assert evaluate(fact("ownership_mention")).status is LatinoStatus.PROBABLE_LATINO_OWNED


# ---- directories and certifications -------------------------------------------------------------


def test_certifications_platform_attributes_and_directories_are_read_from_their_columns() -> None:
    certified = rules.scan_payload(
        {
            "name": "Synthetic Auto Repair 0501",
            "certification": "Hispanic-owned business enterprise",
            "certification_expires": "2030-01-01",
        }
    )
    assert [(f.claim, f.field_path, dict(f.detail)) for f in certified] == [
        ("certification", "payload.certification", {"expires_on": "2030-01-01"})
    ]
    assert claims(rules.scan_payload({"Latino-owned": "Yes"})) == {"ownership_attribute"}
    assert claims(rules.scan_payload({"hispanic_owned": "no"})) == {"ownership_denied"}
    listed = rules.scan_payload({"directory": "Latino-owned business directory"})
    assert claims(listed) == {"ownership_directory"}
    chamber = rules.scan_payload({"membership": "Hispanic Chamber of Commerce"})
    assert claims(chamber) == {"community_directory"}  # its criterion is not ownership
    spoken = rules.scan_payload({"languages": ["English", "Spanish"]})
    assert claims(spoken) == {"spanish_language_attribute"}


def test_a_trusted_certification_verifies_and_a_directory_is_probable() -> None:
    registry = evaluate(fact("certification", kind="public_registry", source="connector:registry"))
    assert registry.status is LatinoStatus.VERIFIED_LATINO_OWNED
    assert "self_declared" not in registry.cannot_conclude
    assert registry.confidence == pytest.approx(0.855)

    directory = evaluate(fact("ownership_directory", kind="directory", source="connector:dir"))
    assert directory.status is LatinoStatus.PROBABLE_LATINO_OWNED
    platform = evaluate(fact("ownership_attribute", kind="platform_api", source="connector:app"))
    assert platform.status is LatinoStatus.HIGH_CONFIDENCE_LATINO_OWNED

    # two independent sources agree; the same source twice is still one source
    independent = evaluate(
        fact("ownership_mention"),
        fact("ownership_directory", kind="directory", source="connector:dir"),
    )
    assert independent.status is LatinoStatus.HIGH_CONFIDENCE_LATINO_OWNED
    repeated = evaluate(fact("ownership_mention"), fact("ownership_mention"))
    assert repeated.status is LatinoStatus.PROBABLE_LATINO_OWNED


# ---- market affinity ----------------------------------------------------------------------------


def test_a_spanish_website_without_a_declaration_is_market_affinity() -> None:
    spanish = page(
        "Se habla español. Servicios en español para la comunidad hispana de Example Ave.",
        lang="es-US",
        links=({"text": "Español", "url": "https://synthetic-cleaning.example/es/"},),
    )
    found = claims(rules.scan_page(spanish))
    assert found == {"spanish_service", "hispanic_community", "spanish_page", "spanish_version"}
    result = evaluate(*(fact(claim) for claim in sorted(found)))
    assert result.status is LatinoStatus.SPANISH_MARKET_AFFINITY
    assert "affinity_is_not_ownership" in result.cannot_conclude
    assert all(factor.role is EvidenceRole.SUPPORTS and factor.counted for factor in result.factors)


def test_mostly_spanish_text_is_a_spanish_page_even_without_a_lang_attribute() -> None:
    text = (
        "Somos una empresa de servicios de calidad para nuestros clientes. Ofrecemos también "
        "trabajo de contacto en la ciudad con los mejores años de experiencia y la calidad que "
        "nuestras familias esperan de su empresa, por eso llame hoy para más servicios."
    )
    assert claims(rules.scan_page(page(text, lang=None))) == {"spanish_page"}


def test_no_mix_of_market_signals_from_any_source_ever_reaches_ownership() -> None:
    for size in range(1, len(MARKET) + 1):
        for combination in itertools.combinations(MARKET, size):
            for kind in rules.SOURCE_RELIABILITY:
                facts = [
                    fact(claim, kind=kind, source=f"{kind}:{index}")
                    for index, claim in enumerate(combination)
                ]
                assert evaluate(*facts).status is LatinoStatus.SPANISH_MARKET_AFFINITY


# ---- never evidence -----------------------------------------------------------------------------

SURNAMES = ("García", "Hernández", "Rodríguez", "López", "Martínez", "González", "Pérez", "Sánchez")


def test_names_surnames_photos_and_places_never_raise_the_status_above_unknown() -> None:
    for first, last in itertools.product(("María", "José", "Synthetic"), SURNAMES):
        payload = {
            "name": f"{last} & Sons Roofing",
            "dba": f"Taller {last}",
            "owner_name": f"{first} {last}",
            "contact_person": f"{first} {last}",
            "photo_url": "https://synthetic-roofing.example/owner.jpg",
            "photo_alt": f"Photo of {first} {last}",
            "neighborhood": "Little Havana",
            "nationality": "Mexican",
            "ethnicity": "Hispanic",
            "race": "Hispanic or Latino",
            "surname_origin": "Spanish",
        }
        assert rules.scan_payload(payload) == []
        about = page(
            f"{first} {last} opened {last} Family Restaurant in Little Havana. Authentic Mexican "
            f"and Cuban food. Photo: the {last} family.",
            title=f"{last} Family Restaurant",
            headings=(f"Meet the owner, {first} {last}",),
        )
        assert rules.scan_page(about) == []
    nothing = evaluate()
    assert nothing.status is LatinoStatus.UNKNOWN
    assert nothing.confidence == 0
    assert {"names_not_used", "absence_is_not_evidence"} <= set(nothing.cannot_conclude)
    assert evaluate(fact("a_claim_these_rules_do_not_know")).status is LatinoStatus.UNKNOWN


# ---- contradictions -----------------------------------------------------------------------------


def test_a_newer_reliable_denial_contradicts_a_declaration_and_is_shown() -> None:
    declared = fact("ownership_statement", days_old=100)
    denied = fact("ownership_denied", kind="public_registry", source="connector:registry")
    assert rules.contradicted([declared, denied]) == {declared.evidence_id}

    result = evaluate(replace(declared, status=EvidenceStatus.CONTRADICTED), denied)
    assert result.status is LatinoStatus.UNKNOWN
    assert "contradicted" in warnings(result)
    roles = {factor.evidence_id: (factor.role, factor.counted) for factor in result.factors}
    assert roles[declared.evidence_id] == (EvidenceRole.SUPPORTS, False)
    assert roles[denied.evidence_id][0] is EvidenceRole.CONTRADICTS


def test_a_weaker_denial_lowers_the_status_instead_of_being_ignored() -> None:
    certified = fact("certification", kind="public_registry", source="connector:registry")
    denied = fact("ownership_denied", kind="web_search", source="search:1", days_old=1)
    assert rules.contradicted([certified, denied]) == set()  # less reliable: it cannot contradict
    alone = evaluate(certified)
    result = evaluate(certified, denied)
    assert result.status is LatinoStatus.PROBABLE_LATINO_OWNED
    assert result.confidence < alone.confidence
    assert "contradicted" in warnings(result)


def test_new_ownership_makes_older_declarations_stop_counting() -> None:
    result = evaluate(fact("ownership_statement", days_old=200), fact("ownership_changed"))
    assert result.status is LatinoStatus.UNKNOWN
    assert "ownership_changed" in warnings(result)


# ---- expired, stale and removed evidence --------------------------------------------------------


def test_expired_old_or_removed_evidence_stops_counting_and_says_so() -> None:
    expired = fact("certification", kind="public_registry", source="registry", expires_in_days=-1)
    assert rules.is_stale(expired, NOW)
    result = evaluate(expired)
    assert (result.status, "stale_evidence" in warnings(result)) == (LatinoStatus.UNKNOWN, True)

    too_old = fact("ownership_statement", days_old=1200)
    assert rules.is_stale(too_old, NOW)
    aging = fact("ownership_statement", days_old=700)
    value = rules.freshness(aging.claim, aging.observed_at, NOW)
    assert 0.5 < value < 1
    assert rules.freshness_label(value) is EvidenceFreshness.AGING
    assert evaluate(aging).confidence < evaluate(fact("ownership_statement")).confidence

    removed = evaluate(fact("ownership_statement", status=EvidenceStatus.REVOKED))
    assert removed.status is LatinoStatus.UNKNOWN
    assert "revoked_evidence" in warnings(removed)


# ---- several locations ---------------------------------------------------------------------------


def test_evidence_an_organization_shares_across_locations_caps_ownership() -> None:
    shared = fact("certification", kind="public_registry", source="registry", organizational=True)
    result = evaluate(shared)
    assert result.status is LatinoStatus.PROBABLE_LATINO_OWNED
    assert "organizational_evidence" in warnings(result)
    assert "location_ownership_unknown" in result.cannot_conclude
    own = fact("certification", kind="public_registry", source="registry")
    assert evaluate(shared, own).status is LatinoStatus.VERIFIED_LATINO_OWNED


# ---- model candidates ---------------------------------------------------------------------------


def test_a_model_may_point_at_a_declaration_but_never_decides_one() -> None:
    about = page(
        "Fresh bread every morning. We are proud to be a Latino-owned bakery. Se habla español."
    )
    accepted = rules.verify_candidate(
        "ownership_statement", "We are proud to be a  Latino-owned bakery", about
    )
    assert isinstance(accepted, rules.Finding)
    assert (accepted.claim, accepted.detail["proposed_by"]) == ("ownership_statement", "model")

    rejected = {
        ("spanish_service", "Se habla español"): "claim_not_supported",
        ("ownership_statement", "Owned"): "quote_too_short",
        ("ownership_statement", "The owners were born in Mexico"): "quote_not_found",
        ("ownership_statement", "Fresh bread every morning"): "not_a_declaration",
        ("certification_statement", "We are proud to be a Latino-owned bakery"): (
            "not_a_declaration"
        ),
    }
    for (claim, quote), reason in rejected.items():
        assert rules.verify_candidate(claim, quote, about) == reason, (claim, quote)


# ---- determinism and versions -------------------------------------------------------------------


def test_the_same_evidence_and_rules_always_give_the_same_result() -> None:
    facts = [
        fact("ownership_mention"),
        fact("ownership_directory", kind="directory", source="connector:dir"),
        fact("spanish_service"),
        fact("ownership_denied", kind="web_search", source="search:1"),
    ]
    first = rules.evaluate(facts, NOW)
    assert rules.evaluate(list(reversed(facts)), NOW) == first
    newer = rules.evaluate(facts, NOW, rules.Ruleset(version="1.0.0-test"))
    assert newer.status is first.status
    assert newer.rationale.startswith("Rules 1.0.0-test:")
