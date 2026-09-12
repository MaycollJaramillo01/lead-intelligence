# Entity resolution

How Lead Intelligence turns heterogeneous records into canonical businesses without merging
businesses that are different, and how every merge can be explained and undone. Delivered in
phase 06.

- Normalisers: [`resolution/normalize.py`](../services/research-engine/src/research_engine/resolution/normalize.py)
- Matching (blocking, comparison, rules, clusters): [`resolution/matching.py`](../services/research-engine/src/research_engine/resolution/matching.py)
- Store (ingest, pairs, clusters, canonical values, review): [`resolution/store.py`](../services/research-engine/src/research_engine/resolution/store.py)
- Job: [`resolution/engine.py`](../services/research-engine/src/research_engine/resolution/engine.py)
- Schema: [`migrations/sqlite/0005_entity_resolution.sql`](../services/research-engine/src/research_engine/migrations/sqlite/0005_entity_resolution.sql)
- Decisions: D-036 to D-044 in [decisions.md](decisions.md)

## 1. Three levels, kept apart

| Level                  | Where                                                                                                          | Changes?                                                              |
| ---------------------- | -------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| Raw source record      | `raw_records` and its projection `discovery_candidates`                                                        | Never                                                                 |
| Normalized observation | `business_names`, `business_locations`, `websites`, `contact_points`, `business_classifications`               | Never rewritten; a new normaliser version adds new observations       |
| Canonical business     | `businesses`: one **record** per candidate, and a **root** that the records of the same entity are merged into | Only `status` / `merged_into_id`, and the values it shows, all logged |

Every observation keeps the value as acquired (`*_raw`) next to its normalised form and points at an
`extraction` (`resolution.candidate` or `resolution.site`, with `NORMALIZER_VERSION`) over its raw
record, so each value answers where it came from (D-018).

A merge never moves an observation: the records stay, `merged_into_id` points at the root, and the
root's detail reads the observations of all its records. Unmerging changes two columns (D-036).

## 2. Normalisation — `NORMALIZER_VERSION = 1.0.0`

Pure functions: no store, no network, no clock. Each result says how present its value was:
`present`, `missing` (empty or a null token such as `N/A`), `unknown` (`unknown`, `TBD`),
`not_applicable` or `invalid` (there is a value, but not a valid one). Nothing is invented.

| Function                                   | What it does                                                                                                                                                                                                                                                               |
| ------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `clean_text`                               | HTML entities, UTF-8-read-as-cp1252 mojibake (`CafÃ©` → `Café`), NFC, invisible and control characters, whitespace, null tokens                                                                                                                                            |
| `business_name`                            | Display name kept; key folded (case, accents, punctuation, `&` → `and`, `Bros` → `brothers`), legal forms removed from the end only (`LLC`, `L.L.C.`, `Inc`, `S.A. de C.V.`…), `dba` split, distinctive vs generic words (typos of trade words count as generic)           |
| `phone`                                    | E.164 for NANP numbers and explicit `+` numbers; extension apart; `Tel:` labels and vanity letters; kind: geographic, toll-free, premium, fictional (555-01xx), international; a number without a country outside the NANP is `invalid`, never guessed                     |
| `email`                                    | `mailto:` and decoration removed; domain to lower-case punycode; local part kept as written (dots and `+tags` are different mailboxes); role (`info@`) and free-provider flags                                                                                             |
| `website`                                  | Scheme and host lower-cased, `www.`, default port, default document, fragment, tracking parameters and trailing slash removed; IDN to punycode; registrable domain (a public-suffix subset, and hosted-site tenants such as `name.wixsite.com`); profile platforms flagged |
| `social_profile`                           | Platform and stable handle for Facebook, Instagram, X, LinkedIn, YouTube, TikTok, Yelp, WhatsApp, Pinterest, Nextdoor; share and intent links are not profiles                                                                                                             |
| `us_address`                               | One-line or component form (components win): street with USPS abbreviations, number and street key, unit (`Suite 4`, `#4`, `Ste 4` as its own part), city, state, ZIP and ZIP+4 (a four-digit ZIP regains its leading zero), coordinates when given                        |
| `us_region`, `locality`, `county`, `metro` | USPS codes from codes or names; `St.` → `saint`; `County`/`Metro Area` suffixes                                                                                                                                                                                            |
| `category`                                 | Versioned vocabulary (`TAXONOMY_VERSION = 2026.1`, 24 keys with English, Spanish and NAICS aliases); the longest alias wins; the source label is always kept                                                                                                               |

## 3. From candidate to record

A resolution run first **ingests**: every candidate of the project without a record gets one
`business` (status `candidate`) and an `entity_records` row, then the observations of its
`resolution.candidate` extraction: names (legal and `dba`), the location (street from the raw
payload when the source had one), the website (a profile URL becomes a social contact), the phone and
email with their structural validity (`contact_validations`, method `structure`), and the category.

Pages crawled from the candidate's own website (phase 05) add, through `resolution.site`
extractions, the emails, phones and social profiles they publish, each with the page as provenance.
A value already observed for the business is not repeated (the listed phone and the footer phone are
one observation).

Ingest is idempotent: a candidate keeps its record for ever, and an extraction of the same version
over the same raw record is never written twice.

## 4. Matching — `RULES_VERSION = 1.0.0`

### 4.1 Blocking

| Kind      | Keys                                                                                                                                                                    |
| --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Exact     | registrable domain (not a platform), E.164 phone, `connector:external_id` of sources whose ids identify an entity                                                       |
| Candidate | distinctive name token within a city or ZIP, the full name key, street number + street within a ZIP or city, email domain (not a free provider), ~1 km coordinate cells |
| Generic   | a name made only of generic words is only compared with the same name in the same city or ZIP                                                                           |

A block with more than `MAX_BLOCK = 250` records says nothing about identity (a franchise
switchboard) and is skipped and counted. Nothing is compared globally (D-039).

### 4.2 Features and weights (log-odds)

Prior −5.0. Name similarity uses sorted tokens and weighs distinctive words most ("Garcia" is not
"Garza"); names that differ only in spacing score 0.99; numbered names with different numbers
("Store 12", "Store 13") are different names.

| Agreement                                                 | Weight | Conflict                          | Weight |
| --------------------------------------------------------- | -----: | --------------------------------- | -----: |
| same source id                                            |   +7.0 | different name (similarity < 0.5) |   −4.0 |
| same name (key)                                           |   +3.5 | different state                   |   −6.0 |
| same website                                              |   +3.5 | different unit at the same street |   −3.0 |
| same phone (geographic)                                   |   +3.0 | different street in the same area |   −2.5 |
| same street                                               |   +3.0 | different city                    |   −2.0 |
| similar name (≥ 0.90)                                     |   +2.5 | more than 5 km apart              |   −2.0 |
| within 150 m                                              |   +2.0 | different websites                |   −1.5 |
| same email domain                                         |   +1.5 | different phones                  |   −1.0 |
| same city · related name (≥ 0.80) · shared toll-free line |   +1.0 | different category                |   −0.5 |
| same ZIP · same category                                  |   +0.5 |                                   |        |

A generic name counts half. `score = logistic(prior + Σ weights)`.

### 4.3 Decision

| Outcome         | When                                                                                                                                                                                |
| --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `auto_merge`    | score ≥ **0.97**, at least two agreeing identities among name, website, geographic phone, street and source id, one of them the name or the source id, no conflict, no generic name |
| `manual_review` | score ≥ **0.25**, or a safety rule sends it to a person                                                                                                                             |
| `distinct`      | otherwise                                                                                                                                                                           |

### 4.4 Safety rules (they only add caution)

| Rule                            | Effect                                                                                                                                    |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `shared_phone_only`             | a phone (or toll-free line) agreeing with nothing else never merges automatically                                                         |
| `same_brand_different_location` | same name or website at another street, city or unit without the same local phone → `distinct`, and the pair keeps the two clusters apart |
| `address_changed`               | same name or website and the same local phone at another address → `manual_review` (history of one business)                              |
| `generic_name`                  | never merged automatically                                                                                                                |
| `insufficient_identity`         | a score above the threshold without two agreeing identities goes to review                                                                |
| different state                 | `distinct`, unless the same source record (`different_state_same_source` → review)                                                        |
| `cluster_conflict`              | an automatic merge that would join records a person kept apart, or a hard conflict separates, goes back to review                         |

No rule uses a surname, a language or a presumed origin.

**Risk**: an automatic merge is `low` with three or more agreeing identities and `medium` with two.
Any other pair is `high` when a safety rule fired, a conflict exists or a review scores below 0.6;
other reviews are `medium` and distinct pairs `low`.

### 4.5 What is stored

`match_candidates` keeps every pair that is not a clear non-match (review, merge, a distinct pair
above 0.05, or one a safety rule stopped): score, outcome, risk, `hard_conflict`, the compared
values field by field with their agreement, the blocking keys, every weight and rule with its detail
(`reasons_json`), the rules version, the job and who decided. The pair (`left < right`) never
changes; a new run refreshes its evidence.

## 5. Clusters

Clusters are recomputed from the stored pairs by union-find:

1. merges a person made, always;
2. automatic merges, by score, unless the two sides contain records a person kept apart or a hard
   conflict separates — then the pair goes back to review with `cluster_conflict`;
3. the root is the smallest business id (the oldest record, ids being UUIDv7), so it does not depend
   on the order merges were found.

The differences with the current `businesses` are written in batches: a record joining a cluster is
`merged` with `merged_into_id = root` and a `business_merges` row (the pair, its explanation, who and
why); a record leaving it goes back to the status it had and its merge row is closed
(`unmerged_at`, `unmerged_by`, `unmerge_reason`), never deleted. Both are audited (`merge`,
`unmerge`). The same function serves a run and a person's decision (D-040).

## 6. Canonical values — `POLICY_VERSION = 1.0.0`

For every root and field (`name`, `location`, `website`, `phone`, `email`, `category`), one
observation of the cluster is chosen:

- most trusted source first (official API and public registry 3; directory, public dataset,
  platform API and business website 2; web search, file import, manual seed and synthetic 1);
- then confidence, then freshness — freshness before confidence for a location, so a move is
  followed while the old address stays visible;
- then the oldest id, so the choice is deterministic.

`canonical_values` records the choice, its method (`policy` or `manual`), its reason and
`alternative_count`: how many different values the cluster holds for that field. Above one, the
business has a conflict: the list shows `conflict_count`, the inspector marks the field and shows
every value. A person's choice stays while its observation is still in the cluster; choices are
superseded, never overwritten. The root's display name follows its chosen name (D-041).

## 7. The reversible flow

```text
candidates ─ ingest ─▶ records + observations
                          │
                  compare (rules vN) ─▶ match_candidates ◀── person: merge · keep separate · defer · undo
                          │                                        (review_decisions, with reason)
                      recluster ─▶ businesses.status / merged_into_id  +  business_merges  +  audit_log
                          │
                   canonical values ─▶ canonical_values (policy or manual, superseded)
```

- **Undo** reverts the latest decision on a pair, whatever it was; undoing what the engine decided
  on its own sends the pair to a person. Undo is a decision too: undoing it restores what it replaced.
- **A later run** (new candidates, a new rules or normaliser version) refreshes the evidence of every
  pair but never the status of a pair a person decided; engine pairs the rules no longer compare are
  retired as `distinct` and `separate` with `no_longer_compared`, and clusters follow.
- Decisions are refused while a run is rewriting the same project.

## 8. The job

`jobs.kind = resolution`, one open run per project (`resolution.start` with a null project starts
one per project with something to resolve).

| Stage   | How                                                                                      | Resume                     |
| ------- | ---------------------------------------------------------------------------------------- | -------------------------- |
| ingest  | batches of 200 candidates, one transaction each, checkpoint with the last candidate id   | from the cursor            |
| compare | features, blocking and comparisons in memory; pause and cancel checked every 2,000 items | recomputed (deterministic) |
| apply   | pairs, clusters and canonical values in batches of 500, differences only                 | re-applied (idempotent)    |

Pause, resume and cancel go through `jobs.apply_control`; a run a dead worker left running is paused
at startup with `run_interrupted`, never resumed silently. The run's counts are in its last
checkpoint and in the `run_succeeded` event (D-042).

## 9. Interface and messages

Businesses has two views (D-043): the canonical list (records per business and conflicts shown on
each row, merged records hidden but found by search) and **Possible duplicates**: the queue by score
with filters by status, risk, minimum score and source; the last run and a button to run it; and a
side-by-side review of one pair: every field with its agreement highlighted, the reasons with their
weights, the evidence and source of every value of both records, merge / keep separate / defer /
undo with an optional reason, and the history of decisions and merges. The business inspector lists
the records of a cluster, marks the value shown for each field, flags conflicts, lets a person show
another value, and keeps the merge history.

| Request                   | Answer              | Command                  |
| ------------------------- | ------------------- | ------------------------ |
| `resolution.start`        | `ResolutionRunPage` | `start_resolution`       |
| `resolution.control`      | `ResolutionRun`     | `control_resolution`     |
| `resolution.runs`         | `ResolutionRunPage` | `list_resolution_runs`   |
| `matches.list`            | `MatchPage`         | `list_matches`           |
| `matches.get`             | `MatchDetail`       | `get_match`              |
| `matches.act`             | `MatchDetail`       | `act_on_match`           |
| `businesses.choose_value` | `BusinessDetail`    | `choose_canonical_value` |

All additive: `protocol_version` and `data_contract_version` stay 1. The core validates filters,
reasons and every answer: a queue page must obey its filters and order, a pair detail must describe
that pair and both records, and a business detail may only hold observations of its own records,
with each canonical value pointing at one of them.

## 10. Measurements

Synthetic corpora in [`tests/resolution_corpus.py`](../services/research-engine/tests/resolution_corpus.py):
fictitious names, `.example` domains, 555-01xx phones. Precision is judged on the resulting clusters
(a merge through transitivity counts); a true pair counts for recall once, whether merged or sent to
a person.

| Corpus                               | Records | True pairs | Automatic precision | Automatic recall | Recall with review |
| ------------------------------------ | ------: | ---------: | ------------------: | ---------------: | -----------------: |
| Labelled hard cases                  |      31 |         11 |           **1.000** |            0.727 |          **1.000** |
| Generated at scale (18,000 entities) |  30,719 |     16,305 |           **1.000** |            0.886 |          **0.999** |

At scale, 14,438 pairs merged automatically, none wrongly, and 1,861 went to review (1,849 of them
true duplicates). The run compared 55,093 pairs out of 471,813,121 possible (45,700 blocks, the
largest of 28 records, none skipped) in 15.4 s on this machine, 8.6 s of them normalising. A store
run over 2,551 generated candidates took 7.0 s (367 candidates/s) for ingest, 11,526 observations,
1,628 pairs, 678 clusters and their canonical values.

## 11. Limits

- The public-suffix list is a subset and phones are validated for the NANP only.
- The address parser is conservative: an unusual format keeps its text in `street` and matches less.
- Social handles and JSON-LD of crawled pages are not yet matching features (phase 08 reads the site).
- A decision recomputes the clusters of the whole project (fast at tens of thousands of records).
- Lead stages, notes and tags live on the root; a later merge does not move them yet (phase 12).
