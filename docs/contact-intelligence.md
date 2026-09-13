# Contact intelligence

How Lead Intelligence finds, consolidates, validates and prioritises the contact channels a business
publishes or the operator imports: where and when each one was seen, what every check actually
proved, the people a business publishes, a contactability subscore and a local do-not-contact list.
Delivered in phase 09.

Nothing here calls a number, sends a message or an email, talks to a mail server, submits a form,
buys data or reads leaked data. The only network access is an optional DNS question about a mail
domain.

- Rules (pure, versioned): [`contacts/rules.py`](../services/research-engine/src/research_engine/contacts/rules.py)
- Store (extraction, channels, people, suppressions, reads, runs): [`contacts/store.py`](../services/research-engine/src/research_engine/contacts/store.py)
- Mail-domain lookups: [`contacts/mail_dns.py`](../services/research-engine/src/research_engine/contacts/mail_dns.py)
- Job: [`contacts/engine.py`](../services/research-engine/src/research_engine/contacts/engine.py)
- Schema: [`migrations/sqlite/0008_contact_intelligence.sql`](../services/research-engine/src/research_engine/migrations/sqlite/0008_contact_intelligence.sql)
- Decisions: D-058 to D-063 in [decisions.md](decisions.md)

## 1. What is read

| Input                   | Where it comes from                                                                                                                                          |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Channels from sources   | `contact_points` of every record of a canonical business, written by entity resolution (phase 06) from source payloads and crawled pages                     |
| Crawled pages           | Raw records of `web.crawler` pages that speak for a business: the business whose candidate asked for the crawl and every business whose website is that site |
| Forms of a page         | The stored HTML read again with the website extractor (phase 08): purpose, visible fields, method, captcha, insecure action                                  |
| HTTP status of a page   | `crawl_urls.http_status`                                                                                                                                     |
| Country of a number     | An explicit `+` code, or the business's locations when they agree on one country; never guessed                                                              |
| Mail domains            | `mail_domain_checks`, the cache of DNS answers (section 3)                                                                                                   |
| A person's decisions    | `review_decisions` on the business (channels) and on each published person                                                                                   |
| The do-not-contact list | Live `contact_suppressions`                                                                                                                                  |

Observations carry their provenance (extraction, raw record, source run, connector, URL, date) and
are never edited: only their status, method, suppression flags and primary flag move.

### Extraction from crawled pages (`contacts.site`, `EXTRACTOR_VERSION = 1.0.0`)

| Channel        | Read from (confidence)                                                                                                        |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Phone          | `tel:` links (0.8), page text (0.6), `telephone` of LocalBusiness, Organization, ContactPoint or Person structured data (0.8) |
| Email          | `mailto:` links (0.8), page text (0.6), `email` of the same structured data (0.8)                                             |
| Contact form   | A form whose purpose is contact or quote (0.7); login, search and newsletter forms are not channels                           |
| Booking form   | A booking form, or a link to a booking tool the website signatures know (Calendly, Acuity, Square, Jobber…) (0.7)             |
| Social profile | A link to a profile with a handle (0.7), `sameAs` of structured data (0.8)                                                    |

- A ContactPoint's `contactType` is its department (`sales`); the local part of a role address
  (`info@`, `sales@`) is its role.
- A structured-data block whose postal code (and street number) repeats a location of the business
  ties its channels to that location.
- Idempotent: one extraction per page and extractor version, one observation per channel,
  extraction and business, one person per name. Running again writes nothing new.

## 2. Channels

A **channel** is every live observation of the same value within a canonical business (its root and
every record merged into it), whatever the source. Its key:

| Type                  | Key                                                                           |
| --------------------- | ----------------------------------------------------------------------------- |
| Phone                 | `phone:+15125550171` (E.164)                                                  |
| Email                 | `email:info@example.com` (case folded)                                        |
| Social profile        | `social:facebook:handle`                                                      |
| Contact, booking form | `contact_form:<url>`, `booking_form:<url>`                                    |
| Not normalisable      | `<type>:raw:<text>`: a malformed value still shows, with its `invalid` status |

| Field                          | Meaning                                                                                                                                                           |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `level`                        | `person` when tied to a published person, `location` when tied to a location, else `business`                                                                     |
| `role`                         | `personal`, `department` (a published department), `role_address` (`info@`, `sales@`) or `general`                                                                |
| `origin`                       | `published` (a source or page shows it), `user_import` (the operator imported it) or `hypothesis` (section 5)                                                     |
| `validation_status`, `_method` | Section 3, with the method that decided it                                                                                                                        |
| `signals`                      | What the checks read (below)                                                                                                                                      |
| `freshness`                    | From the newest observation (below)                                                                                                                               |
| `sources`                      | Every observation: raw value, context (`mailto link on …`), confidence, date, provenance and `current` (false when a newer crawl of that page no longer shows it) |
| `is_primary`                   | Phones and emails follow the business's canonical value (phase 06); forms and profiles their own flag                                                             |
| `shared_with`                  | How many other canonical businesses of the project publish the same value                                                                                         |
| `suppression`, `review`        | The do-not-contact entry that covers it, and the latest decision of a person                                                                                      |

Statuses are decided again on every read from what is stored (cached mail answers only), so a
suppression, a merge or a decision shows at once; runs and actions also write them to every
observation with a `contact_validations` row.

**Signals.** Phones: `country`, `extension`, `line_type` (`not_determinable` for North American
numbers, which the numbering plan cannot tell apart), `fictional_range` (555-0100 to 0199),
`not_in_numbering_plan`, `not_possible`, `unparseable`, `no_country_context`. Emails: `syntax`,
`role_address`, `free_provider`, `disposable`, `idn`, `domain_matches_website`, `catch_all` (always
`not_checked`), `mail_domain` (the DNS answer or `not_checked`). Forms: `form_fields`, `form_method`,
`captcha`, `form_purpose`, `page_status`, `provider`, `no_fields`, `insecure_action`. Profiles:
`platform`.

**Freshness**, from the newest observation:

| Type                         | Fresh      | Aging      | Stale      |
| ---------------------------- | ---------- | ---------- | ---------- |
| Phone, email, social profile | ≤ 365 days | ≤ 730 days | > 730 days |
| Contact and booking forms    | ≤ 180 days | ≤ 365 days | > 365 days |

**Conflicts** are shown, never resolved silently:

| Code                    | When                                                                                                 |
| ----------------------- | ---------------------------------------------------------------------------------------------------- |
| `values_disagree`       | The records of the business hold more than one phone (or email) and more than one value is published |
| `shared_value`          | Another canonical business of the project publishes the same value                                   |
| `person_roles_disagree` | A published name appears with different roles                                                        |

## 3. What each status means — `RULES_VERSION = 1.0.0`

| Status               | Means exactly                                                                                                                                                                                                                                                                                                                                                               | Does not mean                                               | Method                                                              |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------- |
| `observed`           | Seen in a source or on a page; no check has decided it yet (a form without stored facts, an observation just released from the do-not-contact list)                                                                                                                                                                                                                         | that it works                                               | `form_structure`, `suppression_lifted`                              |
| `structurally_valid` | **Well formed only**: a number the numbering plan accepts for its country, an address with valid syntax (its domain not checked, or the lookup timed out), a form with fields on a page that answered 2xx, a booking link, a profile URL with a handle                                                                                                                      | that it rings, the mailbox exists, or anyone reads the form | `numbering_plan`, `structure`, `mail_domain`, `form_structure`      |
| `domain_valid`       | The address is well formed **and its domain answered DNS that it accepts mail** (MX or implicit MX)                                                                                                                                                                                                                                                                         | that this mailbox exists; catch-all is never checked        | `mail_domain`                                                       |
| `likely_valid`       | **Independent evidence agrees**: a well-formed phone published by two kinds of source and outside the fictional range; an address whose domain accepts mail, not disposable, on the business's website domain or published by two kinds of source; a form, booking link or profile on the business's own crawled site, without an insecure action; or a person confirmed it | a guarantee that someone answers                            | `sources_agree`, `form_structure`, `official_link`, `manual_review` |
| `invalid`            | A check refuses it (not a possible number, outside the numbering plan, bad syntax, a domain that does not exist or accepts no mail, a form without fields or on a page that failed) or a person marked it invalid                                                                                                                                                           | —                                                           | the check's method, `manual_review`                                 |
| `unknown`            | It cannot be checked: a national number with no country; every hypothesis                                                                                                                                                                                                                                                                                                   | that it is invalid                                          | `numbering_plan`, `pattern_hypothesis`                              |
| `stale`              | Last seen longer ago than its type allows, or every crawled page that showed it was crawled again without it                                                                                                                                                                                                                                                                | that it stopped working                                     | `freshness`, `pattern_hypothesis`                                   |
| `suppressed`         | A live do-not-contact entry covers it                                                                                                                                                                                                                                                                                                                                       | —                                                           | `suppression_list`                                                  |

**Order, most restrictive first:** suppressed → a person's `invalid` → a check's `invalid` →
hypothesis (`unknown`, or `stale`) → `stale` → a person's confirmation (`likely_valid`) → independent
evidence (`likely_valid`) → the check's own result. A confirmation never lifts what a check refuses,
and a hypothesis is never `suppressed` (it is flagged instead), `likely_valid` or primary.

### Mail domains

One DNS question per domain: MX, then A/AAAA when there is no MX. Never an SMTP conversation.

| Answer        | Meaning                                                | An address becomes                                   | Cached |
| ------------- | ------------------------------------------------------ | ---------------------------------------------------- | ------ |
| `mx`          | The domain names mail exchanges                        | `domain_valid`                                       | 7 days |
| `implicit_mx` | No MX, but an address record (RFC 5321 fallback)       | `domain_valid`                                       | 7 days |
| `null_mx`     | The domain declares that it accepts no mail (RFC 7505) | `invalid`                                            | 1 day  |
| `no_mail`     | Neither MX nor address records                         | `invalid`                                            | 1 day  |
| `nxdomain`    | The domain does not exist                              | `invalid`                                            | 1 day  |
| `timeout`     | No answer within 3 s                                   | stays `structurally_valid`; never replaces an answer | 1 hour |
| `error`       | The resolver failed                                    | stays `structurally_valid`; never replaces an answer | 1 hour |

A run looks domains up only when **Look up mail domains (DNS)** is ticked and the worker is not
offline (`LEAD_DISCOVERY_OFFLINE=1`), at most 1 000 per run, and only for missing or expired
answers; **Check again** on one address with the lookup ticked asks again even if cached. Reads and
the export query never touch the network.

## 4. Published people

A decision maker exists only when a page or source publishes a name **next to a role**:

- `Name, Role`, `Role: Name` (also with `|`, `-`, `–`, `—`), `founded / started / established /
owned / run / operated by Name` and their Spanish forms, in headings and main text; structured-data
  `Person` items, and people under `founder`, `employee` or `member`.
- Roles and the relationship they state: owner (owner, co-owner, owner/operator, proprietor,
  dueño/a, propietario/a), founder (founder, co-founder, fundador/a), executive (CEO, COO, CFO,
  president, vice president, principal), partner (managing partner, partner, socio/a), manager
  (general, office or operations manager, manager, director/a, gerente, administrador/a). A
  structured-data person whose published title is none of these is not a decision maker.
- A name is two to four capitalised words beside the role; words such as `Team`, `Services`, `Inc`
  or the role itself never count. At most 20 people per page. Confidence: 0.6 for a role phrase, 0.5
  for "founded by", 0.8 for structured data.
- **Nothing is inferred from a name**: not a role, not an origin, not an identity. Names never feed
  Latino evidence or any score.

**Review queue.** A person with a doubt waits for a decision (`ambiguous`):

| Reason                  | Set by the page or the store                               |
| ----------------------- | ---------------------------------------------------------- |
| `historical_role`       | "Founded by": a founding role may be historical            |
| `no_role`               | A structured-data person with no role                      |
| `conflicting_roles`     | The same name appears with different roles in the business |
| `shared_site`           | The page speaks for several canonical businesses           |
| `name_matches_business` | The name is part of the business's own name                |

A person confirms, rejects or resets (back to what the rules say) with a reason; the decision covers
every observation of that name in the business and a run never overrides it. Rejecting a person
withdraws the hypotheses built for them, and rejected people count nowhere.

## 5. Hypotheses

When a published **personal** address on the business's own website domain (not a role address, not
a free provider) follows a pattern of a published person's name (`first.last`, `first_last`,
`firstlast`, `f.last`, `flast`, `firstl`; a first name alone is never used), the most frequent pattern
builds an address for every other published person without one. It is stored as a `hypothesis`
(`contacts.pattern` extraction, confidence 0.2, context `hypothesis: built with the pattern … not
confirmed`):

- status `unknown` (or `stale`), never primary — both enforced by database triggers;
- never confirmed, never shown as primary, never counted in contactability;
- listed apart in the interface, left out of contact lists and exports unless asked for;
- withdrawn when the business publishes the address itself or the person is rejected.

## 6. Contactability — `CONTACTABILITY_VERSION = 1.0.0`

How reachable a business is through what it publishes. It is **not** the lead score (phase 11).

```text
score = round(100 × Σ weight × value / Σ weight)
        over availability 25 · validation 30 · role 15 · freshness 15 · source 15
```

| Component    | Value                                                                                                                                                                     |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| availability | Sum over the kinds of channel present, at most 1: phone 0.4, email 0.35, form 0.15, social profile 0.1                                                                    |
| validation   | Best status among phones and emails (any channel when there are none): likely valid 1, domain valid 0.8, well formed 0.6, observed 0.4, unknown 0.3, stale 0.1            |
| role         | Best channel: a person who is owner, founder, executive or partner 1; another person, a department or a personal address 0.7; general 0.6; role address 0.5; profile 0.2  |
| freshness    | Best channel: fresh 1, aging 0.5, stale 0                                                                                                                                 |
| source       | Best source kind: official API, public registry, business website 1; directory, public dataset, platform API 0.8; file import, manual seed 0.7; web search 0.6; other 0.5 |

Only published or imported channels that are not invalid, suppressed or in the fictional range
count; hypotheses never do. Gates: `business_suppressed` (the business is on the list: no score) and
`no_usable_channel`. Every component shows the value it read and why.

## 7. The do-not-contact list

| Kind     | Stored as                                    | Covers                                                  |
| -------- | -------------------------------------------- | ------------------------------------------------------- |
| email    | the lower-case address                       | that address                                            |
| phone    | E.164 (a national number is read as US)      | that number                                             |
| domain   | the registrable domain (of a URL or address) | every address and form on that domain                   |
| business | the business id                              | every channel of the canonical business and its records |

Every entry has a reason, a source (`manual`, `import:<file>`), an actor and a date. Entries are
**permanent**: one is lifted once, with who and why, and never deleted (database triggers).

It is applied three times: (1) as soon as it changes, to every observation it covers (flag, status
`suppressed`, history); (2) by the queries — the business list never shows a suppressed phone, and
every contact read and export query checks live entries again; (3) by the desktop core, which refuses
a channel whose status and suppression disagree. Lifting an entry returns what it alone covered to
`observed`; the next check decides it again.

**Import:** a CSV with a header naming `kind` and `value` (`reason` optional), at most 5 000 rows
(1 000 000 characters in the core). Each unusable row is reported by line: `missing_columns`,
`unknown_kind`, `invalid_value`, `too_many_rows`; values already listed are counted, not duplicated.
**Export:** `kind,value,reason,source,created_at,lifted_at`, lifted entries only when asked for. A
cell that starts with `=`, `+`, `-`, `@`, a tab or a carriage return gets a leading `'` so a
spreadsheet never runs it, and the core checks every cell again.

## 8. Actions on a channel

| Action          | Reason   | Effect                                                                                                   | Refused when                          |
| --------------- | -------- | -------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| Check again     | optional | Decides it now; with the lookup ticked, an email's domain is asked again                                 | —                                     |
| Confirm         | required | `contact_confirmed`: `likely_valid` by `manual_review`, unless it is suppressed or stale                 | a hypothesis; checks refuse the value |
| Mark invalid    | required | `contact_invalid`: `invalid` by `manual_review`                                                          | —                                     |
| Clear review    | required | `contact_review_cleared`: back to what the checks say                                                    | there is no review                    |
| Show as primary | optional | Phones and emails: the business's canonical value (phase 06, with its history); forms and profiles: flag | a hypothesis; invalid or suppressed   |
| Do not contact  | required | A manual do-not-contact entry for the number or address                                                  | not a phone or email; already listed  |

Actions apply to the canonical business (a merged record is refused), are written at once and leave
their history in `review_decisions`, `contact_validations` and `audit_log`.

## 9. Runs

`jobs.kind = contact_intelligence`, one job per project (or every live project). Two stages:

1. **extract** — crawled pages in raw-record order, 100 per transaction and checkpoint;
2. **validate** — canonical businesses, 200 per batch: mail domains, people to review, hypotheses,
   statuses written with history, checkpoint.

Pause, cancel and a worker restart resume where the checkpoint says; a run a dead worker left running
is paused at startup (D-027). A second run over the same data writes no new observation. Counts:
pages scanned, contacts, forms and people found, hypotheses, businesses, channels, statuses changed,
mail domains checked and failed, likely valid, domain valid, well formed, invalid, stale, suppressed
and ambiguous people.

| Message                  | Payload                      | Answer                    |
| ------------------------ | ---------------------------- | ------------------------- |
| `contacts.start`         | `ContactStartRequest`        | `ContactRunPage`          |
| `contacts.control`       | `DiscoveryControlRequest`    | `ContactRun`              |
| `contacts.runs`          | `ContactRunListQuery`        | `ContactRunPage`          |
| `contacts.list`          | `ContactListQuery`           | `ContactPage`             |
| `contacts.get`           | `ContactDetailQuery`         | `ContactDetail`           |
| `contacts.act`           | `ContactActionRequest`       | `ContactDetail`           |
| `decision_makers.list`   | `DecisionMakerListQuery`     | `DecisionMakerPage`       |
| `decision_makers.review` | `DecisionMakerReviewRequest` | `DecisionMakerItem`       |
| `suppressions.list`      | `SuppressionListQuery`       | `SuppressionPage`         |
| `suppressions.change`    | `SuppressionChangeRequest`   | `ContactSuppression`      |
| `suppressions.import`    | `SuppressionImportRequest`   | `SuppressionImportResult` |
| `suppressions.export`    | `SuppressionExportRequest`   | `SuppressionExport`       |

The desktop core validates every answer before the interface sees it: a hypothesis never among the
channels nor reading as validated or primary, status and suppression in agreement, a contactability
score that adds up its components (or names its gate), list pages inside their filters, a
suppression entry consistent with its kind, and an export that is a CSV with the expected header,
rows and no cell a spreadsheet would run.

## 10. Interface

- **Research Center ▸ Contacts.** _Channels_: every channel with its status (in words and code),
  role, freshness, sources and last seen, filtered by channel type, status, role and freshness, with
  hypotheses and do-not-contact entries only when asked for; selecting one opens its business.
  _People to review_: ambiguous published people with their reasons, context and source, confirmed or
  rejected with a reason. _Do not contact_: the list with add, lift, import and export. The latest run
  shows its progress and controls; **Check contacts** starts one, with or without DNS lookups.
- **Business inspector ▸ Contact channels.** Contactability with its components and formula,
  conflicts, channels by type with status, method, sources, dates, signals and suppression,
  hypotheses apart under a notice, published people, decisions and the actions of section 8.
- **Jobs.** Contact runs next to the other engines, with cancel.

## 11. Limits

- A `likely_valid` channel may still not answer: no check here reaches a person.
- Catch-all domains are never detected; `domain_valid` never proves a mailbox.
- North American numbers carry no line type; a national number is `unknown` without a country.
- People and role phrases are read in English and Spanish only; unusual layouts are missed and some
  names will be wrong, which is what the review queue is for.
- The disposable-provider list is short (30 well-known providers).
- A pattern hypothesis uses the single most frequent pattern on the business's own domain.
- Contact lists decide every channel of a project per request; very large projects will want
  materialised channels.
