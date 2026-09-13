# Website intelligence

How Lead Intelligence turns what the crawler acquired from a business website into a reproducible
technical and commercial diagnosis: findings with their evidence, subscores that never count what
could not be evaluated, and what changed between two audits. Delivered in phase 08.

- Page facts (pure, versioned extractor): [`website/extract.py`](../services/research-engine/src/research_engine/website/extract.py)
- Rules, subscores and comparison (pure, versioned ruleset): [`website/rules.py`](../services/research-engine/src/research_engine/website/rules.py)
- Store (snapshots, audits, reads, runs): [`website/store.py`](../services/research-engine/src/research_engine/website/store.py)
- Job: [`website/engine.py`](../services/research-engine/src/research_engine/website/engine.py)
- Schema: [`migrations/sqlite/0007_website_intelligence.sql`](../services/research-engine/src/research_engine/migrations/sqlite/0007_website_intelligence.sql)
- Decisions: D-052 to D-057 in [decisions.md](decisions.md)

## 1. What an audit reads

An audit reads **one crawl**: the latest finished `web.crawler` source run of a site (succeeded,
failed or cancelled) in a project. Nothing is fetched again, nothing is rendered, clicked or
submitted, and no model is asked anything.

| Input                      | Where it comes from                                                                                    |
| -------------------------- | ------------------------------------------------------------------------------------------------------ |
| Pages                      | `crawl_urls` page rows that were fetched, failed or left queued, in `(depth, url)` order               |
| HTML of a page             | The artifact of its raw record: the rendered HTML when the crawler rendered it, the cached copy on 304 |
| Status, redirects, headers | `crawl_urls.http_status`, `redirects_json` (every hop), `headers_json` (the headers the crawler keeps) |
| Lab timings                | `crawl_urls.fetch_ms` (request, redirects and download) and `browser_ms` (render)                      |
| robots.txt, sitemaps       | Their crawl rows and parsed payloads (`text`, `location_count`)                                        |
| Crawl limits               | URLs skipped for a budget (`page_budget`, `request_budget`, `time_budget`, `byte_budget`)              |

Every stored page is read again with `extract.page_facts` (`EXTRACTOR_VERSION = 1.0.0`): title,
meta tags, canonical, hreflang, favicon, Open Graph, headings, main-content words, internal, phone,
email, messaging, map and social links, visible phones, emails and US street addresses, hours,
service area, copyright year, forms (purpose, fields, required and unlabelled fields, captcha, file
upload, insecure action), calls to action, images without `alt`, links without a name, zoom,
mixed-content references, technologies, trust signals and structured data (valid and invalid
JSON-LD blocks, types, LocalBusiness properties, FAQPage, `sameAs`, microdata). Duplicates the
crawler already recognised are listed but not analyzed twice.

**The snapshot.** Everything the rules read — the pages with their facts, robots.txt, sitemaps,
limits and versions — is stored with the audit (`snapshot_json`). An audit is a pure function of its
snapshot and a ruleset: recalculating with newer rules reuses the snapshot, even if the artifacts are
gone, and produces a new audit without touching the old one.

### Why a page has no facts (`gap`)

| Gap                | Meaning                                                      | Expected page? |
| ------------------ | ------------------------------------------------------------ | -------------- |
| `fetch_failed`     | The crawler requested it and it failed                       | yes            |
| `not_fetched`      | Still queued when the crawl stopped (paused, cancelled)      | yes            |
| `artifact_missing` | The row says fetched but its stored HTML is not on disk      | yes            |
| `not_html`         | The stored content is not HTML                               | yes            |
| `too_large`        | Larger than 6 MiB (the crawler never stores more than 5 MiB) | yes            |
| `unreadable`       | The stored HTML could not be parsed                          | yes            |
| `duplicate`        | Same visible text as another page of the crawl               | no             |

## 2. Findings

Each rule produces exactly one finding per audit:

| Field                      | Meaning                                                                                    |
| -------------------------- | ------------------------------------------------------------------------------------------ |
| `rule_id` + `rule_version` | Stable code (`seo.title`) and the version of the rule that decided                         |
| `category`                 | One of nine categories, each with its own subscore                                         |
| `result`                   | `pass`, `warning`, `fail`, `unknown` (the snapshot cannot show it) or `not_applicable`     |
| `severity`                 | `critical`, `high`, `medium`, `low` or `info`: the weight of the rule, whatever its result |
| `observed_value`           | What the pages show, in words and numbers                                                  |
| `expected_condition`       | The condition the rule checks                                                              |
| `evidence`                 | Crawled URL (and `crawl_url_id`), the field read and the fragment that shows it            |
| `confidence`               | How far the check can be trusted (0 for `unknown`)                                         |
| `remediation`              | What to do                                                                                 |
| `commercial_relevance`     | Why it matters to the business                                                             |

Findings are listed with failures first, then warnings, unknowns, passes and not applicable, by
severity. Rule titles, conditions and remediations are English text of the ruleset; categories,
results and severities are translated in the interface.

**Never concluded from an absence.** A technology that leaves no trace in the HTML is not detected,
which never means it is absent: analytics that are not detected are a warning (`not detected`), or
`unknown` when a tag manager could be loading them; advertising and call tracking are informational
and `unknown` when not detected, with the observation that this does not show that the business does
not advertise. No measurement rule ever fails.

## 3. The rules — `RULESET_VERSION = 1.0.0`

Confidence is 0.95 unless stated. "Home" is the page at the crawl's root address. "Linked" means an
analyzed page, a crawled URL, or an internal link whose path, title, heading or text names the kind
of page (English and Spanish words).

### Technical

| Rule                        | Severity | Pass                                          | Warning                   | Fail                                                         | Unknown / not applicable    | Limits                                                                                     |
| --------------------------- | -------- | --------------------------------------------- | ------------------------- | ------------------------------------------------------------ | --------------------------- | ------------------------------------------------------------------------------------------ |
| `technical.https`           | high     | the home page ends on an https address        | —                         | it ends on http                                              | home not crawled            | only the home page's final address                                                         |
| `technical.mixed_content`   | medium   | https pages reference no http resource        | —                         | a script, image, frame, media, embed or stylesheet over http | no https page (n/a)         | static references only; resources injected by scripts or CSS are not seen (0.9)            |
| `technical.redirect_chain`  | medium   | at most one redirect                          | two redirects             | three or more, a loop, or too many redirects                 | home not crawled            | the home page's chain                                                                      |
| `technical.domain_redirect` | medium   | the final address stays on the crawled site   | it ends on another domain | —                                                            | no final address            | `www` and scheme changes are the same site; a move may be deliberate                       |
| `technical.page_errors`     | high     | every requested page answered                 | some failed (up to 20 %)  | the home page failed, or more than 20 % failed               | nothing requested           | only failures a visitor would meet: crawl budgets, robots.txt and bot walls are not errors |
| `technical.broken_links`    | medium   | the linked pages the crawl requested answered | —                         | one answered 4xx/5xx or not found                            | none of them was requested  | links beyond the crawl budget are not checked                                              |
| `technical.server_response` | low      | median crawler response ≤ 800 ms              | ≤ 2 s                     | slower                                                       | no timing                   | lab: one request per page from this machine and network (0.5)                              |
| `technical.html_weight`     | low      | home HTML ≤ 500 KiB                           | ≤ 1.5 MiB                 | larger                                                       | size not recorded           | the rendered HTML when the page was rendered (0.9)                                         |
| `technical.compression`     | low      | gzip, Brotli, Zstandard or deflate            | served uncompressed (0.8) | —                                                            | no headers, or a 304 answer | the headers of the home page                                                               |

### Search engines

| Rule                     | Severity | Pass                                                  | Warning                                          | Fail                                                                                                  | Unknown / not applicable            | Limits                                                |
| ------------------------ | -------- | ----------------------------------------------------- | ------------------------------------------------ | ----------------------------------------------------------------------------------------------------- | ----------------------------------- | ----------------------------------------------------- |
| `seo.indexable`          | critical | no noindex, and robots.txt allows the home page (0.9) | —                                                | `noindex`/`none` in meta robots or `X-Robots-Tag`, or robots.txt disallows the home page to Googlebot | home not analyzed                   | robots.txt is read for Googlebot's group, else `*`    |
| `seo.title`              | high     | 10 to 70 characters                                   | shorter or longer                                | no title                                                                                              | home not analyzed                   | the home page                                         |
| `seo.duplicate_metadata` | medium   | every page has its own title and description          | a page without a title, or pages sharing one     | —                                                                                                     | fewer than two pages analyzed (n/a) | analyzed pages only                                   |
| `seo.meta_description`   | medium   | 50 to 170 characters                                  | shorter or longer                                | no description                                                                                        | home not analyzed                   | the home page                                         |
| `seo.h1`                 | medium   | exactly one H1                                        | several                                          | none                                                                                                  | home not analyzed                   | the home page                                         |
| `seo.canonical`          | low      | canonical URL on the same site                        | no canonical (0.9)                               | canonical on another site                                                                             | home not analyzed                   | the home page                                         |
| `seo.sitemap`            | low      | a sitemap read lists URLs                             | it lists none, or none could be read (0.85)      | —                                                                                                     | no sitemap requested                | sitemaps declared in robots.txt, else `/sitemap.xml`  |
| `seo.robots_txt`         | low      | read                                                  | absent or unusable (4xx, wrong type) (0.9)       | unreadable (5xx, network): search engines stop crawling                                               | not requested                       | —                                                     |
| `seo.hreflang`           | low      | hreflang alternates declared                          | Spanish and English pages without hreflang (0.8) | —                                                                                                     | pages in one language (n/a)         | language from common words, else the `lang` attribute |

### Local search

| Rule                              | Severity | Pass                                                                                               | Warning                                     | Fail                                | Unknown / not applicable                              | Limits                                                |
| --------------------------------- | -------- | -------------------------------------------------------------------------------------------------- | ------------------------------------------- | ----------------------------------- | ----------------------------------------------------- | ----------------------------------------------------- |
| `local_seo.phone_visible`         | high     | a phone number in the text or a tel: link                                                          | —                                           | none on the analyzed pages (0.85)   | no page analyzed                                      | North American numbers                                |
| `local_seo.address_or_area`       | medium   | a street address or state and ZIP (0.8), LocalBusiness address, service area (0.7) or `areaServed` | none stated (0.7)                           | —                                   | no page analyzed                                      | US address patterns in English                        |
| `local_seo.local_business_schema` | high     | valid JSON-LD with a LocalBusiness type or subtype                                                 | Organization only                           | only invalid JSON-LD, or none (0.9) | no page analyzed                                      | 57 schema.org types count as LocalBusiness            |
| `local_seo.schema_properties`     | medium   | name, address (street or locality) and telephone                                                   | a property is missing                       | —                                   | no LocalBusiness data (n/a)                           | the most complete LocalBusiness item                  |
| `local_seo.nap_consistency`       | medium   | every structured-data telephone is shown on a page                                                 | one is not                                  | —                                   | no structured-data telephone, or no phone shown (n/a) | only phones are compared; names and addresses are not |
| `local_seo.hours`                 | low      | hours in the text (0.75) or opening hours in structured data                                       | none stated (0.7)                           | —                                   | no page analyzed                                      | day and time ranges in English and Spanish            |
| `local_seo.map_or_directions`     | low      | a map embed or link, or a directions action                                                        | none (0.8)                                  | —                                   | no page analyzed                                      | Google, Apple and Bing maps                           |
| `local_seo.locations`             | low      | several addresses and a locations page (0.8)                                                       | several addresses, no locations page (0.75) | —                                   | one address or none (n/a)                             | distinct addresses as written on the pages            |

### Content

| Rule                     | Severity | Pass                                                               | Warning                         | Fail                                             | Unknown / not applicable | Limits                                                         |
| ------------------------ | -------- | ------------------------------------------------------------------ | ------------------------------- | ------------------------------------------------ | ------------------------ | -------------------------------------------------------------- |
| `content.contact_page`   | medium   | a contact page is linked (0.85)                                    | —                               | none linked (0.7)                                | home not analyzed        | a menu built by scripts on an unrendered page is not seen      |
| `content.about_page`     | medium   | an about page is linked (0.85)                                     | none linked (0.7)               | —                                                | home not analyzed        | same                                                           |
| `content.services_page`  | medium   | a services page is linked (0.85)                                   | none linked (0.7)               | —                                                | home not analyzed        | same                                                           |
| `content.privacy_policy` | low      | a privacy policy is linked (0.85)                                  | none linked, no lead form (0.7) | lead forms without a linked privacy policy (0.7) | home not analyzed        | does not judge the policy's content                            |
| `content.home_content`   | medium   | ≥ 250 words of main content (0.85)                                 | ≥ 80                            | fewer                                            | home not analyzed        | `main` or `article`, else the body; text in images is not read |
| `content.freshness`      | low      | newest copyright year is the crawl's year or the year before (0.6) | older (0.6)                     | —                                                | no copyright year        | a footer year is a weak sign of maintenance                    |
| `content.open_graph`     | low      | `og:title` and `og:image`                                          | one or both missing (0.9)       | —                                                | home not analyzed        | the home page                                                  |
| `content.favicon`        | low      | an icon link is declared                                           | none declared (0.7)             | —                                                | home not analyzed        | a `/favicon.ico` served without a link is not requested        |

### Conversion

| Rule                               | Severity | Pass                                                                                            | Warning                                       | Fail                                            | Unknown / not applicable                     | Limits                                                                                                                              |
| ---------------------------------- | -------- | ----------------------------------------------------------------------------------------------- | --------------------------------------------- | ----------------------------------------------- | -------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `conversion.primary_cta`           | high     | a call, quote, booking, contact or buy action in the header, as a button or styled as one (0.8) | actions only elsewhere on the home page (0.7) | none on the home page (0.8)                     | home not analyzed                            | a plain "Contact" menu link is not a main call to action; English and Spanish wording                                               |
| `conversion.click_to_call`         | high     | a tel: link                                                                                     | —                                             | a phone shown as plain text, or no phone (0.85) | no page analyzed                             | —                                                                                                                                   |
| `conversion.lead_form`             | medium   | a contact, quote or booking form (0.85), or a form tool (0.8)                                   | only booking or chat tools (0.75)             | none (0.8)                                      | no page analyzed                             | forms built by scripts on unrendered pages are not seen; no form is ever submitted                                                  |
| `conversion.form_friction`         | low      | the longest lead form asks ≤ 7 fields (0.85)                                                    | ≤ 12                                          | more                                            | no lead form (n/a)                           | visible fields; hidden fields and buttons are not counted                                                                           |
| `conversion.form_security`         | medium   | forms on https pages submit to https                                                            | —                                             | a form on an http page or submitting to http    | no form (n/a)                                | search and login forms are left out                                                                                                 |
| `conversion.booking_or_quote`      | low      | a booking tool, or a booking or quote action or form                                            | none (0.7)                                    | —                                               | no page analyzed                             | —                                                                                                                                   |
| `conversion.chat_or_messaging`     | low      | a chat tool or a WhatsApp, SMS or Messenger link (0.9)                                          | not detected (0.6)                            | —                                               | not detected but a tag manager could load it | widgets loaded later by scripts are not seen                                                                                        |
| `conversion.trust_signals`         | medium   | two or more kinds (0.75)                                                                        | one kind                                      | none (0.7)                                      | no page analyzed                             | testimonials, licence, insurance, guarantee, portfolio, experience, awards, ratings, review widgets; phrases in English and Spanish |
| `conversion.contact_in_navigation` | low      | a header or navigation link to contact, or a call button there                                  | none (0.75)                                   | —                                               | home not analyzed                            | the home page                                                                                                                       |

### Mobile

| Rule              | Severity | Pass                                 | Warning          | Fail                                    | Unknown / not applicable | Limits                |
| ----------------- | -------- | ------------------------------------ | ---------------- | --------------------------------------- | ------------------------ | --------------------- |
| `mobile.viewport` | high     | a viewport with `width=device-width` | another viewport | no viewport                             | home not analyzed        | no device is emulated |
| `mobile.zoom`     | medium   | zoom allowed                         | —                | `user-scalable=no` or `maximum-scale=1` | no viewport (n/a)        | the home page         |

### Accessibility

| Rule                          | Severity | Pass                                 | Warning            | Fail              | Unknown / not applicable | Limits                                                                                                  |
| ----------------------------- | -------- | ------------------------------------ | ------------------ | ----------------- | ------------------------ | ------------------------------------------------------------------------------------------------------- |
| `accessibility.lang`          | medium   | the html element declares a language | —                  | it does not       | home not analyzed        | the home page                                                                                           |
| `accessibility.image_alt`     | medium   | every image has `alt`                | up to 10 % without | more              | no images (n/a)          | `alt=""` counts as present (decorative); the quality of the text is not judged                          |
| `accessibility.form_labels`   | medium   | every field is labelled              | —                  | some field is not | no form fields (n/a)     | `label for`, a wrapping label, `aria-label`, `aria-labelledby` or `title`; a placeholder is not a label |
| `accessibility.link_names`    | low      | every link has a name (0.9)          | up to 3 without    | more              | no page analyzed         | text, `aria-label`, `title` or an image `alt`                                                           |
| `accessibility.heading_order` | low      | no heading level skipped             | levels skipped     | —                 | no headings (n/a)        | the home page; this is not a full accessibility audit                                                   |

### Measurement

| Rule                      | Severity | Pass                                           | Warning            | Fail  | Unknown / not applicable                                         | Limits                                                  |
| ------------------------- | -------- | ---------------------------------------------- | ------------------ | ----- | ---------------------------------------------------------------- | ------------------------------------------------------- |
| `measurement.analytics`   | medium   | analytics detected                             | not detected (0.6) | never | not detected but a tag manager is                                | tags inside a tag manager or consent tool are invisible |
| `measurement.tag_manager` | info     | a tag manager detected                         | —                  | never | not detected                                                     | informational, not scored                               |
| `measurement.advertising` | info     | an advertising pixel or call tracking detected | —                  | never | not detected: this does not show the business does not advertise | informational, not scored                               |

### Answer engines and AI assistants

| Rule                                 | Severity | Pass                                                                                                                      | Warning                                              | Fail                                                                                         | Unknown / not applicable               | Limits                                                                                        |
| ------------------------------------ | -------- | ------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- | -------------------------------------------------------------------------------------------- | -------------------------------------- | --------------------------------------------------------------------------------------------- |
| `aeo_geo.content_without_javascript` | high     | pages show their content without JavaScript (0.9)                                                                         | —                                                    | pages the crawler had to render (empty shell, JavaScript required, blocked for non-browsers) | every page rendered by configuration   | the crawler's own criteria (`fallback_reason`)                                                |
| `aeo_geo.structured_data`            | medium   | valid JSON-LD describing an entity, or microdata (0.8)                                                                    | valid and invalid blocks together                    | only invalid blocks, or none (0.9)                                                           | no page analyzed                       | validity is JSON syntax plus known types, not schema.org validation                           |
| `aeo_geo.faq`                        | medium   | FAQPage data, an FAQ page (0.85) or ≥ 3 question headings (0.8)                                                           | 1 or 2 question headings (0.8)                       | none (0.75)                                                                                  | no page analyzed                       | questions in headings, English and Spanish                                                    |
| `aeo_geo.entity_profiles`            | low      | `sameAs` data or a link to a profile elsewhere (0.85)                                                                     | none (0.8)                                           | —                                                                                            | no page analyzed                       | Facebook, Instagram, X, LinkedIn, YouTube, TikTok, Yelp, Nextdoor, Pinterest, Google Business |
| `aeo_geo.ai_crawlers`                | medium   | robots.txt lets GPTBot, OAI-SearchBot, PerplexityBot, Google-Extended and CCBot read `/`, or there is no robots.txt (0.9) | one of them is blocked (a choice the owner may make) | —                                                                                            | robots.txt not requested or unreadable | the user agents named here only                                                               |

## 4. Technologies

Signatures match the traces a page itself carries, each with a confidence: a script address 0.95,
an iframe address or a generator meta tag 0.9, inline script text 0.85, markup 0.75, a link 0.7. A
technology seen on several pages keeps its strongest trace and the first three references as
evidence; a version is kept when the trace carries one (WordPress and Drupal generators, Angular,
jQuery).

| Category                 | Signatures                                                                                                                                  |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Site builders and CMS    | WordPress, Wix, Squarespace, Webflow, GoDaddy Website Builder, Weebly, Duda, Joomla, Drupal                                                 |
| Online stores            | Shopify, WooCommerce                                                                                                                        |
| Frameworks and libraries | Next.js, Nuxt, Gatsby, Angular, React, jQuery                                                                                               |
| Analytics                | Google Analytics 4, Universal Analytics, Microsoft Clarity, Hotjar, Matomo, Plausible                                                       |
| Tag management           | Google Tag Manager                                                                                                                          |
| Advertising              | Meta Pixel, Google Ads, TikTok Pixel, LinkedIn Insight Tag, Microsoft Advertising UET                                                       |
| Call tracking            | CallRail                                                                                                                                    |
| Booking                  | Calendly, Acuity Scheduling, Square Appointments, Housecall Pro, Jobber, ServiceTitan, Setmore, Vagaro, Booksy, Mindbody, OpenTable, Zocdoc |
| Chat                     | Intercom, Drift, tawk.to, LiveChat, Zendesk Chat, HubSpot, Tidio, Crisp, Podium, Facebook Customer Chat                                     |
| Forms                    | HubSpot Forms, Typeform, Jotform, Gravity Forms, Contact Form 7, WPForms, Google Forms                                                      |
| Reviews and maps         | Elfsight widgets, Trustindex, Birdeye, Google Maps, Mapbox                                                                                  |
| Bot protection           | reCAPTCHA, hCaptcha, Cloudflare Turnstile                                                                                                   |

Hosting providers are not guessed: the crawler keeps only the `server` header, and most business
sites answer through a CDN.

**Conversion elements** are listed with their evidence: main and other calls to action, tel: links,
messaging links, contact, quote, booking and newsletter forms, booking and chat tools, and maps.

## 5. Subscores, coverage and confidence

```text
score      = round(100 × Σ weight × credit / Σ weight)         over the rules of a category that could be evaluated
credit     = pass 1 · warning 0.5 · fail 0
weight     = critical 10 · high 6 · medium 3 · low 1 · info 0
coverage   = evaluated weight / (evaluated + unknown weight)
confidence = Σ weight × confidence / Σ weight                  over the evaluated rules
```

- `unknown` and `not_applicable` results are left out of the score: they never lower it. A category
  whose rules are all unknown has no score (`null`), not zero.
- The formula is stored with every audit, next to its subscores.
- **There is no overall score.** One number would hide what could not be evaluated, and the
  commercial score belongs to phase 11.
- The audit's own `coverage` is the same ratio over every rule.

**Audit status.** `empty` when no page could be analyzed; `partial` when some expected page could
not be analyzed, the home page was not analyzed, or the crawl did not succeed (cancelled or failed);
`complete` otherwise. Pages a crawl budget left out are not expected pages: they are reported under
`limits_hit` and the note `crawl_limits_reached`.

## 6. Performance: lab only, never invented

Every page lists its crawler measurements — response time, render time, HTML size, scripts and
images — with the tool (`crawler 1.0.0: one request per page, no device or network emulation`), the
device profile (`crawler_http` or `crawler_browser`) and when it was measured. There is **no field
data** and **no Core Web Vitals**: every audit carries the notes `field_data_unavailable` and
`core_web_vitals_unavailable`, and no rule estimates them.

Lighthouse and PageSpeed Insights are not integrated (D-055): Lighthouse needs Node and a Chrome
profile the sidecar does not ship, and PageSpeed Insights would send every address to a third
party. A later integration would add lab and field sections with their own environment, never
overwrite the crawler's figures.

## 7. History and comparison

- **Idempotent.** At most one audit per site, crawl, extractor version and ruleset version: auditing
  again without a new crawl writes nothing (`unchanged` in the run).
- **Versioned.** A newer ruleset over the same crawl recalculates from the stored snapshot into a new
  audit; the previous audit and its findings never change (triggers refuse any update or delete).
- **Comparison.** Every audit points at the previous audit of its site and stores what changed:
  whether the rules changed, the final address before and after and whether the **domain changed**,
  subscores that moved, findings whose result changed (with rules that appeared or disappeared),
  technologies added and removed, and analyzed pages added and removed.
- **History.** Every audit of a site, newest first, with its status, ruleset and counts.

## 8. Runs

`jobs.kind = website_audit`, one open run per project. A run audits the sites of a project in site
order, one transaction and one checkpoint per site, so pause, cancel and a worker restart resume
exactly where it stopped; a run a dead worker left running is paused at startup (D-027). A run can
be limited to some sites of one project, to audit them again.

| Message           | Payload                   | Answer               |
| ----------------- | ------------------------- | -------------------- |
| `website.start`   | `WebsiteStartRequest`     | `WebsiteRunPage`     |
| `website.control` | `DiscoveryControlRequest` | `WebsiteRun`         |
| `website.runs`    | `WebsiteRunListQuery`     | `WebsiteRunPage`     |
| `website.audits`  | `WebsiteAuditListQuery`   | `WebsiteAuditPage`   |
| `website.audit`   | `WebsiteAuditQuery`       | `WebsiteAuditDetail` |

The desktop core validates every answer before the interface sees it: each subscore must count
exactly the findings of its category (an `unknown` reported as a failure is refused), the counts of
an audit must match its findings, a comparison must be with the previous audit of the same site,
and a page of latest audits cannot list a site twice.

## 9. Interface

Research Center ▸ **Website intelligence**: the latest audit of every crawled site (status, pages
analyzed, coverage, failed, warnings, unknown, crawled and audited dates), the latest run with its
controls, and a button to audit every crawled site. Selecting a site opens its audit: failures and
warnings first, filterable by result, category and severity, each with what was observed, what was
expected, what to do, why it matters and its evidence; coverage and notes; subscores with their
counts and formula; technologies with their confidence; conversion elements; the pages read with
their lab measurements and why any is missing; the comparison with the previous audit; the history,
from which any earlier audit opens; and a button to audit the site again. Runs also appear in Jobs.

## 10. What this engine never does

- It does not fetch, render, click or submit anything, and never logs in.
- It does not ask a model for findings or read a model's opinion of a site.
- It does not say that a business does not advertise, track or chat because nothing was detected.
- It does not estimate Core Web Vitals, field performance or traffic.
- It does not produce an overall or commercial score, and it does not recommend what to sell.
