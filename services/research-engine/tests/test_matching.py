"""Entity resolution on labelled synthetic corpora: hard cases, safety rules, clusters, determinism,
precision/recall and scale. See tests/resolution_corpus.py for the data."""

import random
import time
from collections import Counter
from dataclasses import dataclass
from itertools import combinations

import pytest
from resolution_corpus import Labelled, generated, labelled

from research_engine.domain import MatchOutcome
from research_engine.resolution import matching
from research_engine.resolution.matching import Edge, Features, Resolution, features, resolve


def _resolve(corpus: list[Labelled]) -> tuple[Resolution, dict[str, str]]:
    return resolve(features(item.record) for item in corpus), {
        item.record.business_id: item.tag for item in corpus
    }


def _pair(tags: dict[str, str], left: str, right: str) -> tuple[str, str]:
    ids = {tag: business_id for business_id, tag in tags.items()}
    return (min(ids[left], ids[right]), max(ids[left], ids[right]))


def _outcome(resolution: Resolution, tags: dict[str, str], left: str, right: str) -> MatchOutcome:
    found = resolution.comparisons.get(_pair(tags, left, right))
    return found.outcome if found is not None else MatchOutcome.DISTINCT


@pytest.fixture(scope="module")
def corpus() -> tuple[list[Labelled], Resolution, dict[str, str]]:
    items = labelled()
    resolution, tags = _resolve(items)
    return items, resolution, tags


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        # the same business written differently merges on its own
        ("rivera-llc", "rivera-inc", MatchOutcome.AUTO_MERGE),
        ("rivera-llc", "rivera-caps", MatchOutcome.AUTO_MERGE),
        ("garcia", "garcia-typo", MatchOutcome.AUTO_MERGE),
        ("widgets-award", "widgets-award-2", MatchOutcome.AUTO_MERGE),
        ("sol-dba", "sol-trade", MatchOutcome.AUTO_MERGE),
        ("cafe-entities", "cafe-mojibake", MatchOutcome.AUTO_MERGE),
        ("greenleaf-a", "greenleaf-b", MatchOutcome.AUTO_MERGE),
        # ambiguous cases go to a person
        ("blue-heron-old", "blue-heron-new", MatchOutcome.MANUAL_REVIEW),  # moved
        ("lotus-thin", "lotus-full", MatchOutcome.MANUAL_REVIEW),  # incomplete
        ("sunrise-north", "sunrise-north-again", MatchOutcome.MANUAL_REVIEW),
        # different establishments are never merged automatically
        ("sunrise-downtown", "sunrise-north", MatchOutcome.DISTINCT),  # branches
        ("quicklube-austin", "quicklube-dallas", MatchOutcome.DISTINCT),  # franchise
        ("quicklube-austin", "quicklube-miami", MatchOutcome.DISTINCT),
        ("cleaning-houston", "cleaning-tampa", MatchOutcome.DISTINCT),  # generic, other state
        ("cleaning-houston", "cleaning-houston-2", MatchOutcome.DISTINCT),  # generic, other street
        ("garcia", "garza", MatchOutcome.DISTINCT),  # similar name, another business
        ("joes-midtown", "joes-uptown", MatchOutcome.DISTINCT),
        ("northside", "harbor", MatchOutcome.DISTINCT),  # shared line, other suite
    ],
)
def test_hard_cases_are_decided_conservatively(
    corpus: tuple[list[Labelled], Resolution, dict[str, str]],
    left: str,
    right: str,
    expected: MatchOutcome,
) -> None:
    _, resolution, tags = corpus
    assert _outcome(resolution, tags, left, right) is expected


def _reasons(resolution: Resolution, tags: dict[str, str], left: str, right: str) -> set[str]:
    return {reason.code for reason in resolution.comparisons[_pair(tags, left, right)].reasons}


def test_every_decision_explains_itself(
    corpus: tuple[list[Labelled], Resolution, dict[str, str]],
) -> None:
    _, resolution, tags = corpus
    assert "same_brand_different_location" in _reasons(
        resolution, tags, "sunrise-downtown", "sunrise-north"
    )
    assert "address_changed" in _reasons(resolution, tags, "blue-heron-old", "blue-heron-new")
    assert {"shared_phone_only", "different_unit"} <= _reasons(
        resolution, tags, "northside", "harbor"
    )
    assert "generic_name" in _reasons(resolution, tags, "cleaning-houston", "cleaning-houston-2")
    comparison = resolution.comparisons[_pair(tags, "rivera-llc", "rivera-inc")]
    stored = comparison.features_json()
    assert stored["rules_version"] == matching.RULES_VERSION
    fields = stored["fields"]
    assert isinstance(fields, list)
    compared = {field["field"] for field in fields if isinstance(field, dict)}
    assert compared >= {"name", "phone", "address"}
    assert all(reason.weight != 0 or reason.direction == "rule" for reason in comparison.reasons)
    assert any(key.startswith("domain:") for key in comparison.keys)


def test_generic_names_in_different_states_are_not_even_compared(
    corpus: tuple[list[Labelled], Resolution, dict[str, str]],
) -> None:
    _, resolution, tags = corpus
    assert _pair(tags, "cleaning-houston", "cleaning-tampa") not in resolution.comparisons


def test_numbered_establishments_are_different_businesses() -> None:
    left, right = (
        features(matching.EntityInput(business_id=business_id, names=(name,),
                                      addresses=(matching.AddressInput(locality="Austin",
                                                                       region="TX"),),
                                      websites=("https://storefixture.example",)))
        for business_id, name in (("a", "Fixture Store 12"), ("b", "Fixture Store 13"))
    )  # fmt: skip
    comparison = matching.compare(left, right)
    assert "different_name" in {reason.code for reason in comparison.reasons}
    assert comparison.hard_conflict


def test_clusters_follow_automatic_merges_only(
    corpus: tuple[list[Labelled], Resolution, dict[str, str]],
) -> None:
    items, resolution, tags = corpus
    groups: dict[str, set[str]] = {}
    for business_id, root in resolution.roots.items():
        groups.setdefault(root, set()).add(tags[business_id])
    clusters = sorted(sorted(group) for group in groups.values() if len(group) > 1)
    assert clusters == [
        ["cafe-entities", "cafe-mojibake"],
        ["garcia", "garcia-typo"],
        ["greenleaf-a", "greenleaf-b"],
        ["rivera-caps", "rivera-inc", "rivera-llc"],
        ["sol-dba", "sol-trade"],
        ["widgets-award", "widgets-award-2"],
    ]
    for root, group in groups.items():  # the root is the oldest record of its cluster
        assert root == min(business_id for business_id, tag in tags.items() if tag in group)
    assert len(resolution.roots) == len(items)


@dataclass(frozen=True, slots=True)
class Metrics:
    true_pairs: int
    auto: int
    auto_correct: int
    review: int
    review_correct: int

    @property
    def auto_precision(self) -> float:
        return self.auto_correct / self.auto if self.auto else 1.0

    @property
    def auto_recall(self) -> float:
        return self.auto_correct / self.true_pairs

    @property
    def recall_with_review(self) -> float:
        return (self.auto_correct + self.review_correct) / self.true_pairs


def measure(items: list[Labelled], resolution: Resolution) -> Metrics:
    """Precision is judged on the clusters (a merge made through transitivity counts too); a true
    pair counts for recall once, whether it was merged or sent to a person."""
    entity = {item.record.business_id: item.entity for item in items}
    sizes = Counter(entity.values())
    true_pairs = sum(size * (size - 1) // 2 for size in sizes.values())
    clustered = set(_cluster_pairs(resolution))
    reviewed = {
        pair
        for pair, comparison in resolution.comparisons.items()
        if comparison.outcome is MatchOutcome.MANUAL_REVIEW
    } - clustered
    return Metrics(
        true_pairs=true_pairs,
        auto=len(clustered),
        auto_correct=sum(entity[a] == entity[b] for a, b in clustered),
        review=len(reviewed),
        review_correct=sum(entity[a] == entity[b] for a, b in reviewed),
    )


def _cluster_pairs(resolution: Resolution) -> list[tuple[str, str]]:
    groups: dict[str, list[str]] = {}
    for business_id, root in resolution.roots.items():
        groups.setdefault(root, []).append(business_id)
    return [pair for group in groups.values() for pair in combinations(sorted(group), 2)]


def test_precision_and_recall_on_the_labelled_corpus(
    corpus: tuple[list[Labelled], Resolution, dict[str, str]],
) -> None:
    items, resolution, _ = corpus
    metrics = measure(items, resolution)
    print(
        f"labelled corpus: {metrics} auto_precision={metrics.auto_precision:.3f} "
        f"auto_recall={metrics.auto_recall:.3f} with_review={metrics.recall_with_review:.3f}"
    )
    assert metrics.auto_precision == 1.0
    assert metrics.recall_with_review == 1.0
    assert metrics.auto_recall >= 0.7


def test_the_same_input_in_any_order_resolves_identically() -> None:
    items = generated(400, seed=11)
    first = resolve(features(item.record) for item in items)
    shuffled = items[:]
    random.Random(3).shuffle(shuffled)  # noqa: S311
    second = resolve(features(item.record) for item in shuffled)
    assert first.roots == second.roots
    assert {
        pair: (comparison.score, comparison.outcome, comparison.reasons)
        for pair, comparison in first.comparisons.items()
    } == {
        pair: (comparison.score, comparison.outcome, comparison.reasons)
        for pair, comparison in second.comparisons.items()
    }


def test_people_win_and_kept_apart_records_are_never_chained() -> None:
    ids = ["a", "b", "c", "d"]
    merges = [Edge("a", "b", 0.99, False), Edge("b", "c", 0.98, False)]
    roots, refused = matching.clusters(ids, merges, separations=[("a", "c")])
    assert roots["a"] == roots["b"]
    assert roots["c"] == "c"
    assert refused == {("b", "c")}
    manual = [*merges, Edge("c", "d", 0.1, True), Edge("a", "d", 0.1, True)]
    roots, refused = matching.clusters(ids, manual, separations=[("a", "c")])
    # people merged a-d and d-c before any automatic edge ran, so b-c no longer joins anything new
    assert set(roots.values()) == {"a"}
    assert not refused


def test_a_manual_separation_stops_an_automatic_merge_and_says_why() -> None:
    items = labelled()
    tags = {item.record.business_id: item.tag for item in items}
    pair = _pair(tags, "rivera-llc", "rivera-inc")
    resolution = resolve((features(item.record) for item in items), manual={pair: False})
    assert resolution.roots[pair[0]] != resolution.roots[pair[1]]
    # rivera-caps would join both sides; the second of its merges is refused and says why
    chained = [
        resolution.comparisons[_pair(tags, "rivera-caps", other)]
        for other in ("rivera-llc", "rivera-inc")
    ]
    reasons = {reason.code for comparison in chained for reason in comparison.reasons}
    assert "cluster_conflict" in reasons


def test_oversized_blocks_are_skipped() -> None:
    rows = [
        features(matching.EntityInput(business_id=f"id{index:04d}", names=(f"Store {index}",),
                                      phones=("1-800-555-0199",)))
        for index in range(matching.MAX_BLOCK + 1)
    ]  # fmt: skip
    blocking = matching.candidate_pairs(rows)
    assert blocking.skipped_blocks >= 1
    assert not blocking.pairs


def test_resolution_scales_to_tens_of_thousands_of_records() -> None:
    items = generated(18_000, seed=6)
    started = time.perf_counter()
    rows: list[Features] = [features(item.record) for item in items]
    featured = time.perf_counter() - started
    resolution = resolve(rows)
    elapsed = time.perf_counter() - started
    metrics = measure(items, resolution)
    n = len(items)
    print(
        f"{n} records: features {featured:.1f}s, total {elapsed:.1f}s, "
        f"{len(resolution.comparisons)} pairs compared ({n * (n - 1) // 2} possible), "
        f"blocks {resolution.blocks} (+{resolution.skipped_blocks} skipped, largest "
        f"{resolution.largest_block}); {metrics} auto_precision={metrics.auto_precision:.4f} "
        f"auto_recall={metrics.auto_recall:.4f} with_review={metrics.recall_with_review:.4f}"
    )
    assert n >= 25_000
    assert len(resolution.comparisons) < n * 20  # far from the n² of comparing everything
    assert metrics.auto_precision >= 0.995
    assert metrics.recall_with_review >= 0.85
    assert elapsed < 180
