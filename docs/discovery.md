# Discovery engine

How Lead Intelligence turns a campaign into candidate references, and how to add a source to it.
Delivered in phase 04; the engines that turn a candidate into a lead come later.

- Contract: [`discovery/sources/base.py`](../services/research-engine/src/research_engine/discovery/sources/base.py)
- Engine: [`discovery/engine.py`](../services/research-engine/src/research_engine/discovery/engine.py)
- Registry: [`discovery/registry.py`](../services/research-engine/src/research_engine/discovery/registry.py)
- Criteria and the shapes the UI sees: the discovery section of
  [`domain.py`](../services/research-engine/src/research_engine/domain.py)

## 1. What a candidate is, and is not

A **candidate** is a reference a source returned: a name, usually a place, sometimes a domain or a
phone, and the payload exactly as it arrived. It is not a business, not an entity and not a lead.

| It has                                              | It does not have                                 |
| --------------------------------------------------- | ------------------------------------------------ |
| A raw record it projects, immutable and hashed      | A canonical business id (phase 06 resolves that) |
| A run, a query and an acquired page behind it       | A score, an ICP verdict or a latino assessment   |
| A `dedup_key`: a cheap guess at "seen this already" | A merge: repeats are linked, never collapsed     |

The interface says so in words on every screen where candidates appear.

## 2. What the engine does not do

No CAPTCHA solving, no signing in without the operator's authorisation, no rotating identities to
get around a block, no private areas, no scraping a source whose terms forbid automation. A source
that forbids it declares `automation_allowed=False` and the registry keeps it disabled; a source
that needs a key declares `requires_credentials=True` and fails with `auth_required` until the
operator configures it. Nothing in this code invents a credential or an endpoint.

## 3. The shape of a run

```text
campaign (criteria_json, versioned DSL)
  └── job  kind="discovery"                          ← pause / resume / cancel act here
        ├── job_task  (one per source)   ── task_attempt (one per execution)
        └── source_run (one per source)
              └── source_query (one per page)
                    └── acquired_page  ── raw_record (immutable) ── discovery_candidate
        └── job_checkpoint (one per page: cursor + counters)  ← where a resume continues
        └── job_event (progress, limits, structured errors)
```

One page = one transaction. The query, the acquisition, every raw record, every candidate and the
checkpoint are written together, so a resumed run never asks a source for a page it already stored
and never skips one it did not.

## 4. The adapter contract

```python
class DiscoverySource(Protocol):
    metadata: SourceMetadata
    async def validate_config(self, config) -> list[str]: ...
    async def estimate(self, request, context) -> Estimate: ...
    async def discover(self, request, cursor, context) -> DiscoveryPage: ...
    def checkpoint(self, cursor) -> JsonObject: ...
```

Adapters are **stateless**: the engine owns the cursor, the transactions, the limits, the retries
and the provenance. An adapter answers "here is a page from this cursor, here is the next cursor".

`SourceMetadata` is the honest description the interface shows: kind, version, capabilities, terms
URL and summary, whether credentials are needed, whether automation is allowed, the default rate
limit, daily budget and page size, and the configuration fields the source needs.

`SourceContext` is what the engine lends for one call: the data directory, an HTTP client (or
`None` when the run may not reach the network) and the current time.

### Adding a source

1. Write a module in `discovery/sources/` that exposes `SOURCE`, an instance satisfying the
   protocol. Keep it small: parse, map, page, and nothing else.
2. Declare truthful `SourceMetadata`. Capabilities you do not honour must not be listed - the
   builder tells the operator which criteria a source will ignore, and it reads them from here.
3. Raise `DiscoveryError` with the right code (below). The retry policy reads the code.
4. Register it in `registry.SOURCES`. `sync_connectors` creates its `source_connectors` row on the
   next start; existing rows are updated, never deleted, because raw records point at them.
5. Add a test. If it talks to a network API, record a real response under `tests/recorded/` and
   replay it through an `httpx` transport, the way `test_discovery.py` does.

### Error codes and what the engine does with them

| Code                 | Retried | Means                                                     |
| -------------------- | ------- | --------------------------------------------------------- |
| `rate_limited`       | yes     | The source asked us to slow down (`Retry-After` honoured) |
| `network_error`      | yes     | Timeout, connection reset, unreachable host               |
| `source_unavailable` | yes     | The source is up but cannot answer right now              |
| `auth_required`      | no      | Credentials are missing or rejected                       |
| `blocked`            | no      | The source refused this client                            |
| `invalid_query`      | no      | The query, the file or the mapping is wrong               |
| `schema_changed`     | no      | The answer no longer has the shape the connector expects  |

Retries use exponential backoff with jitter, never shorter than the `Retry-After` the source asked
for, up to four attempts per page. A source that fails finally is marked failed; the other sources
of the same run keep going.

## 5. The criteria DSL

`campaigns.criteria_json` holds one `DiscoveryCriteria` document and `criteria_schema_version` says
which version it follows (currently 1).

| Field                                      | Meaning                                                                 |
| ------------------------------------------ | ----------------------------------------------------------------------- |
| `geography[]`                              | Country, state, metro, city, ZIP, radius - each source uses what it can |
| `verticals[]`                              | NAICS codes, two to six digits                                          |
| `categories[]`, `services[]`, `keywords[]` | Free terms, for sources that can search on them                         |
| `exclusions`                               | Names, domains, categories and ZIPs that never become candidates        |
| `market_languages[]`                       | A signal about the market served, **never** evidence of ownership       |
| `size_proxies[]`                           | Observable stands-in for size, when a source can provide one            |
| `freshness_since`                          | Only for sources that filter by date                                    |
| `strategy`                                 | `broad` (cast wide, filter later) or `narrow`                           |
| `sources[]`                                | Connector key, priority, record cap, per-campaign settings              |
| `limits`                                   | Candidates, request budget and pages per source                         |

`check_criteria` returns **errors** (a run cannot start) and **warnings** (it can, but the operator
should know): an unknown or unusable source, a duplicate selection, a criterion a source will
ignore, no geography at all, a narrow strategy with nothing to narrow by, and a reminder that market
language is a signal. A campaign with errors is still saved as a draft.

Estimates come from the sources themselves, are always labelled as estimates, and are never a
promise of how many candidates a run will find.

## 6. Limits, rate and budget

| Limit                         | Where it comes from                             | What happens                                     |
| ----------------------------- | ----------------------------------------------- | ------------------------------------------------ |
| `limits.max_candidates`       | Campaign                                        | The run stops, with a `limit_reached` event      |
| `limits.max_requests`         | Campaign                                        | Same, before the next source starts              |
| `limits.max_pages_per_source` | Campaign                                        | That source stops                                |
| `selection.max_records`       | Campaign, per source                            | That source stops                                |
| Requests per minute           | Connector default or `source_connector_configs` | Paced with jitter between pages                  |
| Daily request budget          | Same                                            | Counted from `source_queries` since midnight UTC |

Sources run one at a time, in priority order, one page at a time. The ceiling is deliberate: a
desktop app querying public APIs should be gentle by default.

## 7. Pause, resume, cancel and restart

Control is written to the store first, so it survives a restart, and observed cooperatively between
pages (and during a rate-limit wait, which is interruptible).

- **Pause**: the page in flight is finished and stored, then the run stops. `source_runs` go to
  `paused` with their cursor in the last checkpoint.
- **Resume**: each source starts a new attempt from its checkpoint cursor.
- **Cancel**: the run and its sources go to `cancelled`. Whatever was already found is kept.
- **Restart of the app**: a job left `running` belonged to a worker that is gone. At startup it is
  moved to `paused` with a `run_interrupted` event - never silently restarted, because that would
  mean unannounced network activity. The operator resumes it.

A source is only recorded as `succeeded` when it actually finished: exhausted or capped by a limit.
Anything else - a pause, a cancellation, the worker stopping - leaves it resumable.

## 8. The sources this build ships

| Key                      | Kind         | Needs a key | Notes                                                                                              |
| ------------------------ | ------------ | ----------- | -------------------------------------------------------------------------------------------------- |
| `file.import`            | file import  | no          | CSV, TSV, JSON, JSONL supplied by the operator; column mapping + preview                           |
| `manual.seeds`           | manual seeds | no          | URLs, domains or names, one per line                                                               |
| `usaspending.recipients` | official API | no          | US federal award recipients: public domain, paginated, filters by state, city, ZIP, NAICS and date |
| `directory.api_template` | directory    | yes         | A template, disabled on purpose: it shows what a keyed connector declares and refuses to run       |

XLSX import is **not** implemented: it is a zip of XML, not text, and nothing here would parse it
honestly today. The interface says so and asks for a CSV export.

An imported file is written once under `imports/<source run>/` in the data directory and recorded as
an `acquired_page`, so a candidate can be traced to the exact bytes it came from and a paused import
resumes by row offset.

## 9. Duplicate detection

`dedup_key` is the connector's cheap identity guess: a stable id from the source if there is one,
otherwise a domain, otherwise the tidied name plus the strongest place signal. A candidate whose key
was already seen **in the same campaign** points at the first one through `duplicate_of_id`.

Nothing is merged, nothing is discarded, and keys never cross campaigns: two campaigns that find the
same business keep their own candidate, with their own lineage. Real entity resolution is phase 06.

## 10. Messages the interface uses

| Request                  | Answer             | Command               |
| ------------------------ | ------------------ | --------------------- |
| `sources.list`           | `SourceCatalog`    | `list_sources`        |
| `sources.preview_import` | `ImportPreview`    | `preview_import`      |
| `campaigns.list`         | `CampaignPage`     | `list_campaigns`      |
| `campaigns.save`         | `CampaignSaved`    | `save_campaign`       |
| `campaigns.validate`     | `CriteriaCheck`    | `validate_criteria`   |
| `discovery.start`        | `DiscoveryRun`     | `start_discovery`     |
| `discovery.control`      | `DiscoveryRun`     | `control_discovery`   |
| `discovery.runs`         | `DiscoveryRunPage` | `list_discovery_runs` |
| `discovery.candidates`   | `CandidatePage`    | `list_candidates`     |
| `discovery.candidate`    | `CandidateDetail`  | `get_candidate`       |

All of them are additive: `protocol_version` stays 1.

## 11. Offline mode

`LEAD_DISCOVERY_OFFLINE=1` makes the worker run every source without an HTTP client. Sources that
need the network fail with `source_unavailable` instead of reaching out; file imports and seeds keep
working. The test suite runs this way by default.
