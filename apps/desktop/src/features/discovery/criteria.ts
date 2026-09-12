import type {
  CriteriaProblem,
  DiscoveryCriteria,
  SourceInfo,
  SourceSelection,
} from "@lead/shared-types";

/** The starting point of a new campaign. Mirrors `empty_criteria()` in the worker. */
export function emptyCriteria(): DiscoveryCriteria {
  return {
    schema_version: 1,
    geography: [],
    verticals: [],
    categories: [],
    services: [],
    keywords: [],
    exclusions: { names: [], domains: [], categories: [], postal_codes: [] },
    market_languages: [],
    size_proxies: [],
    freshness_since: null,
    strategy: "broad",
    sources: [],
    limits: { max_candidates: 1000, max_requests: 200, max_pages_per_source: 50 },
  };
}

/** One term per line, so a long list stays readable and pasteable. */
export function toLines(values: string[]): string {
  return values.join("\n");
}

export function fromLines(text: string, limit = 50): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, limit);
}

export function fromCommas(text: string, limit = 50): string[] {
  return text
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean)
    .slice(0, limit);
}

export function hasBlockers(problems: CriteriaProblem[]): boolean {
  return problems.some((problem) => problem.severity === "error");
}

export function selectionFor(
  criteria: DiscoveryCriteria,
  key: string,
): SourceSelection | undefined {
  return criteria.sources.find((source) => source.connector_key === key);
}

/** Adds or removes one source, keeping the rest of the selection untouched. */
export function withSource(
  criteria: DiscoveryCriteria,
  source: SourceInfo,
  selected: boolean,
): DiscoveryCriteria {
  const others = criteria.sources.filter((item) => item.connector_key !== source.metadata.key);
  if (!selected) return { ...criteria, sources: others };
  return {
    ...criteria,
    sources: [
      ...others,
      { connector_key: source.metadata.key, priority: 50, max_records: null, config_json: {} },
    ],
  };
}

export function withSourceConfig(
  criteria: DiscoveryCriteria,
  key: string,
  config: Record<string, unknown>,
): DiscoveryCriteria {
  return {
    ...criteria,
    sources: criteria.sources.map((source) =>
      source.connector_key === key
        ? { ...source, config_json: { ...source.config_json, ...config } }
        : source,
    ),
  };
}
