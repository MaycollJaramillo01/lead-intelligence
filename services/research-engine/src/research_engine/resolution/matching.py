"""Entity resolution over normalised records (phase 06): blocking, comparison, decision, clusters.

Pure and deterministic: the same records, the same manual decisions and the same `RULES_VERSION`
always give the same pairs, scores, outcomes and clusters, whatever order the records arrive in.

    records ─ features ─ blocking keys ─ candidate pairs ─ compare ─ rules ─ outcome
                                                                        └─ union-find ─ clusters

1. Exact blocking: registrable domain, E.164 phone, a stable identifier of the source.
2. Candidate blocking: distinctive name tokens within a city or ZIP, the full name, the street
   address, the email domain and nearby coordinates. A block larger than `MAX_BLOCK` says nothing
   about identity (a franchise switchboard, "Main St") and is skipped, so comparisons stay far from
   O(n²).
3. Features: name similarity, street, unit, city, state, phone, domain, category, email domain,
   distance. Each agreement or conflict adds a weight to a log-odds total; the score is its
   logistic.
4. Decision: `auto_merge`, `manual_review` or `distinct`, then the safety rules of the prompt, which
   can only make an outcome more cautious. Ambiguity goes to a person.
"""

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import Final

from research_engine.domain import (
    FieldAgreement,
    JsonObject,
    MatchOutcome,
    MatchRisk,
    ReasonDirection,
)
from research_engine.resolution import normalize
from research_engine.resolution.normalize import (
    AddressResult,
    NameResult,
    PhoneKind,
    Presence,
)

RULES_VERSION: Final = "1.0.0"
#: Logistic score thresholds. Conservative on purpose: see docs/resolution.md for the measurements.
AUTO_MERGE_SCORE: Final = 0.97
REVIEW_SCORE: Final = 0.25
#: A distinct pair below this score, and without a safety rule, is not worth storing.
KEEP_DISTINCT_SCORE: Final = 0.05
MAX_BLOCK: Final = 250
PRIOR: Final = -5.0
#: Log-odds added by each agreement (positive) or conflict (negative).
WEIGHTS: Final[Mapping[str, float]] = {
    "same_source_id": 7.0,
    "same_domain": 3.5,
    "same_phone": 3.0,
    "same_shared_phone": 1.0,
    "same_name": 3.5,
    "similar_name": 2.5,
    "related_name": 1.0,
    "different_name": -4.0,
    "same_street": 3.0,
    "same_postal_code": 0.5,
    "same_locality": 1.0,
    "same_category": 0.5,
    "same_email_domain": 1.5,
    "nearby": 2.0,
    "different_street": -2.5,
    "different_unit": -3.0,
    "different_locality": -2.0,
    "different_state": -6.0,
    "different_category": -0.5,
    "different_domain": -1.5,
    "different_phone": -1.0,
    "far_apart": -2.0,
}
#: Source kinds whose record ids identify an entity (a file row number does not).
STABLE_ID_KINDS: Final = frozenset(
    {"official_api", "public_registry", "public_dataset", "directory", "platform_api"}
)
_SIMILAR_NAME: Final = 0.9
_RELATED_NAME: Final = 0.8
_DIFFERENT_NAME: Final = 0.5
_SAME_STREET: Final = 0.85
_NEARBY_KM: Final = 0.15
_FAR_KM: Final = 5.0
_NAME_BLOCK_TOKENS: Final = 4
#: Rules that stop a merge. When one fires, a distinct pair is stored so the refusal is explained.
SAFETY_RULES: Final = frozenset(
    {"shared_phone_only", "same_brand_different_location", "address_changed", "generic_name",
     "different_state_same_source", "cluster_conflict"}
)  # fmt: skip
_CONFLICTS: Final = frozenset(
    {"different_state", "different_unit", "different_street", "different_locality",
     "different_name", "far_apart"}
)  # fmt: skip
_ADDRESS_CONFLICTS: Final = frozenset(
    {"different_street", "different_unit", "different_locality", "far_apart"}
)


# ---- features ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AddressInput:
    raw: str | None = None
    street: str | None = None
    unit: str | None = None
    locality: str | None = None
    region: str | None = None
    postal_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None


@dataclass(frozen=True, slots=True)
class EntityInput:
    """One record as acquired: raw values only. Normalisation happens in `features`."""

    business_id: str
    names: tuple[str, ...] = ()
    addresses: tuple[AddressInput, ...] = ()
    websites: tuple[str, ...] = ()
    phones: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    #: `connector_key:external_id`, only for sources in STABLE_ID_KINDS.
    source_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Features:
    business_id: str
    names: tuple[NameResult, ...]
    addresses: tuple[AddressResult, ...]
    #: Geographic (and fictional or international) numbers: they point at one place.
    phones: frozenset[str]
    #: Toll-free and premium numbers: shared by every branch of a brand, never an identity alone.
    shared_phones: frozenset[str]
    phone_labels: Mapping[str, str]
    domains: frozenset[str]
    email_domains: frozenset[str]
    categories: frozenset[str]
    source_ids: frozenset[str]
    regions: frozenset[str]
    #: (state, city key); the state is "" when unknown.
    places: frozenset[tuple[str, str]]
    postal_codes: frozenset[str]
    coordinates: tuple[tuple[float, float], ...]


def features(entity: EntityInput) -> Features:
    names: dict[str, NameResult] = {}
    for value in entity.names:
        name = normalize.business_name(value)
        # "Legal LLC dba Trade": both names identify the business
        for found in (name, normalize.business_name(name.legal)) if name.legal else (name,):
            if found.status is Presence.PRESENT and found.key and found.key not in names:
                names[found.key] = found
    addresses = tuple(
        result
        for result in (
            normalize.us_address(
                item.raw,
                street=item.street,
                unit=item.unit,
                locality_value=item.locality,
                region=item.region,
                postal_code=item.postal_code,
                latitude=item.latitude,
                longitude=item.longitude,
            )
            for item in entity.addresses
        )
        if result.status is Presence.PRESENT
    )
    phones: set[str] = set()
    shared: set[str] = set()
    labels: dict[str, str] = {}
    for value in entity.phones:
        number = normalize.phone(value)
        if number.e164 is None:
            continue
        labels[number.e164] = number.national or number.e164
        shared_kinds = (PhoneKind.TOLL_FREE, PhoneKind.PREMIUM)
        (shared if number.kind in shared_kinds else phones).add(number.e164)
    domains = {
        site.registrable_domain
        for site in map(normalize.website, entity.websites)
        if site.registrable_domain and not site.is_platform
    }
    email_domains = {
        address.domain
        for address in map(normalize.email, entity.emails)
        if address.domain and not address.is_free_provider
    }
    categories = {
        label.taxonomy_key or normalize.fold(label.label or "")
        for label in map(normalize.category, entity.categories)
        if label.label
    }
    return Features(
        business_id=entity.business_id,
        names=tuple(names[key] for key in sorted(names)),
        addresses=addresses,
        phones=frozenset(phones),
        shared_phones=frozenset(shared),
        phone_labels=labels,
        domains=frozenset(domains),
        email_domains=frozenset(email_domains),
        categories=frozenset(categories),
        source_ids=frozenset(entity.source_ids),
        regions=frozenset(item.region for item in addresses if item.region),
        places=frozenset(
            (item.region or "", item.locality_key) for item in addresses if item.locality_key
        ),
        postal_codes=frozenset(item.postal_code for item in addresses if item.postal_code),
        coordinates=tuple(
            (item.latitude, item.longitude)
            for item in addresses
            if item.latitude is not None and item.longitude is not None
        ),
    )


# ---- blocking ----------------------------------------------------------------------------------


def blocking_keys(item: Features) -> set[str]:
    keys = {f"domain:{domain}" for domain in item.domains}
    keys |= {f"phone:{number}" for number in item.phones | item.shared_phones}
    keys |= {f"source:{source}" for source in item.source_ids}
    keys |= {f"email:{domain}" for domain in item.email_domains}
    for name in item.names:
        if name.distinctive:
            keys.add(f"fullname:{name.key}")
            for token in sorted(set(name.distinctive))[:_NAME_BLOCK_TOKENS]:
                keys |= {f"name:{region}:{place}:{token}" for region, place in item.places}
                keys |= {f"zipname:{postal}:{token}" for postal in item.postal_codes}
        else:  # a generic name is only worth comparing with the same name in the same place
            keys |= {f"generic:{region}:{place}:{name.key}" for region, place in item.places}
            keys |= {f"genericzip:{postal}:{name.key}" for postal in item.postal_codes}
    for address in item.addresses:
        area = address.postal_code or (
            f"{address.region}:{address.locality_key}" if address.locality_key else None
        )
        if address.street_number and address.street_key and area:
            keys.add(f"street:{area}:{address.street_number}:{address.street_key}")
    for latitude, longitude in item.coordinates:
        cell = (round(latitude * 100), round(longitude * 100))  # ~1 km cells and their neighbours
        keys |= {f"geo:{cell[0] + dy}:{cell[1] + dx}" for dy in (-1, 0, 1) for dx in (-1, 0, 1)}
    return keys


@dataclass(frozen=True, slots=True)
class Blocking:
    pairs: Mapping[tuple[str, str], tuple[str, ...]]
    blocks: int
    skipped_blocks: int
    largest_block: int


def candidate_pairs(items: Iterable[Features], max_block: int = MAX_BLOCK) -> Blocking:
    index: dict[str, list[str]] = defaultdict(list)
    for item in sorted(items, key=lambda value: value.business_id):
        for key in blocking_keys(item):
            index[key].append(item.business_id)
    pairs: dict[tuple[str, str], list[str]] = defaultdict(list)
    blocks = skipped = largest = 0
    for key in sorted(index):
        members = index[key]
        if len(members) < 2:
            continue
        if len(members) > max_block:
            skipped += 1
            continue
        blocks += 1
        largest = max(largest, len(members))
        for position, left in enumerate(members):
            for right in members[position + 1 :]:
                pairs[(left, right)].append(key)
    return Blocking({pair: tuple(keys) for pair, keys in pairs.items()}, blocks, skipped, largest)


# ---- comparison --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Reason:
    code: str
    direction: ReasonDirection
    weight: float
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class FieldResult:
    field: str
    left: tuple[str, ...]
    right: tuple[str, ...]
    agreement: FieldAgreement
    similarity: float | None = None


@dataclass(frozen=True, slots=True)
class Comparison:
    left_id: str
    right_id: str
    score: float
    outcome: MatchOutcome
    risk: MatchRisk
    #: A conflict that should keep the two records' clusters apart (different state, unit...).
    hard_conflict: bool
    fields: tuple[FieldResult, ...]
    reasons: tuple[Reason, ...]
    keys: tuple[str, ...]
    total: float

    @property
    def pair(self) -> tuple[str, str]:
        return (self.left_id, self.right_id)

    @property
    def worth_storing(self) -> bool:
        rules = {reason.code for reason in self.reasons if reason.direction is ReasonDirection.RULE}
        return (
            self.outcome is not MatchOutcome.DISTINCT
            or self.score >= KEEP_DISTINCT_SCORE
            or bool(rules & SAFETY_RULES)
        )

    def features_json(self) -> JsonObject:
        return {
            "rules_version": RULES_VERSION,
            "prior": PRIOR,
            "total": round(self.total, 4),
            "thresholds": {"auto_merge": AUTO_MERGE_SCORE, "review": REVIEW_SCORE},
            "blocking_keys": list(self.keys),
            "fields": [
                {
                    "field": item.field,
                    "left": list(item.left),
                    "right": list(item.right),
                    "agreement": item.agreement.value,
                    "similarity": item.similarity,
                }
                for item in self.fields
            ],
        }

    def reasons_json(self) -> JsonObject:
        return {
            "reasons": [
                {
                    "code": reason.code,
                    "direction": reason.direction.value,
                    "weight": reason.weight,
                    "detail": reason.detail,
                }
                for reason in self.reasons
            ]
        }


def _ratio(left: str, right: str) -> float:
    return 1.0 if left == right else SequenceMatcher(None, left, right).ratio()


def name_similarity(left: NameResult, right: NameResult) -> float:
    """Similarity of two name keys; distinctive words weigh most, so "Garcia" is not "Garza"."""
    if left.key == right.key:
        return 1.0
    if left.key and right.key and left.key.replace(" ", "") == right.key.replace(" ", ""):
        return 0.99  # "Green Leaf" and "Greenleaf": only the spacing differs
    numbers = {token for token in left.tokens if token.isdigit()}
    other_numbers = {token for token in right.tokens if token.isdigit()}
    if numbers and other_numbers and numbers != other_numbers:
        return 0.45  # "Store 12" and "Store 13": numbered establishments are different ones
    full = _ratio(" ".join(sorted(set(left.tokens))), " ".join(sorted(set(right.tokens))))
    if left.distinctive and right.distinctive:
        distinctive = _ratio(
            " ".join(sorted(set(left.distinctive))), " ".join(sorted(set(right.distinctive)))
        )
        full = min(full, distinctive)
    return round(full, 4)


def _street_state(left: Iterable[AddressResult], right: Iterable[AddressResult]) -> str | None:
    state: str | None = None
    for a in left:
        for b in right:
            if not (a.street_number and a.street_key and b.street_number and b.street_key):
                continue
            areas_known = (a.postal_code or a.locality_key) and (b.postal_code or b.locality_key)
            same_area = (a.postal_code and a.postal_code == b.postal_code) or (
                a.locality_key and a.locality_key == b.locality_key
            )
            if areas_known and not same_area:
                continue  # different towns are judged by the city, not by the street
            if a.street_number == b.street_number and _ratio(a.street_key, b.street_key) >= (
                _SAME_STREET
            ):
                if a.unit_key and b.unit_key and a.unit_key != b.unit_key:
                    state = "unit_conflict"
                else:
                    return "match"
            elif state is None:
                state = "conflict"
    return state


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def _address_label(address: AddressResult) -> str:
    place = ", ".join(filter(None, (address.locality, address.region, address.postal_code)))
    street = " ".join(filter(None, (address.street, address.unit)))
    return " · ".join(filter(None, (street, place)))


def _agreement(left: Iterable[str], right: Iterable[str]) -> FieldAgreement:
    a, b = set(left), set(right)
    if not a or not b:
        return FieldAgreement.MISSING
    if a == b:
        return FieldAgreement.MATCH
    return FieldAgreement.PARTIAL if a & b else FieldAgreement.CONFLICT


def compare(a: Features, b: Features, keys: tuple[str, ...] = ()) -> Comparison:
    """Scores one pair and decides. `a` and `b` are ordered by business id by the caller."""
    reasons: list[Reason] = []
    total = PRIOR

    def add(code: str, detail: str | None = None, factor: float = 1.0) -> None:
        nonlocal total
        weight = round(WEIGHTS[code] * factor, 3)
        total += weight
        direction = ReasonDirection.FOR if weight > 0 else ReasonDirection.AGAINST
        reasons.append(Reason(code, direction, weight, detail))

    def rule(code: str, detail: str | None = None) -> None:
        reasons.append(Reason(code, ReasonDirection.RULE, 0.0, detail))

    strong: set[str] = set()
    fields: list[FieldResult] = []

    if a.source_ids & b.source_ids:
        add("same_source_id", ", ".join(sorted(a.source_ids & b.source_ids)))
        strong.add("source")

    similarity: float | None = None
    generic = False
    shown = ("", "")
    for left in a.names:
        for right in b.names:
            value = name_similarity(left, right)
            if similarity is None or value > similarity:
                similarity, shown = value, (left.display or "", right.display or "")
                generic = left.is_generic or right.is_generic
    if similarity is not None:
        factor = 0.5 if generic else 1.0
        detail = f"{shown[0]} ~ {shown[1]} ({similarity:.2f})"
        if similarity == 1.0:
            add("same_name", detail, factor)
        elif similarity >= _SIMILAR_NAME:
            add("similar_name", detail, factor)
        elif similarity >= _RELATED_NAME:
            add("related_name", detail, factor)
        elif similarity < _DIFFERENT_NAME:
            add("different_name", detail)
        if similarity >= _SIMILAR_NAME and not generic:
            strong.add("name")
        if generic:
            rule("generic_name", "a name made only of generic words cannot identify a business")
    fields.append(
        FieldResult(
            "name",
            tuple(name.display or "" for name in a.names),
            tuple(name.display or "" for name in b.names),
            FieldAgreement.MISSING
            if similarity is None
            else FieldAgreement.MATCH
            if similarity >= _SIMILAR_NAME
            else FieldAgreement.PARTIAL
            if similarity >= _DIFFERENT_NAME
            else FieldAgreement.CONFLICT,
            similarity,
        )
    )

    if a.domains and b.domains:
        if a.domains & b.domains:
            add("same_domain", ", ".join(sorted(a.domains & b.domains)))
            strong.add("domain")
        else:
            add("different_domain")
    fields.append(
        FieldResult("website", tuple(sorted(a.domains)), tuple(sorted(b.domains)),
                    _agreement(a.domains, b.domains))
    )  # fmt: skip

    if a.phones & b.phones:
        add("same_phone", ", ".join(a.phone_labels[p] for p in sorted(a.phones & b.phones)))
        strong.add("phone")
    elif a.shared_phones & b.shared_phones:
        add("same_shared_phone", ", ".join(sorted(a.shared_phones & b.shared_phones)))
    elif a.phones and b.phones:
        add("different_phone")
    left_phones, right_phones = a.phones | a.shared_phones, b.phones | b.shared_phones
    fields.append(
        FieldResult(
            "phone",
            tuple(sorted(a.phone_labels[p] for p in left_phones)),
            tuple(sorted(b.phone_labels[p] for p in right_phones)),
            _agreement(left_phones, right_phones),
        )
    )

    street = _street_state(a.addresses, b.addresses)
    if street == "match":
        add("same_street")
        strong.add("street")
    elif street == "unit_conflict":
        add("different_unit")
    elif street == "conflict":
        add("different_street")
    if a.postal_codes & b.postal_codes:
        add("same_postal_code")
    if a.regions and b.regions and not a.regions & b.regions:
        add("different_state", f"{'/'.join(sorted(a.regions))} vs {'/'.join(sorted(b.regions))}")
    elif a.places and b.places:
        same_place = any(
            place_a == place_b and (region_a == region_b or not region_a or not region_b)
            for region_a, place_a in a.places
            for region_b, place_b in b.places
        )
        if same_place:
            add("same_locality")
        else:
            add("different_locality")
    distances = [_km(x, y) for x in a.coordinates for y in b.coordinates]
    if distances:
        nearest = min(distances)
        if nearest <= _NEARBY_KM:
            add("nearby", f"{nearest:.2f} km")
        elif nearest > _FAR_KM:
            add("far_apart", f"{nearest:.1f} km")
    left_places = tuple(_address_label(item) for item in a.addresses)
    right_places = tuple(_address_label(item) for item in b.addresses)
    fields.append(
        FieldResult(
            "address",
            left_places,
            right_places,
            FieldAgreement.MISSING
            if not left_places or not right_places
            else FieldAgreement.MATCH
            if street == "match"
            else FieldAgreement.CONFLICT
            if street in ("conflict", "unit_conflict")
            or any(r.code in ("different_locality", "different_state") for r in reasons)
            else FieldAgreement.PARTIAL,
        )
    )

    if a.categories and b.categories:
        if a.categories & b.categories:
            add("same_category")
        else:
            add("different_category")
    fields.append(
        FieldResult("category", tuple(sorted(a.categories)), tuple(sorted(b.categories)),
                    _agreement(a.categories, b.categories))
    )  # fmt: skip
    if a.email_domains & b.email_domains:
        add("same_email_domain", ", ".join(sorted(a.email_domains & b.email_domains)))
    fields.append(
        FieldResult("email_domain", tuple(sorted(a.email_domains)), tuple(sorted(b.email_domains)),
                    _agreement(a.email_domains, b.email_domains))
    )  # fmt: skip
    if a.source_ids or b.source_ids:
        fields.append(
            FieldResult("source_id", tuple(sorted(a.source_ids)), tuple(sorted(b.source_ids)),
                        _agreement(a.source_ids, b.source_ids))
        )  # fmt: skip

    score = 1 / (1 + math.exp(-total))
    codes = {reason.code for reason in reasons}
    conflicts = codes & _CONFLICTS
    outcome = (
        MatchOutcome.AUTO_MERGE
        if score >= AUTO_MERGE_SCORE
        else MatchOutcome.MANUAL_REVIEW
        if score >= REVIEW_SCORE
        else MatchOutcome.DISTINCT
    )
    hard = bool(conflicts & {"different_state", "different_unit", "different_name"})

    # ---- safety rules: each one can only make the outcome more cautious ----
    if "different_state" in conflicts:
        if "source" in strong:
            rule(
                "different_state_same_source", "one registered entity, two states: a person decides"
            )
            outcome = MatchOutcome.MANUAL_REVIEW
        else:
            outcome = MatchOutcome.DISTINCT
    if conflicts & _ADDRESS_CONFLICTS and strong & {"name", "domain"} and "source" not in strong:
        if "phone" in strong:
            rule("address_changed", "same name or site and the same local phone at another address")
            outcome = MatchOutcome.MANUAL_REVIEW
        else:
            rule("same_brand_different_location", "a branch or franchise is its own establishment")
            outcome = MatchOutcome.DISTINCT
            hard = True
    phone_agrees = "phone" in strong or "same_shared_phone" in codes
    if phone_agrees and not strong & {"name", "domain", "street", "source"}:
        rule("shared_phone_only", "a phone shared by several establishments is not enough")
        if outcome is MatchOutcome.AUTO_MERGE:
            outcome = MatchOutcome.MANUAL_REVIEW
    if outcome is MatchOutcome.AUTO_MERGE and (
        generic or conflicts or len(strong) < 2 or not strong & {"name", "source"}
    ):
        if not generic:
            rule("insufficient_identity", "an automatic merge needs two agreeing identities")
        outcome = MatchOutcome.MANUAL_REVIEW

    rules = codes | {reason.code for reason in reasons if reason.direction is ReasonDirection.RULE}
    if outcome is MatchOutcome.AUTO_MERGE:
        risk = MatchRisk.LOW if len(strong) >= 3 else MatchRisk.MEDIUM
    elif (
        rules & SAFETY_RULES or conflicts or (outcome is MatchOutcome.MANUAL_REVIEW and score < 0.6)
    ):
        risk = MatchRisk.HIGH
    elif outcome is MatchOutcome.MANUAL_REVIEW:
        risk = MatchRisk.MEDIUM
    else:
        risk = MatchRisk.LOW
    return Comparison(
        left_id=a.business_id,
        right_id=b.business_id,
        score=round(score, 6),
        outcome=outcome,
        risk=risk,
        hard_conflict=hard,
        fields=tuple(fields),
        reasons=tuple(reasons),
        keys=keys,
        total=total,
    )


# ---- clusters ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Edge:
    left: str
    right: str
    score: float
    manual: bool


def clusters(
    business_ids: Iterable[str],
    merges: Iterable[Edge],
    separations: Iterable[tuple[str, str]],
) -> tuple[dict[str, str], frozenset[tuple[str, str]]]:
    """Root of every record, and the automatic merges refused because they would join records a
    person (or a hard conflict) kept apart.

    Manual merges are applied first and always: people win. Automatic ones follow by score. The
    root of a cluster is its smallest business id (the oldest record, ids being UUIDv7), so it does
    not depend on the order in which merges were found.
    """
    parent: dict[str, str] = {}
    members: dict[str, set[str]] = {}
    for business_id in business_ids:
        parent[business_id] = business_id
        members[business_id] = {business_id}
    apart: dict[str, set[str]] = defaultdict(set)
    for left, right in separations:
        apart[left].add(right)
        apart[right].add(left)

    def find(node: str) -> str:
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:
            parent[node], node = root, parent[node]
        return root

    refused: set[tuple[str, str]] = set()
    for edge in sorted(merges, key=lambda e: (not e.manual, -e.score, e.left, e.right)):
        if edge.left not in parent or edge.right not in parent:
            continue
        left, right = find(edge.left), find(edge.right)
        if left == right:
            continue
        small, large = sorted((left, right), key=lambda root: (len(members[root]), root))
        if not edge.manual and any(apart[member] & members[large] for member in members[small]):
            refused.add((edge.left, edge.right))
            continue
        root, other = (left, right) if left < right else (right, left)
        parent[other] = root
        members[root] |= members.pop(other)
    return {business_id: find(business_id) for business_id in parent}, frozenset(refused)


@dataclass(frozen=True, slots=True)
class Resolution:
    comparisons: Mapping[tuple[str, str], Comparison]
    roots: Mapping[str, str]
    blocks: int
    skipped_blocks: int
    largest_block: int


def resolve(
    items: Iterable[Features],
    manual: Mapping[tuple[str, str], bool] | None = None,
) -> Resolution:
    """The whole pipeline over a set of records. `manual` maps a pair to what a person decided:
    True = merged, False = kept separate. Pending or deferred pairs are simply not in it."""
    manual = manual or {}
    by_id = {item.business_id: item for item in items}
    blocking = candidate_pairs(by_id.values())
    comparisons = {
        pair: compare(by_id[pair[0]], by_id[pair[1]], keys) for pair, keys in blocking.pairs.items()
    }
    merges = [
        Edge(pair[0], pair[1], comparisons[pair].score if pair in comparisons else 1.0, True)
        for pair, merged in manual.items()
        if merged
    ]
    merges += [
        Edge(pair[0], pair[1], comparison.score, False)
        for pair, comparison in comparisons.items()
        if comparison.outcome is MatchOutcome.AUTO_MERGE and pair not in manual
    ]
    separations = [pair for pair, merged in manual.items() if not merged]
    separations += [pair for pair, comparison in comparisons.items() if comparison.hard_conflict]
    roots, refused = clusters(by_id, merges, separations)
    for pair in refused:
        comparison = comparisons[pair]
        comparisons[pair] = replace(
            comparison,
            outcome=MatchOutcome.MANUAL_REVIEW,
            risk=MatchRisk.HIGH,
            reasons=(
                *comparison.reasons,
                Reason("cluster_conflict", ReasonDirection.RULE, 0.0,
                       "merging would join records that were kept apart"),
            ),
        )  # fmt: skip
    return Resolution(
        comparisons=comparisons,
        roots=roots,
        blocks=blocking.blocks,
        skipped_blocks=blocking.skipped_blocks,
        largest_block=blocking.largest_block,
    )
