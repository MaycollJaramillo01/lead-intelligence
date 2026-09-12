"""Normalisers are pure: a raw value in, its normalised form and structure out, never invented."""

import pytest

from research_engine.resolution import normalize
from research_engine.resolution.normalize import PhoneKind, Presence


@pytest.mark.parametrize(
    ("raw", "expected", "presence"),
    [
        ("  Caf&eacute;   Ol&eacute; ", "Café Olé", Presence.PRESENT),
        ("CafÃ© OlÃ©", "Café Olé", Presence.PRESENT),  # UTF-8 read as windows-1252
        ("Zero\u200bwidth\u00a0space", "Zerowidth space", Presence.PRESENT),
        ("N/A", None, Presence.MISSING),
        ("", None, Presence.MISSING),
        (None, None, Presence.MISSING),
        ("unknown", None, Presence.UNKNOWN),
        ("Not applicable", None, Presence.NOT_APPLICABLE),
        (42, "42", Presence.PRESENT),
    ],
)
def test_text_is_cleaned_without_inventing_values(
    raw: object, expected: str | None, presence: Presence
) -> None:
    assert normalize.clean_text(raw) == (expected, presence)


@pytest.mark.parametrize(
    ("raw", "key", "suffix"),
    [
        ("Rivera Plumbing LLC", "rivera plumbing", "llc"),
        ("Rivera Plumbing, L.L.C.", "rivera plumbing", "llc"),
        ("RIVERA PLUMBING, INC.", "rivera plumbing", "inc"),
        ("The Smith & Sons Co", "smith and sons", "co"),
        ("Panadería El Niño S.A. de C.V.", "panaderia el nino", "sadecv"),
        ("Acme Bros. Intl", "acme brothers international", None),
        ("Company", "company", None),  # never reduced to nothing
    ],
)
def test_business_names_keep_the_original_and_compare_by_key(
    raw: str, key: str, suffix: str | None
) -> None:
    name = normalize.business_name(raw)
    assert (name.display, name.raw) == (raw, raw)
    assert (name.key, name.legal_suffix) == (key, suffix)


def test_dba_generic_and_empty_names() -> None:
    dba = normalize.business_name("Fixture Holdings LLC dba Sol Taqueria")
    assert (dba.key, dba.dba, dba.legal_suffix) == ("sol taqueria", "Sol Taqueria", "llc")
    assert dba.distinctive == ("sol",)
    assert normalize.business_name("Cleaning Services LLC").is_generic
    assert normalize.business_name("Garcia Rofing").distinctive == ("garcia",)  # typo of a trade
    assert not normalize.business_name("Garza Roofing").is_generic
    assert normalize.business_name(" - ").status is Presence.MISSING
    assert normalize.business_name("LLC").key == "llc"
    assert normalize.business_name("!!!").status is Presence.INVALID


@pytest.mark.parametrize(
    ("raw", "e164", "extension", "kind"),
    [
        ("(512) 555-0101", "+15125550101", None, PhoneKind.FICTIONAL),
        ("512.555.0101", "+15125550101", None, PhoneKind.FICTIONAL),
        ("+1 512 555 0101 ext. 42", "+15125550101", "42", PhoneKind.FICTIONAL),
        ("Tel: 1-512-555-0101 x7", "+15125550101", "7", PhoneKind.FICTIONAL),
        ("(512) 482-1234", "+15124821234", None, PhoneKind.GEOGRAPHIC),
        ("1-800-555-0199", "+18005550199", None, PhoneKind.TOLL_FREE),
        ("1-800-FLOWERS", "+18003569377", None, PhoneKind.TOLL_FREE),
        ("+52 55 1234 5678", "+525512345678", None, PhoneKind.INTERNATIONAL),
    ],
)
def test_phones_become_e164_with_the_extension_apart(
    raw: str, e164: str, extension: str | None, kind: PhoneKind
) -> None:
    result = normalize.phone(raw)
    assert (result.e164, result.extension, result.kind, result.status) == (
        e164,
        extension,
        kind,
        Presence.PRESENT,
    )


@pytest.mark.parametrize("raw", ["555-0101", "(112) 555-0101", "(512) 055-0101", "12345", "+1 23"])
def test_structurally_invalid_phones_are_not_guessed(raw: str) -> None:
    result = normalize.phone(raw)
    assert (result.status, result.e164) == (Presence.INVALID, None)


def test_a_missing_phone_is_missing_not_invalid() -> None:
    assert normalize.phone(None).status is Presence.MISSING
    assert normalize.phone("n/a").status is Presence.MISSING


def test_emails_keep_the_local_part_and_fold_the_domain() -> None:
    result = normalize.email("  mailto:Ventas.Norte+web@Ejemplo-Niño.Example?subject=hi ")
    assert result.domain is not None
    assert result.domain.startswith("xn--")
    assert result.domain.encode().decode("idna") == "ejemplo-niño.example"
    assert result.address == f"Ventas.Norte+web@{result.domain}"
    assert result.match_key == f"ventas.norte+web@{result.domain}"
    assert (result.local, result.is_role) == ("Ventas.Norte+web", False)
    assert normalize.email("INFO@sample.example").is_role
    assert normalize.email("someone@gmail.com").is_free_provider
    for bad in ("no-at-sign.example", "a@@b.example", ".dot@x.example", "a..b@x.example", "a@x"):
        assert normalize.email(bad).status is Presence.INVALID, bad


@pytest.mark.parametrize(
    ("raw", "url", "registrable"),
    [
        ("http://WWW.Rivera.example/?utm_source=a&b=2&gclid=x#top", "http://rivera.example?b=2",
         "rivera.example"),
        ("rivera.example/index.html", "https://rivera.example", "rivera.example"),
        ("https://rivera.example:443/about/", "https://rivera.example/about", "rivera.example"),
        ("https://shop.rivera.example", "https://shop.rivera.example", "rivera.example"),
        ("https://joes.wixsite.com/pizza", "https://joes.wixsite.com/pizza", "joes.wixsite.com"),
        ("https://www.fixture.co.uk", "https://fixture.co.uk", "fixture.co.uk"),
    ],
)  # fmt: skip
def test_websites_are_canonical_and_know_their_registrable_domain(
    raw: str, url: str, registrable: str
) -> None:
    result = normalize.website(raw)
    assert (result.url, result.registrable_domain) == (url, registrable)


def test_international_domains_are_stored_as_punycode() -> None:
    result = normalize.website("https://WWW.Panadería.example/")
    assert result.host is not None
    assert result.host.startswith("xn--")
    assert result.host.encode().decode("idna") == "panadería.example"
    assert result.url == f"https://{result.host}"


def test_platform_pages_and_bad_urls() -> None:
    facebook = normalize.website("https://facebook.com/RiveraPlumbing")
    assert (facebook.is_platform, facebook.registrable_domain) == (True, "facebook.com")
    for bad in ("mailto:a@b.example", "ftp://files.example", "http://127.0.0.1/", "localhost"):
        assert normalize.website(bad).status is Presence.INVALID, bad


@pytest.mark.parametrize(
    ("raw", "platform", "handle"),
    [
        ("https://www.facebook.com/RiveraPlumbing/", "facebook", "riveraplumbing"),
        ("https://facebook.com/profile.php?id=1234", "facebook", "id:1234"),
        ("https://instagram.com/Rivera.Plumbing?igshid=abc", "instagram", "rivera.plumbing"),
        ("https://twitter.com/RiveraPlumb", "x", "riveraplumb"),
        ("https://www.linkedin.com/company/Rivera-Plumbing/about", "linkedin",
         "company/rivera-plumbing"),
        ("https://www.youtube.com/channel/UCaBcD", "youtube", "channel/UCaBcD"),
        ("https://wa.me/15125550101", "whatsapp", "15125550101"),
    ],
)  # fmt: skip
def test_social_profiles_become_platform_and_handle(raw: str, platform: str, handle: str) -> None:
    result = normalize.social_profile(raw)
    assert (result.platform, result.handle, result.status) == (platform, handle, Presence.PRESENT)


def test_share_links_are_not_profiles() -> None:
    assert normalize.social_profile("https://www.facebook.com/sharer/sharer.php?u=x").handle is None
    assert normalize.social_profile("https://rivera.example").status is Presence.NOT_APPLICABLE


def test_us_addresses_are_split_into_components() -> None:
    result = normalize.us_address(
        "1200 North Example Avenue Suite 4, Austin, Texas 78701-2233, USA"
    )
    assert (result.street, result.street_number, result.street_key) == (
        "1200 N Example Ave",
        "1200",
        "n example ave",
    )
    assert (result.unit, result.unit_key) == ("Ste 4", "4")
    assert (result.locality, result.region, result.postal_code, result.zip4) == (
        "Austin",
        "TX",
        "78701",
        "2233",
    )
    assert result.country_code == "US"
    hashed = normalize.us_address("22 Sample St #210, Newark, NJ 7102")
    assert (hashed.unit, hashed.unit_key, hashed.postal_code) == ("# 210", "210", "07102")
    city_only = normalize.us_address("Austin, TX")
    assert (city_only.street, city_only.locality, city_only.region) == (None, "Austin", "TX")


def test_address_components_win_and_nothing_is_invented() -> None:
    result = normalize.us_address(
        street="100 Sample Rd", locality_value="SAINT PAUL", region="minnesota", postal_code="55101"
    )
    assert (result.street, result.locality, result.locality_key, result.region) == (
        "100 Sample Rd",
        "Saint Paul",
        "saint paul",
        "MN",
    )
    assert result.country_code is None  # a state code is not a country the source gave
    assert normalize.us_address(None).status is Presence.MISSING
    assert normalize.us_address("somewhere nice").street == "somewhere nice"
    assert normalize.us_address(latitude=91, longitude=10).status is Presence.MISSING


def test_places() -> None:
    assert normalize.us_region("Texas") == ("TX", Presence.PRESENT)
    assert normalize.us_region("tx") == ("TX", Presence.PRESENT)
    assert normalize.us_region("Tejas") == (None, Presence.INVALID)
    assert normalize.locality("St. Louis").key == "saint louis"
    assert normalize.county("Travis County").display == "Travis"
    assert normalize.metro("Austin-Round Rock Metro Area").key == "austin round rock"


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("Commercial Bakeries", "bakery"),
        ("Panadería y Pastelería", "bakery"),
        ("Plumbing, Heating, and Air-Conditioning Contractors", "plumbing"),
        ("Heating and Air", "hvac"),
        ("Offices of Dentists", "dental"),
        ("Underwater basket weaving", None),
    ],
)
def test_categories_map_to_a_versioned_vocabulary_and_keep_the_label(
    raw: str, key: str | None
) -> None:
    result = normalize.category(raw)
    assert (result.label, result.taxonomy_key) == (raw, key)
    assert result.taxonomy_version == (normalize.TAXONOMY_VERSION if key else None)


def test_normalisers_are_deterministic() -> None:
    values = ["Rivera Plumbing LLC", "(512) 555-0101", "http://www.x.example/?utm_source=1"]
    first = (normalize.business_name(values[0]), normalize.phone(values[1]),
             normalize.website(values[2]))  # fmt: skip
    again = (normalize.business_name(values[0]), normalize.phone(values[1]),
             normalize.website(values[2]))  # fmt: skip
    assert first == again


@pytest.mark.parametrize(
    "raw",
    [
        "100 Example Avenue, Ste 4",
        "100 Example Ave, Suite 4, Austin, TX 78701",
        "100 Example Ave #4",
    ],
)
def test_a_unit_is_found_wherever_it_was_written(raw: str) -> None:
    result = normalize.us_address(raw)
    assert (result.street, result.unit_key) == ("100 Example Ave", "4")
    assert result.locality in (None, "Austin")
