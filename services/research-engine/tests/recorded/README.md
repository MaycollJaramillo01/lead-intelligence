# Recorded source responses

Real answers from public APIs, saved verbatim so the test suite never needs the network.

| File                        | Request                                                                |
| --------------------------- | ---------------------------------------------------------------------- |
| `usaspending_page1..3.json` | `POST /api/v2/search/spending_by_award/`, contracts (A-D), NAICS 23, recipients in Hialeah FL, awards from 2024-01-01, `limit: 5`, sorted by award id. Recorded 2026-09-12. Thirteen awards over three pages, seven distinct businesses, two of them spanning a page boundary - so pagination, the end of a listing and the light duplicate check all have real data to work on. |

Re-record a file by replaying the same request and saving the body unchanged. Never edit one by
hand: its value is that it is exactly what the source returned.
