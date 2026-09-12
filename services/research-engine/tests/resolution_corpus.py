"""SYNTHETIC TEST DATA: labelled corpora for entity resolution (phase 06).

Every value is fictitious: domains use the reserved `.example` top-level domain, phone numbers use
the 555-01xx range reserved for fiction, streets are named Example, Sample, Demo, Fixture or Test,
and no person is represented. Names are built from neutral syllables and trade words; nothing here
(and nothing in the engine) uses a surname, a language or a presumed origin to decide identity.

`LABELLED` holds the hard cases the prompt lists, each record tagged with the real-world entity it
belongs to. `generated` scales the same kinds of variation to tens of thousands of records.
"""

import random
from collections.abc import Iterator
from dataclasses import dataclass

from research_engine.resolution.matching import AddressInput, EntityInput


def record_id(index: int) -> str:
    return f"01900000-0000-7000-8000-{index:012d}"


@dataclass(frozen=True, slots=True)
class Labelled:
    #: The entity this record really is. Two records are a true match when their entities agree.
    entity: str
    tag: str
    record: EntityInput


def _rows() -> Iterator[tuple[str, str, dict[str, object]]]:
    # 1. the same business with different legal suffixes, phone formats and URL variants
    yield (
        "rivera",
        "rivera-llc",
        {
            "names": ("Rivera Plumbing LLC",),
            "addresses": (AddressInput(raw="1200 Example Ave Suite 4, Austin, TX 78701"),),
            "websites": ("http://www.riveraplumbing.example/?utm_source=directory",),
            "phones": ("(512) 555-0110",),
            "categories": ("Plumbing",),
        },
    )
    yield "rivera", "rivera-inc", {
        "names": ("Rivera Plumbing, Inc.",),
        "addresses": (AddressInput(street="1200 Example Avenue", unit="Ste 4", locality="Austin",
                                   region="Texas", postal_code="78701-2233"),),
        "websites": ("https://riveraplumbing.example",),
        "phones": ("512.555.0110",),
        "categories": ("plumbing, heating and air-conditioning contractors",),
    }  # fmt: skip
    yield (
        "rivera",
        "rivera-caps",
        {
            "names": ("RIVERA PLUMBING",),
            "addresses": (AddressInput(locality="austin", region="tx"),),
            "websites": ("riveraplumbing.example/index.html",),
            "phones": ("+1 512 555 0110 ext. 3",),
        },
    )
    # 2. legitimate branches of one brand: two establishments, plus a second record of one of them
    yield (
        "sunrise-downtown",
        "sunrise-downtown",
        {
            "names": ("Sunrise Dental",),
            "addresses": (AddressInput(raw="45 Sample St, Austin, TX 78702"),),
            "websites": ("https://sunrisedental.example/",),
            "phones": ("(512) 555-0120",),
            "categories": ("Dentist",),
        },
    )
    yield (
        "sunrise-north",
        "sunrise-north",
        {
            "names": ("Sunrise Dental",),
            "addresses": (AddressInput(raw="9800 Demo Rd, Round Rock, TX 78664"),),
            "websites": ("https://sunrisedental.example/round-rock",),
            "phones": ("(512) 555-0121",),
            "categories": ("Dentist",),
        },
    )
    yield (
        "sunrise-north",
        "sunrise-north-again",
        {
            "names": ("Sunrise Dental - Round Rock",),
            "addresses": (AddressInput(raw="9800 Demo Road, Round Rock, Texas 78664"),),
            "phones": ("512-555-0121",),
        },
    )
    # 3. a franchise: same name, same site, the same toll-free line, different cities and states
    for tag, place in (
        ("quicklube-austin", "10 Fixture Pkwy, Austin, TX 78745"),
        ("quicklube-dallas", "220 Fixture Parkway, Dallas, TX 75201"),
        ("quicklube-miami", "31 Fixture Blvd, Miami, FL 33130"),
    ):
        yield (
            tag,
            tag,
            {
                "names": ("QuickLube Express",),
                "addresses": (AddressInput(raw=place),),
                "websites": ("https://www.quicklube.example",),
                "phones": ("1-800-555-0199",),
                "categories": ("Auto repair",),
            },
        )
    # 4. generic names: equal in different states, equal in the same city at another address
    yield (
        "cleaning-houston",
        "cleaning-houston",
        {
            "names": ("Cleaning Services",),
            "addresses": (AddressInput(raw="10 Fixture Ln, Houston, TX 77002"),),
            "phones": ("(713) 555-0130",),
        },
    )
    yield (
        "cleaning-tampa",
        "cleaning-tampa",
        {
            "names": ("Cleaning Services LLC",),
            "addresses": (AddressInput(raw="22 Fixture Ln, Tampa, FL 33602"),),
            "phones": ("(813) 555-0131",),
        },
    )
    yield (
        "cleaning-houston-2",
        "cleaning-houston-2",
        {
            "names": ("Cleaning Services",),
            "addresses": (AddressInput(raw="500 Test St, Houston, TX 77003"),),
            "phones": ("(713) 555-0132",),
        },
    )
    # 5. an address change: same name, site and local phone, another street, observed later
    yield (
        "blue-heron",
        "blue-heron-old",
        {
            "names": ("Blue Heron Bakery",),
            "addresses": (AddressInput(raw="12 Sample Mill Rd, Denver, CO 80202"),),
            "websites": ("blueheron.example",),
            "phones": ("(303) 555-0140",),
        },
    )
    yield (
        "blue-heron",
        "blue-heron-new",
        {
            "names": ("Blue Heron Bakery LLC",),
            "addresses": (AddressInput(raw="880 Demo Market St, Denver, CO 80205"),),
            "websites": ("https://blueheron.example/",),
            "phones": ("303-555-0140",),
        },
    )
    # 6. a small typo (the same business) versus a similar name (another business)
    yield (
        "garcia",
        "garcia",
        {
            "names": ("Garcia Roofing LLC",),
            "addresses": (AddressInput(raw="77 Test Dr, Phoenix, AZ 85004"),),
            "phones": ("(602) 555-0150",),
        },
    )
    yield (
        "garcia",
        "garcia-typo",
        {
            "names": ("Garcia Rofing",),
            "addresses": (AddressInput(raw="77 Test Drive, Phoenix, AZ 85004"),),
            "phones": ("602 555 0150",),
        },
    )
    yield (
        "garza",
        "garza",
        {
            "names": ("Garza Roofing",),
            "addresses": (AddressInput(raw="300 Sample Blvd, Phoenix, AZ 85008"),),
            "phones": ("(602) 555-0151",),
        },
    )
    # 7. incomplete data: a name and a city on one side, everything on the other
    yield (
        "lotus",
        "lotus-thin",
        {
            "names": ("Lotus Tax Preparation",),
            "addresses": (AddressInput(locality="Chicago", region="IL"),),
        },
    )
    yield (
        "lotus",
        "lotus-full",
        {
            "names": ("Lotus Tax Preparation",),
            "addresses": (AddressInput(raw="60 Example Plz, Chicago, IL 60601"),),
            "websites": ("lotustax.example",),
            "phones": ("(312) 555-0160",),
        },
    )
    # 8. the same name in the same city: two different businesses
    yield (
        "joes-midtown",
        "joes-midtown",
        {
            "names": ("Joe's Pizza",),
            "addresses": (AddressInput(raw="1 Fixture Ave, New York, NY 10001"),),
            "phones": ("(212) 555-0170",),
        },
    )
    yield (
        "joes-uptown",
        "joes-uptown",
        {
            "names": ("Joes Pizza",),
            "addresses": (AddressInput(raw="900 Demo St, New York, NY 10025"),),
            "phones": ("(212) 555-0171",),
        },
    )
    # 9. one shared office line in one building, two businesses in different suites
    yield (
        "northside",
        "northside",
        {
            "names": ("Northside Accounting",),
            "addresses": (AddressInput(raw="400 Example Ave Suite 10, Miami, FL 33131"),),
            "phones": ("(305) 555-0180",),
        },
    )
    yield (
        "harbor",
        "harbor",
        {
            "names": ("Harbor Insurance Agency",),
            "addresses": (AddressInput(raw="400 Example Ave Suite 12, Miami, FL 33131"),),
            "phones": ("(305) 555-0180",),
        },
    )
    # 10. a stable identifier from an official source
    yield (
        "widgets",
        "widgets-award",
        {
            "names": ("LOS ALAMOS WIDGETS INC",),
            "addresses": (AddressInput(locality="Albuquerque", region="NM", postal_code="87102"),),
            "source_ids": ("usaspending.recipients:fixture-recipient-1",),
        },
    )
    yield (
        "widgets",
        "widgets-award-2",
        {
            "names": ("Los Alamos Widgets",),
            "addresses": (AddressInput(locality="Albuquerque", region="NM", postal_code="87102"),),
            "source_ids": ("usaspending.recipients:fixture-recipient-1",),
        },
    )
    # 11. a "doing business as" name, and HTML entities plus mojibake in another pair
    yield (
        "sol",
        "sol-dba",
        {
            "names": ("Fixture Holdings LLC dba Sol Taqueria",),
            "addresses": (AddressInput(raw="5 Sample Sq, San Antonio, TX 78205"),),
            "phones": ("(210) 555-0190",),
        },
    )
    yield (
        "sol",
        "sol-trade",
        {
            "names": ("Sol Taqueria",),
            "addresses": (AddressInput(raw="5 Sample Square, San Antonio, TX 78205"),),
            "phones": ("210.555.0190",),
        },
    )
    yield (
        "cafe",
        "cafe-entities",
        {
            "names": ("Caf&eacute; Ol&eacute; &amp; Grill",),
            "addresses": (AddressInput(raw="8 Demo Way, El Paso, TX 79901"),),
            "phones": ("(915) 555-0191",),
        },
    )
    yield (
        "cafe",
        "cafe-mojibake",
        {
            "names": ("CafÃ© OlÃ© & Grill",),
            "addresses": (AddressInput(raw="8 Demo Way, El Paso, TX 79901"),),
            "phones": ("915-555-0191",),
        },
    )
    # 12. coordinates a few metres apart
    yield "greenleaf", "greenleaf-a", {
        "names": ("Green Leaf Landscaping",),
        "addresses": (AddressInput(locality="Austin", region="TX", latitude=30.2672,
                                   longitude=-97.7431),),
        "phones": ("(512) 555-0102",),
    }  # fmt: skip
    yield "greenleaf", "greenleaf-b", {
        "names": ("Greenleaf Landscaping Co.",),
        "addresses": (AddressInput(locality="Austin", region="TX", latitude=30.2674,
                                   longitude=-97.7430),),
        "phones": ("512-555-0102",),
    }  # fmt: skip


def labelled() -> list[Labelled]:
    return [
        Labelled(entity, tag, EntityInput(business_id=record_id(index + 1), **fields))  # type: ignore[arg-type]
        for index, (entity, tag, fields) in enumerate(_rows())
    ]


# ---- generated at scale ------------------------------------------------------------------------

_SYLLABLES = [
    "ka",
    "ri",
    "lo",
    "me",
    "sa",
    "nu",
    "te",
    "vi",
    "da",
    "ro",
    "pe",
    "zu",
    "la",
    "mi",
    "no",
    "ba",
    "ce",
    "fo",
    "gu",
    "hi",
    "jo",
    "ku",
    "li",
    "mo",
    "ne",
    "pa",
    "qui",
    "ra",
    "si",
    "to",
    "va",
    "xe",
    "yo",
    "zo",
    "bri",
    "cla",
    "dre",
    "flo",
    "gra",
    "pla",
    "tro",
    "ste",
]
_TRADES = (
    "Bakery", "Roofing", "Dental Clinic", "Auto Repair", "Landscaping", "Cleaning Services",
    "Plumbing", "Tax Preparation", "Insurance Agency", "Painting", "Hair Salon", "Grocery",
)  # fmt: skip
_STREETS = ("Example", "Sample", "Demo", "Fixture", "Test")
_SUFFIXES = (
    ("Ave", "Avenue"),
    ("St", "Street"),
    ("Rd", "Road"),
    ("Dr", "Drive"),
    ("Blvd", "Boulevard"),
)
_STATES = ("TX", "FL", "AZ", "IL", "CA", "NY", "CO", "NM", "GA", "NC", "WA", "OH")
_LEGAL = ("", " LLC", " Inc.", ", Inc", " Co.", " L.L.C.")


def _area_codes() -> list[str]:
    tolls = {"800", "833", "844", "855", "866", "877", "888", "900"}
    return [
        f"{a}{b}{c}"
        for a in "23456789"
        for b in "0123456789"
        for c in "0123456789"
        if f"{b}{c}" != "11" and f"{a}{b}{c}" not in tolls
    ]


def generated(entities: int, seed: int = 6) -> list[Labelled]:
    """Entities with one to three records each, varied the way real sources vary them.

    Some names collide on purpose (same distinctive word and trade in the same city at another
    address), so precision is measured against realistic near-misses, not only easy negatives.
    """
    rng = random.Random(seed)  # noqa: S311 - reproducible test data, not security
    areas = _area_codes()
    cities = [(f"{rng.choice(_SYLLABLES).title()}{rng.choice(_SYLLABLES)}ton", state)
              for state in _STATES for _ in range(15)]  # fmt: skip
    out: list[Labelled] = []
    for entity in range(entities):
        word = "".join(rng.choice(_SYLLABLES) for _ in range(rng.choice((2, 3)))).title()
        trade = rng.choice(_TRADES)
        city, state = rng.choice(cities)
        postal = f"{rng.randint(10, 99)}{rng.randint(100, 999)}"
        number = str(rng.randint(1, 9999))
        street, (short, long) = rng.choice(_STREETS), rng.choice(_SUFFIXES)
        area, line = areas[entity % len(areas)], f"01{entity // len(areas) % 100:02d}"
        slug = f"{word.lower()}{trade.split()[0].lower()}{entity}.example"
        copies = rng.choices((1, 2, 3), weights=(5, 3, 2))[0]
        for copy in range(copies):
            has_phone = copy == 0 or rng.random() < 0.8
            has_site = rng.random() < 0.6
            has_street = copy == 0 or rng.random() < 0.7
            shown_trade = trade
            if copy and rng.random() < 0.1:  # a small typo in the trade word
                shown_trade = trade.replace("o", "", 1) if "o" in trade else trade + "s"
            phone = rng.choice(
                (f"({area}) 555-{line}", f"{area}.555.{line}", f"+1 {area} 555 {line}")
            )
            site = rng.choice((f"https://{slug}", f"http://www.{slug}/?utm_source=x", slug))
            address = AddressInput(
                street=f"{number} {street} {rng.choice((short, long))}" if has_street else None,
                locality=city if rng.random() < 0.8 else city.upper(),
                region=state,
                postal_code=postal if rng.random() < 0.9 else None,
            )
            out.append(
                Labelled(
                    entity=f"e{entity}",
                    tag=f"e{entity}.{copy}",
                    record=EntityInput(
                        business_id=record_id(len(out) + 1),
                        names=(f"{word} {shown_trade}{rng.choice(_LEGAL)}",),
                        addresses=(address,),
                        websites=(site,) if has_site else (),
                        phones=(phone,) if has_phone else (),
                        categories=(trade,),
                    ),
                )
            )
    return out
