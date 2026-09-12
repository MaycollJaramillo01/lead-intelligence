# Web acquisition (crawling)

How Lead Intelligence turns candidates and website addresses into acquired pages and parsed
content, within limits that keep it a polite, safe visitor. Delivered in phase 05; the engines that
read this content (evidence, website audit, contacts) come later.

- Engine: [`crawl/engine.py`](../services/research-engine/src/research_engine/crawl/engine.py)
- HTTP level: [`crawl/fetch.py`](../services/research-engine/src/research_engine/crawl/fetch.py) ·
  SSRF guard: [`crawl/guard.py`](../services/research-engine/src/research_engine/crawl/guard.py)
- Browser level: [`crawl/browser.py`](../services/research-engine/src/research_engine/crawl/browser.py)
- Rules: [`crawl/urls.py`](../services/research-engine/src/research_engine/crawl/urls.py),
  [`crawl/robots.py`](../services/research-engine/src/research_engine/crawl/robots.py) ·
  parsing: [`crawl/extract.py`](../services/research-engine/src/research_engine/crawl/extract.py)
- Contract: the web acquisition section of
  [`domain.py`](../services/research-engine/src/research_engine/domain.py) and migration
  `0004_web_acquisition.sql`

## 1. What it does, and what it never does

A crawl fetches a small, prioritised set of pages of each site and stores them with everything
needed to check them later: the bytes, how they were obtained, and the raw findings parsed from
them (title, metadata, headings, links, visible emails and phones, JSON-LD, main text). It does not
decide whether a business is Latino-owned, a good fit or worth contacting.

| Never                                                            | How that is enforced                                                                        |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Solve or bypass a CAPTCHA, a WAF challenge, a paywall or a login | A challenge page stops the site with `blocked`; it is never rendered "to get past it"       |
| Enter private areas                                              | Login, account, cart, checkout and admin paths are skipped (`private_area`)                 |
| Ignore robots.txt                                                | robots.txt is the first request of every site and is not configurable (RFC 9309)            |
| Hide who it is                                                   | Every request says `LeadIntelligence/<version> (local desktop research tool)`               |
| Reach this machine, the local network or cloud metadata          | SSRF guard at connect time; only public addresses (section 5)                               |
| Hammer a host                                                    | Per-host concurrency, spacing, `Crawl-delay`, `Retry-After` and cool-down after 429/5xx     |
| Download or open files                                           | MIME check before reading, byte sniffing, bodies stored as content-addressed artifacts only |

## 2. Two levels

```text
Level 1  HTTP        httpx + selectolax          every URL
Level 2  Browser     Playwright + Chromium        only when a verifiable criterion says so
```

### When the browser is used (`CrawlSettings.browser`)

| Mode     | Rule                                                                                       |
| -------- | ------------------------------------------------------------------------------------------ |
| `never`  | Never.                                                                                     |
| `always` | Every page that answered over HTTP (`fallback_reason = configured`).                       |
| `auto`   | Only for one of the criteria below, measured on the HTTP page and stored in its `signals`. |

| Criterion (`fallback_reason`) | Measured as                                                                                                                            |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `empty_shell`                 | Scripts on the page and fewer than 30 visible characters, or an empty app mount point (`#root`, `#app`, `#__next`…) and fewer than 200 |
| `js_required`                 | The page or its `<noscript>` says JavaScript is required                                                                               |
| `http_functional_block`       | HTTP 406, or the page says the browser is not supported                                                                                |

A CAPTCHA or challenge page is **never** a criterion: it stops the site. The rendered page gets its
own `acquired_page` (the rendered HTML), the raw record carries a `fallback` block with the reason,
the browser version, the requests blocked and the HTTP page's signals and body hash, and the URL row
keeps `browser_ms` as the cost. If the browser is not available or the render fails, the HTTP content
is kept and `reason` says `browser_unavailable` or `browser_failed`. A render that navigates to
another site is not stored as this site's page (`redirect_out_of_scope`).

### The browser, safely

- One Chromium process is launched on first need and reused; each render gets a fresh context, so
  cookies and storage never travel between sites. It is closed when no crawl is running.
- Images, media, fonts, stylesheets and sockets are aborted; every other request must be http(s) to
  a public address. Downloads are refused and service workers blocked.
- Waiting is signal-based: DOM ready (15 s cap), network idle (5 s cap), visible text (3 s cap),
  30 s overall. No fixed sleeps.
- Contexts close in `finally`, also on cancel. On Windows the worker enters a kill-on-close job object
  before the first launch, so Chromium and the Playwright driver die with the worker however it ends.
- Which browser: Playwright's own Chromium when installed (`playwright install chromium`), otherwise
  the Chromium-based Edge or Chrome already on the machine.

## 3. The HTTP pipeline

| Step          | Behaviour                                                                                                                                                                                                           |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Canonical URL | http(s) only, IDNA host, lower case, no default port, no credentials, no fragment, dot segments resolved, tracking parameters dropped, the rest sorted                                                              |
| Safety        | Host resolved and every address checked before connecting; the connection goes to the checked address (section 5)                                                                                                   |
| Redirects     | Followed by hand, at most 5; each hop recorded in `redirects_json`; loops, unusable `Location`, other sites and robots-disallowed targets stop the URL                                                              |
| Timeouts      | connect 5 s, read 15 s, write 15 s, pool 30 s, and 30 s per attempt overall                                                                                                                                         |
| Retries       | Only timeouts, resets, 408, 429 and 5xx; 3 attempts; backoff `1 s × 2ⁿ` with jitter, never shorter than `Retry-After` (seconds or HTTP date); a wait over 60 s fails the URL                                        |
| Conditional   | `If-None-Match` / `If-Modified-Since` from the newest plain HTTP fetch of the same URL in the project; a 304 reuses its parsed record (`not_modified`)                                                              |
| Decompression | gzip and deflate; the byte limit is counted after decompression; gzipped sitemaps are inflated with a bounded buffer                                                                                                |
| Type          | Declared type checked before reading; the first bytes sniffed (PDF, ZIP, EXE, images… are refused even when served as HTML)                                                                                         |
| Charset       | BOM, then the HTTP header, then `<meta>`, then UTF-8, then windows-1252 (latin-1 labels map to 1252 as browsers do)                                                                                                 |
| Parsing       | selectolax (lexbor); XML sitemaps too, so no XML entity is ever expanded                                                                                                                                            |
| Hashes        | `body_sha256` of the stored bytes; `content_sha256` of the visible text with whitespace normalised; a page whose content hash was already seen in the site is linked through `duplicate_of_id` and not stored again |
| Stored        | Status, final URL, allowed headers, timings, request and retry counts, and provenance (`acquired_pages` → `raw_records`)                                                                                            |

Headers kept: `age`, `cache-control`, `content-encoding`, `content-language`, `content-length`,
`content-type`, `date`, `etag`, `expires`, `last-modified`, `location`, `retry-after`, `server`, `vary`,
`x-robots-tag`. Cookies and anything else are not stored.

## 4. Crawl strategy

Per site, in priority order:

```text
robots.txt                          1000   always first; nothing else is requested before its rules
sitemaps (declared or /sitemap.xml)  900   at most 4 per site, 200 URLs each; other sites' are ignored
home page                            100
contact · about · services      90 · 80 · 70   minus 10 per level of depth
anything else                         20   minus 10 per level of depth
```

Page kinds are recognised from the path and the link text, in English and Spanish (`contacto`,
`quienes-somos`, `servicios`…). Links are followed only inside the site (`www.` is the same site;
subdomains only when `include_subdomains`), never with `rel=nofollow`, a `nofollow` meta robots tag
or `X-Robots-Tag`, and never beyond `max_depth`. Every URL the crawler decided about is on record,
up to 400 per site.

| Skipped as        | When                                                                                                                                                                       |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `robots`          | robots.txt disallows it (longest match wins, `Allow` wins a tie, our product token's group replaces `*`)                                                                   |
| `private_area`    | login, logout, sign-up, account, cart, checkout, basket, wishlist, admin, dashboard, password paths                                                                        |
| `trap`            | calendars, dated archives, deep pagination, feeds, search, tags, more than 3 query parameters, facet/sort/session parameters, 8+ path segments, a segment repeated 3 times |
| `binary_resource` | PDF, archives, executables, images, audio, video, fonts, CSS/JS/JSON/XML and office files                                                                                  |
| `excluded`        | matches an `exclude_paths` glob                                                                                                                                            |
| `not_included`    | `include_paths` is set and none matches (the home page and robots.txt always go)                                                                                           |
| `*_budget`        | a limit was reached; the rest of the site is skipped in one write and a `limit_reached` event says which                                                                   |

robots.txt outcomes follow RFC 9309: rules when it is read; no rules when it answers 4xx; when it
answers 5xx or 429 or cannot be reached, nothing is allowed and the site stops with
`robots_unreachable`. `Crawl-delay` is honoured up to 30 s.

## 5. SSRF protection

A URL from a web page is untrusted. Before connecting, the host is resolved and **every** address
must be public: loopback, private (10/8, 172.16/12, 192.168/16, fc00::/7), link-local (169.254/16,
where cloud metadata lives), shared (100.64/10), reserved, multicast and unspecified ranges are
refused, IPv4-mapped IPv6 included, and so are `localhost`, `*.localhost`, `*.local`, `*.internal`,
`*.lan`, `*.home.arpa` and `metadata.google.internal`. The check runs inside the HTTP client's
connect step and the socket connects to the address that passed it, so a DNS answer that changes
between check and connect cannot slip through; TLS still verifies the certificate for the original
name. Environment proxies are ignored. The browser applies the same check to every request it makes.

`allow_private` exists for the test suite's local fixture server only; the worker never sets it.

## 6. Limits and configuration

`CrawlSettings` travels with the start request, is validated by the worker and again by the desktop
core, and is stored with the job and each site run.

| Setting                          | Default | Allowed           | Meaning                                                          |
| -------------------------------- | ------- | ----------------- | ---------------------------------------------------------------- |
| `max_sites`                      | 100     | 1–500             | Sites taken from the campaign and the addresses                  |
| `max_pages_per_site`             | 12      | 1–100             | Page fetches per site (robots and sitemaps not counted)          |
| `max_depth`                      | 2       | 0–4               | Link depth from the home page                                    |
| `max_bytes_per_site`             | 25 MB   | 64 KiB–100 MB     | Stored bytes per site                                            |
| `max_seconds_per_site`           | 180     | 10–1800           | Wall time of a site in one execution                             |
| `max_requests`                   | 2000    | 1–20 000          | HTTP requests of the whole crawl, retries and redirects included |
| `requests_per_minute_per_host`   | 30      | 1–120             | Spacing between request starts to one host                       |
| `per_host_concurrency`           | 2       | 1–4               | Requests in flight to one host                                   |
| `include_subdomains`             | no      |                   | Follow links to subdomains of the site                           |
| `include_paths`, `exclude_paths` | none    | 25 globs each     | Path filters, e.g. `/blog/*`                                     |
| `browser`                        | `auto`  | never/auto/always | Section 2                                                        |
| `screenshots`                    | no      |                   | Keep a PNG of rendered pages (for later modules)                 |

Fixed safe limits: 5 MiB per page, 2 MiB per robots.txt (500 KiB parsed), 10 MiB per sitemap
(after decompression), 5 redirects, 3 attempts, 20 main-text kilobytes, 300 links, 25 emails and 25
phones per page.

**Slots.** HTTP slots = `min(16, 2 × CPUs)`; browser slots = up to 2, one per free GiB above 2 GiB and
at most one per 4 CPUs (none when memory is short: the fallback then reports `browser_unavailable`).
`HTTP slots ÷ per_host_concurrency` sites run at once. The request budget is reserved before each
request, so parallel workers overshoot it by at most the requests in flight.

## 7. Jobs, pause, resume and restart

```text
job (crawl)                                     ← pause / resume / cancel act here
 ├── job_task + task_attempt (one per site)
 └── source_run (one per site, connector web.crawler)
       └── crawl_urls ──► acquired_pages ──► raw_records
 └── job_event (site finished/failed, limit reached, site refused, run outcome)
```

- One URL = one transaction: its acquired pages, its raw record, its row and the URLs it discovered.
  `UNIQUE (source_run_id, url)` and the final-state trigger make "never fetch or store twice" a
  property of the database.
- **Pause** and **cancel** stop at once: requests in flight are abandoned, their rows stay queued,
  browser contexts close. A paused crawl resumes from the queued rows; the URLs that were in flight
  are fetched again.
- A crawl a dead worker left `running` is paused at startup with `run_interrupted`, never resumed
  silently (D-027).
- A site succeeds when at least one page was fetched; otherwise it fails with the reason that stopped
  it (`unsafe_target`, `dns_error`, `blocked`, `robots_unreachable`, or its home page's error).

## 8. Metrics

Read from `crawl_urls` on every request, so they are the same while the crawl runs and after a
restart: requests, pages fetched/failed, URLs skipped and queued, bytes, cache hits (304), retries,
blocks, HTTP pages, browser pages and fallbacks, identical pages, pages per minute, and latency p50,
p95 and max (network time only: politeness waits and backoff are not latency). Failed pages are
grouped by reason with an example URL. The URLs being fetched right now come from the worker's
memory and are listed only while the crawl runs there.

## 9. Messages

| Request         | Answer           | Command           |
| --------------- | ---------------- | ----------------- |
| `crawl.start`   | `CrawlRun`       | `start_crawl`     |
| `crawl.control` | `CrawlRun`       | `control_crawl`   |
| `crawl.runs`    | `CrawlRunPage`   | `list_crawl_runs` |
| `crawl.urls`    | `CrawlUrlPage`   | `list_crawl_urls` |
| `crawl.url`     | `CrawlUrlDetail` | `get_crawl_url`   |

All additive: `protocol_version` and `data_contract_version` stay 1.

## 10. Reason codes

| Code                                                                                           | Status            | Meaning                                            |
| ---------------------------------------------------------------------------------------------- | ----------------- | -------------------------------------------------- |
| `robots`, `private_area`, `trap`, `binary_resource`, `excluded`, `not_included`                | skipped           | Policy (section 4)                                 |
| `page_budget`, `byte_budget`, `time_budget`, `request_budget`                                  | skipped           | A limit (section 6)                                |
| `robots_unreachable`, `unsafe_target`, `dns_error`, `blocked`                                  | skipped or failed | The site stops                                     |
| `redirect_loop`, `too_many_redirects`, `redirect_invalid`, `redirect_out_of_scope`             | failed            | Redirects                                          |
| `timeout`, `connection_failed`, `network_error`, `rate_limited`, `server_error`                | failed            | Transient, after 3 attempts                        |
| `not_found`, `http_error`, `decoding_error`, `too_large`, `unsupported_type`, `empty_response` | failed            | Final answers                                      |
| `not_modified`                                                                                 | fetched           | 304: the earlier parsed record is reused           |
| `browser_unavailable`, `browser_failed`                                                        | fetched           | The HTTP content is kept; the fallback did not run |
| `internal_error`                                                                               | failed            | A bug; the traceback is in the worker log          |
