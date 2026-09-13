# Latino evidence

How Lead Intelligence tells **explicitly evidenced Latino ownership** apart from **affinity with the
Spanish-speaking market**, and how every result says what supports it and what it cannot conclude.
Delivered in phase 07.

- Rules and extraction patterns (pure): [`latino/rules.py`](../services/research-engine/src/research_engine/latino/rules.py)
- Store (sources, evidence, lifecycle, assessments, review): [`latino/store.py`](../services/research-engine/src/research_engine/latino/store.py)
- Job: [`latino/engine.py`](../services/research-engine/src/research_engine/latino/engine.py)
- Schema: [`migrations/sqlite/0006_latino_evidence.sql`](../services/research-engine/src/research_engine/migrations/sqlite/0006_latino_evidence.sql)
- Decisions: D-045 to D-051 in [decisions.md](decisions.md)

## 1. Two tracks that never mix

| Track             | What it can speak to                                    | Highest status it can reach |
| ----------------- | ------------------------------------------------------- | --------------------------- |
| **Ownership**     | who owns the business, only when stated explicitly      | `verified_latino_owned`     |
| **Market**        | that the business serves Spanish speakers               | `spanish_market_affinity`   |
| **Contradiction** | a denial of ownership, or an announced ownership change | lowers or removes ownership |

`spanish_market_affinity` never means Latino-owned, however many market signals there are.

**Never read, never evidence:** names of the business or of people, surnames, photos, the address or
neighbourhood, presumed nationality, "ethnicity" columns (data vendors usually infer them from
names), and anything a model concludes on its own. The payload scanner only opens the columns listed
in section 3; every other column is never looked at. Every assessment carries the limitation
`names_not_used`.

## 2. Statuses

Stable codes are stored and exported; the interface shows words next to the code, never a flag or a
colour that turns a probability into a certainty.

| Code                           | Label                            | Meaning                                                                                                        |
| ------------------------------ | -------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `verified_latino_owned`        | Verified Latino-owned            | Strong, fresh ownership evidence about this location from an official or registry source, not contradicted     |
| `high_confidence_latino_owned` | Latino-owned, high confidence    | Strong ownership evidence from a trusted source, or two independent sources that each carry weight             |
| `probable_latino_owned`        | Probably Latino-owned            | Any other ownership evidence, ownership an organization shares with other locations, or a weaker contradiction |
| `spanish_market_affinity`      | Spanish-speaking market affinity | Only market signals (or ownership evidence a stronger denial outweighs, when market signals exist)             |
| `unknown`                      | Unknown                          | Nothing that counts. Never a guess, and not a negative: `absence_is_not_evidence`                              |

## 3. Evidence matrix — `EXTRACTOR_VERSION = 1.0.0`

| Claim code                   | Track         | Type                  | Strength | Max age | Found in                                                                                                                                                                    |
| ---------------------------- | ------------- | --------------------- | -------- | ------: | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ownership_statement`        | ownership     | ownership_declaration | strong   | 3 years | a page's first-person or prominent (title, heading, meta description, JSON-LD) declaration: "we are a proud Latina-owned practice", "somos una empresa de propiedad latina" |
| `ownership_mention`          | ownership     | ownership_declaration | moderate | 3 years | the same phrase in running text without first person: "the bakery is Hispanic-owned"                                                                                        |
| `ownership_attribute`        | ownership     | ownership_declaration | strong   | 3 years | a source column that flags ownership (`Latino-owned: yes`, `hispanic_owned: true`) or states it in words (`ownership: Hispanic-owned`)                                      |
| `certification`              | ownership     | certification         | strong   | 3 years | a certifications column naming a Hispanic- or Latino-owned certification, with its expiry date when a column gives one                                                      |
| `certification_statement`    | ownership     | certification         | strong   | 3 years | a page stating the business is certified as Hispanic- or Latino-owned                                                                                                       |
| `ownership_directory`        | ownership     | directory_membership  | moderate | 2 years | a directory or membership column naming a Latino- or Hispanic-**owned** directory                                                                                           |
| `ownership_denied`           | contradiction | ownership_declaration | strong   | 3 years | `hispanic_owned: no`, "not a Latino-owned business"                                                                                                                         |
| `ownership_changed`          | contradiction | ownership_declaration | moderate | 3 years | "under new ownership", "bajo nueva propiedad"                                                                                                                               |
| `spanish_service`            | market        | market_language       | moderate | 2 years | "se habla español", "we speak Spanish", "bilingual (English/Spanish)", "services available in Spanish"                                                                      |
| `hispanic_community`         | market        | market_language       | moderate | 2 years | "serving the Hispanic community", "comunidad hispana"                                                                                                                       |
| `spanish_language_attribute` | market        | market_language       | moderate | 2 years | a languages column that includes Spanish                                                                                                                                    |
| `structured_language`        | market        | market_language       | moderate | 2 years | JSON-LD `availableLanguage`, `knowsLanguage` or `inLanguage` set to Spanish                                                                                                 |
| `community_directory`        | market        | directory_membership  | weak     | 2 years | a Hispanic chamber or community directory whose criterion is not ownership                                                                                                  |
| `spanish_page`               | market        | market_language       | weak     | 2 years | `lang="es…"`, or at least 60 % Spanish among ≥ 20 common function words                                                                                                     |
| `spanish_version`            | market        | market_language       | weak     | 2 years | a link to a Spanish version (`Español`, `/es/`, `es.` host)                                                                                                                 |

Ownership phrases about **other** businesses are not claims: a phrase preceded by words such as
"support", "find", "our directory lists", "apoyamos" or followed by "businesses", "restaurants",
"vendors" is skipped. Patterns cover English and Spanish.

**What is stored for each piece of evidence** (`evidence_items`, only `status` ever changes):

| Prompt field                   | Where                                                                                                   |
| ------------------------------ | ------------------------------------------------------------------------------------------------------- |
| evidence_id, business_id       | `id`, `business_id` (the record the source describes; a canonical business reads all its records)       |
| claim_type                     | `value_json.claim` (the code above) and `value_json.track`; `claim` holds the sentence in words         |
| observed_value                 | `value_json.observed_value` (at most 500 characters) and details such as `expires_on`                   |
| source_type, source_url/record | the extraction's raw record: connector kind, `source_url`, `external_id`, acquired page                 |
| page/field/context             | `field_path` (`payload.certification`, `main_text`, `headings[0]`, `json_ld…`) and `snippet`            |
| captured_at                    | `observed_at` = when the source was acquired                                                            |
| extractor_version              | the extraction: `latino.payload`, `latino.site` or `latino.model`, version `EXTRACTOR_VERSION`          |
| content_hash / snapshot        | `snippet_sha256`, and the raw record (payload hash) and acquired page (body hash) behind the extraction |
| strength                       | `strength`                                                                                              |
| freshness                      | computed by the rules and stored with each assessment that cites the evidence (section 4)               |
| status                         | `status`: `active`, `stale`, `contradicted`, `revoked`                                                  |

Only the sentence that shows a claim is kept (at most 300 characters around the match); pages are
never copied.

## 4. Rules — `RULES_VERSION = 1.0.0`

Pure and versioned (`Ruleset`): the same evidence, rules and moment always give the same result.
Four factors are kept apart:

| Factor                  | Value                                                                                                                                                                                                |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `evidence_strength`     | strong 0.9 · moderate 0.6 · weak 0.3                                                                                                                                                                 |
| `source_reliability`    | official API 0.95 · public registry 0.95 · platform API 0.85 · directory 0.80 · business website 0.80 · public dataset 0.75 · file import 0.60 · synthetic 0.60 · manual seed 0.50 · web search 0.40 |
| `freshness`             | 1.0 up to a year old, then down to 0.5 at the claim's maximum age, and 0 beyond it or past an expiry date (the evidence becomes `stale`)                                                             |
| `assessment_confidence` | `1 − Π(1 − best score of each independent source)`, capped at 0.99, where a score is strength × reliability × freshness                                                                              |

Independent sources: a connector for source records, a registrable domain for crawled pages, so ten
pages of one site are one source.

**Deciding the status** from the active, fresh evidence of a canonical business (all its records):

1. Evidence older than an `ownership_changed` announcement does not count (`ownership_changed`).
2. Ownership evidence gives a tier: **verified** when some strong evidence comes from a source of
   reliability ≥ 0.90, is fresh and describes this location; otherwise **high confidence** when some
   strong evidence comes from a trusted source (≥ 0.75) or two independent sources score ≥ 0.40 each;
   otherwise **probable** (strong evidence from an untrusted source adds `untrusted_source`).
3. When every piece describes an organization several canonical businesses share (a brand's website
   listed by several locations), the tier is capped at probable (`organizational_evidence`,
   `location_ownership_unknown`).
4. An active denial scoring at least as much as the best ownership source removes the ownership
   status; a weaker denial caps it at probable and multiplies the confidence by `1 − denial score`.
   Either way the warning `contradicted` is shown.
5. Without an ownership status, market signals give `spanish_market_affinity` (confidence from the
   market sources, `affinity_is_not_ownership`); nothing gives `unknown` with confidence 0.
6. Ownership resting only on declarations and listings adds `self_declared`: no certification or
   registry confirms it.

Every assessment stores its status, confidence, rules version, a rationale with the counts, the
warnings and what it cannot conclude, and each piece of evidence it cites with the role
(`supports`, `contradicts`, `context`), reliability, freshness, score, whether it **counted** and
whether it is organizational.

## 5. Lifecycle and recalculation

| Status         | When                                                                                                                | Reversible?                      |
| -------------- | ------------------------------------------------------------------------------------------------------------------- | -------------------------------- |
| `stale`        | older than its claim's maximum age, or a certification past its expiry date                                         | back to `active` if rules change |
| `revoked`      | a newer snapshot of the same page no longer makes the claim for that business                                       | never (removed at the source)    |
| `contradicted` | ownership evidence that a newer denial from a source at least as reliable, or a newer ownership change, contradicts | back to `active` when it lifts   |

Every move is a status change with its reason in `audit_log`; evidence is never deleted or edited.
An evaluation run compares each business's calculation with the current one (status, confidence,
rules version, cited evidence and their role, warnings) and only when something differs writes a new
assessment and supersedes the old one, which stays in the history. A new rules version recalculates
every business. A second run over the same data changes nothing.

## 6. A person's review

- A review is an assessment with method `manual_review`, a mandatory reason (at most 2,000
  characters) in `review_decisions`, and the id of the calculation it reviewed. It **coexists** with
  the automatic calculation, which keeps being recalculated; the business shows the review.
- The evidence bounds it: an ownership status needs active ownership evidence, `verified` needs
  strong ownership evidence, market affinity needs active market evidence. `unknown` is always
  allowed.
- When the rules recalculate after a review, the review is flagged **outdated** (and filterable).
- Withdrawing a review (status null, with its reason) shows the calculation again. A record merged
  into another business cannot be reviewed: its business is.

## 7. Local AI

The runtime arrives in phase 10. Until then the store exposes `ingest_model_candidates`: a model may
propose `ownership_statement`, `ownership_mention`, `certification_statement` or `ownership_denied`
with the **literal quote** from an acquired page it rests on. A candidate becomes evidence (extractor
`latino.model`, linked to its inference run) only when the quote is in that page and the patterns read
the quote as the same kind of claim; otherwise it is rejected with `claim_not_supported`,
`quote_too_short`, `quote_not_found` or `not_a_declaration`. The model never adjudicates ownership,
never browses, and its output is only ever a candidate.

## 8. The job

`jobs.kind = latino_evidence`, one open run per project (`latino.start` with a null project starts one
per project with businesses).

| Stage     | How                                                                                                                 | Resume          |
| --------- | ------------------------------------------------------------------------------------------------------------------- | --------------- |
| extract   | the raw records behind the project's businesses and the crawled pages linked to them, in batches of 200, checkpoint | from the cursor |
| lifecycle | stale, revoked and contradicted evidence moved in batches of 500                                                    | idempotent      |
| evaluate  | canonical businesses in batches of 500, differences only, checkpoint; merged records lose their own assessment      | from the cursor |

A crawled page speaks for the business whose candidate asked for the crawl and for every business
whose website is on that site. Pause, resume and cancel go through the shared job control
(`jobs.BackgroundJobs`); a run a dead worker left running is paused at startup.

## 9. Interface and messages

- **Businesses**: a Latino evidence column (label, confidence, reviewed badge); filters by status,
  minimum confidence, kind of source that counted and review state (reviewed, not reviewed, outdated);
  the last evaluation and a button to run it.
- **Business inspector**: ownership and Spanish-speaking market shown on separate lines; the status
  with its code, confidence and whether a person or the rules decided; warnings; what it cannot
  conclude; the evidence grouped by track and ordered by strength and freshness, each with its status,
  whether it counted, strength, freshness, source reliability, source (link), field, extractor and
  date, and the fragment; the assessment history; review decisions; the review form.
- **Jobs**: evaluation runs next to the other engines, with pause, resume and cancel.

| Request          | Answer          | Command            |
| ---------------- | --------------- | ------------------ |
| `latino.start`   | `LatinoRunPage` | `start_latino`     |
| `latino.control` | `LatinoRun`     | `control_latino`   |
| `latino.runs`    | `LatinoRunPage` | `list_latino_runs` |
| `latino.get`     | `LatinoDetail`  | `get_latino`       |
| `latino.review`  | `LatinoDetail`  | `review_latino`    |

`businesses.list` gains `latino_status`, `latino_min_confidence`, `latino_source_kind` and
`latino_review`; its rows gain `latino_status`, `latino_confidence` and `latino_reviewed`. All
additive: `protocol_version` and `data_contract_version` stay 1. The core checks that a page obeys
its Latino filters, that a detail describes the business asked for with assessments of its canonical
business and decisions about it, that no ownership status arrives without ownership evidence nor
market affinity without market evidence, and that a review has a usable reason.

## 10. Examples (synthetic test data)

The development seed and the contract fixtures evaluate these situations with the real engines
(`testdata.seed_latino`):

| Situation                                                                                 | Result                                               |
| ----------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| A public registry records a Hispanic-owned business certification valid until 2030        | `verified_latino_owned`, confidence 0.86             |
| Its own website says "We are a proud Latina-owned family practice"                        | `high_confidence_latino_owned`, 0.72, self-declared  |
| A directory of Latino-owned businesses lists it                                           | `probable_latino_owned`, 0.48, self-declared         |
| Its website is in Spanish, "Se habla español", serves "la comunidad hispana"              | `spanish_market_affinity`, 0.48                      |
| Two locations in different states share a website saying "Proudly Hispanic-owned"         | `probable_latino_owned` for each, organizational     |
| A directory lists it as Hispanic-owned; a newer registry record says `hispanic_owned: no` | `unknown`, the listing `contradicted`, warning shown |
| Any name, surname, owner photo, neighbourhood or "ethnicity" column, alone or combined    | `unknown`                                            |

## 11. Limits

- Patterns read English and Spanish text; a declaration inside an image, a PDF or a video is not read.
- No live source provides ownership attributes or certifications yet; `verified` needs an official or
  registry source, so live data reaches at most `high_confidence` until such a connector exists.
- Freshness is measured from acquisition, not from when a page was written.
- Organizational evidence is inferred from shared websites only; a brand without a shared domain is
  treated as separate locations.
- A title that is itself a business name containing an ownership phrase is read as a declaration.
- Confidence is a transparent combination of documented factors, not a calibrated probability.
