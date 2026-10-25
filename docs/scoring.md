# Lead scoring

Phase 11 decides which businesses deserve research or contact, why, with which offer and how sure
the answer is. It does not contact anyone, change a fact or let a model move a number. The goal is a
short list of defensible opportunities, not many rows.

```text
facts of a canonical business ─ features (evidence, unknown or not applicable)
  ─ category rules and offers (not applicable) ─ gates (do not contact, closed, exclusions, Latino
  requirement, observable problem, must-have criteria) ─ components 0–100 (unknown left out)
  ─ OPPORTUNITY_SCORE ─ FINAL_LEAD_SCORE ─ tier ─ reasons, contact-now signals, inferred impacts,
  risks, missing data, offer, message draft
```

| Module              | Role                                                                                          |
| ------------------- | --------------------------------------------------------------------------------------------- |
| `scoring/model.py`  | The engine: pure and deterministic (no store, clock, network or model), metrics               |
| `scoring/facts.py`  | The facts of each business, read from the store, independent of any ICP or model              |
| `scoring/store.py`  | Profiles, models and versions; runs, scores, reviews, outcomes; ranking, compare, calibration |
| `scoring/engine.py` | Scoring as a resumable job                                                                    |

`ENGINE_VERSION = 1.1.2` and `FACTS_VERSION = 1.0.0` are stored with every score (1.1 adds
`gate.lead_generation`, D-107).

## 1. Facts — `FACTS_VERSION = 1.0.0`

For each live canonical business of a project, at the moment `collected_at`:

- names, locations, categories (classification labels) and domains observed on every record resolved
  into it (current extractions); the number of acquired records behind its names; the canonical
  fields whose records disagree;
- status, first and last seen, and whether the project holds synthetic test data;
- the newest website audit of a crawled site that speaks for it (the phase 07 page links): status,
  coverage, pages analyzed, subscores, the findings of the rules features read plus every failing or
  warning finding, detected technologies, the previous audit and how many subscores and findings
  changed;
- its published channels decided now as phase 09 decides them (hypotheses left out, cached mail
  answers only, no DNS), contactability, published people and the do-not-contact list;
- the Latino assessment it shows (a person's review first, else the rules) and its contradictions.

The facts are stored with the score (`business_scores.facts_json`), so a run can be scored again over
exactly the same facts. Nothing here calls the network or a model.

## 2. Features

A feature is **known** (a value from 0 to 1 that rests on evidence), **unknown** (the facts cannot
show it) or **not applicable** (it does not apply to this business). A value without evidence is
turned into unknown (`no_evidence`). Unknown is never a zero and not applicable is never a penalty:
both are left out of every mean (section 5).

A feature that reads the website is not applicable when no website is known, unknown while the site
has no audit, and unknown when its rule could not be evaluated.

| Feature                                | Component         | Problem                    | Weight | Value                                                                                         |
| -------------------------------------- | ----------------- | -------------------------- | -----: | --------------------------------------------------------------------------------------------- |
| `gap.no_website`                       | DIGITAL_GAP       | `no_website`               |      3 | 1 when no source lists a website; not applicable when one is known                            |
| `gap.website_unreachable`              | DIGITAL_GAP       | `website_unreachable`      |      3 | 1 when the crawl analyzed no page; else `technical.page_errors` fail 1, warning 0.5, pass 0   |
| `gap.conversion_path`                  | DIGITAL_GAP       | `weak_conversion_path`     |      3 | worst of `conversion.primary_cta` and `conversion.lead_form`: fail 1, warning 0.5, pass 0     |
| `gap.online_booking`                   | DIGITAL_GAP       | `no_online_booking`        |      1 | `conversion.booking_or_quote` warning 1, pass 0                                               |
| `gap.local_seo`                        | DIGITAL_GAP       | `weak_local_seo`           |      2 | 1 when the local SEO subscore is below `weak_subscore_below`, else 0                          |
| `gap.technical_seo`                    | DIGITAL_GAP       | `weak_technical_seo`       |      1 | 1 when the SEO subscore is below `weak_subscore_below`, else 0                                |
| `gap.mobile`                           | DIGITAL_GAP       | `poor_mobile_experience`   |      2 | `mobile.viewport` fail 1, warning 0.5, pass 0                                                 |
| `gap.measurement`                      | DIGITAL_GAP       | `measurement_not_detected` |      1 | `measurement.analytics` warning 1, pass 0 (not detected is not absent)                        |
| `health.recently_seen`                 | BUSINESS_HEALTH   |                            |      2 | last listed within 180 days 1, a year 0.5, older 0                                            |
| `health.site_answers`                  | BUSINESS_HEALTH   |                            |      2 | no page analyzed 0; else `technical.page_errors` pass 1, warning 0.5, fail 0                  |
| `health.site_maintained`               | BUSINESS_HEALTH   |                            |      1 | `content.freshness` pass 1, warning 0                                                         |
| `health.trust_signals`                 | BUSINESS_HEALTH   |                            |      1 | `conversion.trust_signals` pass 1, warning 0.5, fail 0                                        |
| `health.multiple_sources`              | BUSINESS_HEALTH   |                            |      1 | two or more acquired records 1, else 0                                                        |
| `capacity.multiple_locations`          | BUYING_CAPACITY   |                            |      2 | two or more distinct locations 1, one 0; unknown without a location                           |
| `capacity.paid_tools`                  | BUYING_CAPACITY   |                            |      1 | a booking, ecommerce or call tracking technology 1; unknown when none is detected             |
| `capacity.team_published`              | BUYING_CAPACITY   |                            |      2 | two or more published people 1, one 0.5, none on an audited site 0                            |
| `capacity.site_depth`                  | BUYING_CAPACITY   |                            |      1 | five or more pages analyzed 1, two 0.5, fewer 0                                               |
| `intent.advertising_active`            | COMMERCIAL_INTENT |                            |      2 | `measurement.advertising` pass 1; unknown otherwise                                           |
| `intent.site_changed`                  | COMMERCIAL_INTENT |                            |      1 | changed since the previous audit and audited within 90 days 1, else 0; unknown with one audit |
| `intent.reputation_without_conversion` | COMMERCIAL_INTENT |                            |      2 | trust signals pass while the conversion subscore is weak 1, else 0                            |
| `data.coverage`                        | DATA_CONFIDENCE   |                            |      3 | share of the weighted opportunity features that apply and are known                           |
| `data.freshness`                       | DATA_CONFIDENCE   |                            |      2 | newest data (listing or audit) within 30 days 1, 90 0.7, 180 0.4, older 0.1                   |
| `data.agreement`                       | DATA_CONFIDENCE   |                            |      1 | 1 − 0.25 per field whose records disagree                                                     |
| `data.audit_coverage`                  | DATA_CONFIDENCE   |                            |      1 | the audit's coverage                                                                          |

Weights are those of the default model; a model version can change every one of them. The
contactability of phase 09 appears as `contact.<code>` features (availability, validation, role,
freshness and source), with the channels as evidence.

Two rules make a known feature not applicable:

- **category rules of the model** (`not_applicable`): a feature and category terms matched as whole
  words. The default model does not weigh online booking for roofing, construction, general
  contractors, wholesale and manufacturing (`not_applicable_to_category`);
- **offers**: a problem that no offer applicable to the business solves is not weighed
  (`no_offer_solves`). An offer does not apply to the categories in its `not_for_categories`.

## 3. Gates

Gates are decided before any weight. A failed gate excludes the business, which has no total. An
undecided gate leaves it with insufficient data. Every decided gate carries its evidence.

| Gate                      | Fails when                                                                                     | Undecided when                                |
| ------------------------- | ---------------------------------------------------------------------------------------------- | --------------------------------------------- |
| `gate.do_not_contact`     | the business is on the do-not-contact list                                                     | never                                         |
| `gate.business_closed`    | its status is `closed`                                                                         | never                                         |
| `gate.lead_generation`    | its name is only a trade and a place: «Plumber Sugar Land TX», «Carpet Cleaning Katy» (D-107)  | never                                         |
| `gate.exclusions`         | an excluded name or category term, domain (or subdomain), region or postal code prefix matches | never                                         |
| `gate.latino`             | the status is not the one required (only when the ICP asks)                                    | Latino evidence has not been evaluated        |
| `gate.observable_problem` | problems were evaluated and none an offer solves is observed (when the model requires one)     | no problem an offer solves could be evaluated |
| `criterion.<name>`        | a must-have criterion fails (section 4)                                                        | the criterion is undecided                    |

`verified_latino_owned` passes only a verified Latino-owned business. `market_affinity` passes any
status other than `unknown`, ownership levels included. Latino evidence filters a campaign; it never
measures quality or buying capacity.

## 4. ICP profiles

A profile names what is worth researching and what the seller can actually deliver. Each criterion
has an importance (`must_have`: a gate; `nice_to_have`: counts in ICP_MATCH; `ignore`: not read) and
a weight from 0 to 10.

| Criterion          | Passes when                                                                                                                     | Undecided when                               |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| `geography`        | not in an excluded area, and in an included one (or none is listed)                                                             | no location is observed                      |
| `categories`       | no excluded term matches, and a term matches (or none is listed)                                                                | no category or service is observed           |
| `size`             | distinct locations ≥ `min_locations` (the only observable size proxy)                                                           | no location is observed                      |
| `freshness`        | last listed within `max_days_since_seen`                                                                                        | never                                        |
| `reputation`       | —                                                                                                                               | always: no connected source rates businesses |
| `digital_maturity` | the band is wanted: mean website subscore below 50 low, below 80 medium, else high; no website is low                           | the site is not audited                      |
| `problems`         | a wanted problem is observed                                                                                                    | a wanted problem is unknown                  |
| `channels`         | a usable published channel of the wanted types (not invalid, suppressed or stale), and a published decision maker when required | no channel and no crawled site               |
| `buying_capacity`  | capacity proxies with a value of at least 0.5 ≥ `min_proxies`                                                                   | the unknown proxies could still reach it     |

Areas are `country, region, city, postal code`; a part left empty matches anything, and a US listing
without a country matches by region. Terms match as whole words, without case or accents.

Profile checks: errors `no_offers`, `offer_solves_nothing`, `duplicate_offer_key` and
`empty_must_have`; warnings `reputation_unknown` and `nothing_required`. A version with errors can be
saved and simulated, not activated or run.

## 5. Components and formulas

Every component is 0–100 or unknown (null).

| Component                                                                         | Formula                                                                                                                  |
| --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| BUSINESS_HEALTH, DIGITAL_GAP, BUYING_CAPACITY, COMMERCIAL_INTENT, DATA_CONFIDENCE | 100 × Σ(value × weight) / Σ weight over its known weighted features                                                      |
| ICP_MATCH                                                                         | 100 × Σ(criterion × weight) / Σ weight over decided criteria that are not ignored (pass 1, fail 0)                       |
| CONTACTABILITY                                                                    | the phase 09 subscore; unknown for a suppressed business                                                                 |
| LATINO_CONFIDENCE                                                                 | 100 × confidence of the assessment the business shows, when its status is not unknown                                    |
| OPPORTUNITY_SCORE                                                                 | (DIGITAL_GAP × 45 + BUSINESS_HEALTH × 20 + BUYING_CAPACITY × 20 + COMMERCIAL_INTENT × 15) / Σ weight of the known inputs |
| FINAL_LEAD_SCORE                                                                  | (ICP_MATCH × 35 + OPPORTUNITY_SCORE × 40 + CONTACTABILITY × 25 + LATINO_CONFIDENCE × 0) / Σ weight of the known inputs   |

- **Unknown inputs are left out**, and the others are renormalized over the weight that is known.
  Each component's coverage is its known weight over its whole weight.
- **Contributions** are weight × value / known weight. They add up to the formula's value (each is
  rounded to four decimals; the core accepts a drift of 0.01).
- **Latino evidence is not an input of OPPORTUNITY_SCORE**, and its weight in the final score is 0 by
  default. Weighting it raises the `latino_weighted` warning.
- **Points and impact** explain a feature and never add to the total. Points are 100 × value × weight
  / the known weight of its component. Impact is how much those points move the final score through
  OPPORTUNITY_SCORE, or directly for contactability.

The tier, in this order:

1. `excluded`: a gate failed. There is no total.
2. `insufficient_data`: a gate is undecided, the total is unknown, or less than `min_known_weight`
   (60 %) of the final formula's weight is known.
3. `priority` at `priority_threshold` (70) or more, `review` at `review_threshold` (50) or more, else
   `low`.

**Confidence** is DATA_CONFIDENCE / 100 × the known share of the final weight, or the known share
alone when data confidence is unknown. Coverage and data confidence change how sure a score is, not
the opportunity.

Model checks: errors `features_mismatch`, `unknown_feature`, `inputs_mismatch`, `nothing_weighted` and
`thresholds_order`; warning `latino_weighted`.

## 6. The scorecard

`business_scores.scorecard_json` holds, for each business in each run:

| Field                                               | Content                                                                                                                                  |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `tier`, `total_score`, `confidence`, `known_weight` | Section 5                                                                                                                                |
| `versions`                                          | engine, facts, model key and version, heuristic flag, ICP name and version                                                               |
| `components`                                        | value, weight, contribution, coverage and the formula text                                                                               |
| `features`, `criteria`, `gates`                     | state or outcome, value, weight, points, impact, detail, reason and evidence                                                             |
| `reasons`                                           | the top 3 positive reasons: observed features and passed criteria, by impact, then id                                                    |
| `contact_now`                                       | up to 5 commercial intent signals (why contact now)                                                                                      |
| `impacts`                                           | the probable commercial impact of each observed problem, marked `is_inference`                                                           |
| `risks`                                             | `records_disagree`, `latino_contradicted`, `measurement_uncertain`, `partial_audit`, `stale_data`, `no_usable_channel`, `gate_undecided` |
| `missing`                                           | unknown inputs, with the share of the whole final formula each one stands for                                                            |
| `offer`                                             | the applicable offer that solves the observed problems with the most impact, why, and its evidence                                       |
| `message`                                           | an English draft built from the observed problems and the offer, `is_draft`; none for an excluded lead                                   |

Evidence is a business, a website audit, finding or technology, a contact channel, a decision maker
or a Latino assessment, with at most 8 per item. The core refuses a scorecard with any of these
faults:

- its contributions do not add up;
- its tier disagrees with its gates;
- a gate is decided without evidence;
- a known feature, a reason or the offer has no evidence.

## 7. Versions, runs and history

- **Versions.** Saving a changed definition creates an immutable, permanent version. Saving an
  unchanged one creates nothing. Activating a version, archiving and restoring need a reason and are
  audited. A version with errors cannot be activated. An archived profile or model cannot gain
  versions or run.
- **The default model.** `lead_intelligence.heuristic` v1 is created on the first catalog read.
  Other models are keyed `custom.<slug>`. Every version is declared heuristic.
- **Runs.** A run is one job (`jobs.kind = lead_scoring`), and a project has at most one open run. It
  applies an ICP version and a scoring version, both without errors and not archived, to every live
  canonical business of the project. With `facts_from_run_id` it uses the stored facts of an earlier
  finished run of the same project instead, which is how two versions are compared on identical
  facts. It works in batches of 200 with a checkpoint after each. A business already scored is
  skipped, so a pause, cancel or restart never scores twice.
- **History.** Runs, scores and components are never rewritten or deleted (triggers). A new version
  or run leaves every earlier score as it was; the scorecard panel lists them.
- **Rank.** The order is tier (priority, review, low, insufficient data, excluded), then total
  descending (a missing total after), then confidence descending, then business id. It is the same in
  Python (`model.rank_key`) and SQL (`store.RANK_ORDER`, `ROW_NUMBER()` over the whole run). A rank is
  a place in the whole run: filters and pages never renumber it.
- **Compare.** Two finished runs of the same project are compared on:
  - how many businesses entered and left the top K;
  - the mean absolute rank change of the businesses both scored;
  - each business of either top K with both ranks, tiers and totals;
  - `same_facts`, true when one run reused the other's facts or both reused the same run.

## 8. Simulation

`scoring.simulate` scores ICP and scoring drafts in memory. It reads the project's businesses now (at
most 5 000; above that, simulate over a run's stored facts) or the facts a run stored. It writes
nothing: no run, score, lead or audit row. It returns:

- tier counts and the score distribution;
- for each gate, how many businesses pass, fail or stay undecided, with up to 5 failing examples;
- the top N leads;
- what keeps the drafts from running.

Over the same facts it gives the same scores as a run.

## 9. Reviews and outcomes

- **Review** of a score: confirm, dismiss or withdraw (`lead_score_confirmed`,
  `lead_score_dismissed`, `lead_score_review_withdrawn`). It needs a reason and is stored in
  `review_decisions` on the business, with the run as its value. It changes no score.
- **Outcome** of working the lead: `not_contacted`, `attempted`, `connected`, `qualified`,
  `meeting_booked`, `proposal_sent`, `won`, `lost` (with a lost reason, which only a lost lead has),
  `invalid_contact` or `not_interested`. It moves `businesses.lead_stage` along its state machine
  ([data-model.md](data-model.md) section 5) with a reason and an audit entry. `lead_outcomes` keeps
  it for good with the previous stage and the run it was worked from.
- **Nothing is sent.** The message is a draft for a person to adapt; outreach is not implemented.

## 10. Calibration

The weights are heuristic and nothing is trained. Calibration measures a run against what happened
afterwards.

- **Labels.**
  - Positive: `qualified`, `meeting_booked`, `proposal_sent`, `won`.
  - Negative: `lost`, `not_interested`, `invalid_contact`.
  - No label: `not_contacted`, `attempted`, `connected`.
- **Leakage.** A business is labelled by its latest outcome that happened after its facts were read
  (`business_scores.facts_collected_at`). Earlier outcomes are counted as `leakage_excluded` and
  never used.
- **Metrics.**
  - precision@K for K 10, 25, 50 and 100 (positives among the labelled leads of the top K);
  - lift by decile (ten groups of the ranking, each group's positive rate against the overall rate);
  - calibration bands (0–20 … 80–100: mean score next to the observed positive rate);
  - conversion by tier cohort;
  - coverage (share of businesses with a scored tier) and the labelled share.
- **Warnings.** `no_labels`, `too_few_labels` (below 30), `no_positives` and `heuristic_weights`.

Fitting weights is a later, separate decision that needs volume and labels.

## 11. Local AI

A model may word a reason; it never decides one. The scorecard detail shows the problems of the newest
valid, not rejected `opportunity_reasoning` answer for the business (phase 10). A problem is kept only
when every reference it cites is a website finding this scorecard's features rest on. The others are
dropped and counted (`ai_dropped`). A kept wording never changes a number, the tier or the evidence
list, and the core refuses one that cites anything else.

## 12. Messages and interface

Messages: `scoring.catalog`, `scoring.save_icp`, `scoring.save_model`, `scoring.change`,
`scoring.simulate`, `scoring.start`, `scoring.control`, `scoring.runs`, `scoring.leads`,
`scoring.scorecard`, `scoring.compare`, `scoring.calibration` and `scoring.act`.

| Screen                          | What it does                                                                                                                                                                                                                                              |
| ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ICP Builder                     | Profiles; the nine criteria with importance, weight and parameters; Latino requirement; exclusions; offers; save a version or save as new; preview on real data; versions to load, compare field by field and activate; archive and restore with a reason |
| Scoring Models ▸ Models         | The formula as text, final and opportunity weights, thresholds, feature weights with descriptions, category rules, versions, archive                                                                                                                      |
| Scoring Models ▸ Runs           | Start a run (facts read now or stored by a run), pause, resume, cancel, tiers and distribution, compare two runs                                                                                                                                          |
| Scoring Models ▸ Calibration    | Section 10 for a finished run                                                                                                                                                                                                                             |
| Lead Explorer                   | Since phase 12, every lead of a run with AND/OR filters, saved views, bulk actions and the research workspace ([lead-explorer.md](lead-explorer.md)); `scoring.leads` still serves keyset pages of a run                                                  |
| Business inspector ▸ Lead score | The scorecard, earlier scores, AI wording, outcomes and reviews; review or record an outcome with a reason                                                                                                                                                |
| Jobs                            | Scoring runs next to every other engine's, with cancel                                                                                                                                                                                                    |

## 13. Assumptions and limits

| Assumption or limit                                                                                                                                                                                        | Consequence                                                                                           |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Weights and thresholds are a declared judgement: digital gap weighs most because the offers address digital problems; ICP 35, opportunity 40, contactability 25; priority 70, review 50; 60 % known weight | Change them as a new version; calibrate once outcomes exist                                           |
| No connected source provides ratings or reviews                                                                                                                                                            | The reputation criterion is always undecided; trust signals shown on the site stand in for reputation |
| Size is seen only as distinct locations; buying capacity only through explicit proxies                                                                                                                     | No employee, revenue or financial claim is made                                                       |
| Paid tools and advertising are detected or unknown, never absent                                                                                                                                           | Tags hidden in a tag manager do not lower a score                                                     |
| Only the newest audit of a site is read                                                                                                                                                                    | Older audits count only through `intent.site_changed`                                                 |
| Channels and contactability are decided when the facts are read, with cached mail answers                                                                                                                  | Stored facts keep the channels as they were then                                                      |
| Phrases and message drafts are in English                                                                                                                                                                  | Translate before use                                                                                  |
| A simulation reads at most 5 000 businesses from the store                                                                                                                                                 | Larger projects simulate over a run's stored facts                                                    |
| Each score stores its facts and scorecard (about 9 KB and 21 KB per business on the synthetic seed)                                                                                                        | About 30 KB per business per run of history                                                           |
| Score runs no longer reference a dataset snapshot                                                                                                                                                          | The facts stored per score are the snapshot; exports: phase 13                                        |
| No model is trained                                                                                                                                                                                        | Until labels are plentiful, precision@K and lift only describe                                        |
