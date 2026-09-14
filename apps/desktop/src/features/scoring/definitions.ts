import type { Area, ScoringDefinition } from "@lead/shared-types";

/** One field whose value differs between two versions of a definition. */
export interface Change {
  path: string;
  before: string;
  after: string;
}

function flatten(value: unknown, path: string, out: Map<string, string>): void {
  if (Array.isArray(value)) {
    if (value.length === 0) out.set(path, "[]");
    value.forEach((item, index) => flatten(item, `${path}[${index}]`, out));
  } else if (value !== null && typeof value === "object") {
    for (const [key, item] of Object.entries(value)) {
      flatten(item, path ? `${path}.${key}` : key, out);
    }
  } else {
    out.set(path, JSON.stringify(value));
  }
}

/** Every field whose value differs between two definitions, in field order. */
export function diffDefinitions(before: unknown, after: unknown): Change[] {
  const left = new Map<string, string>();
  const right = new Map<string, string>();
  flatten(before, "", left);
  flatten(after, "", right);
  return [...new Set([...left.keys(), ...right.keys()])]
    .sort()
    .filter((path) => left.get(path) !== right.get(path))
    .map((path) => ({ path, before: left.get(path) ?? "—", after: right.get(path) ?? "—" }));
}

/** Terms written separated by commas or lines. */
export function toTerms(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((term) => term.trim())
    .filter(Boolean);
}

export function fromTerms(terms: readonly string[]): string {
  return terms.join(", ");
}

/** Areas written one per line as `country, region, city, postal code`; empty parts match anything. */
export function toAreas(text: string): Area[] {
  return text
    .split("\n")
    .map((line) => line.split(",").map((part) => part.trim()))
    .filter((parts) => parts.some(Boolean))
    .map(([country, region, locality, postal]) => ({
      country_code: (country || "US").toUpperCase(),
      region: region ? region.toUpperCase() : null,
      locality: locality || null,
      postal_code: postal || null,
    }));
}

export function fromAreas(areas: readonly Area[]): string {
  return areas
    .map((area) =>
      [area.country_code, area.region ?? "", area.locality ?? "", area.postal_code ?? ""]
        .join(", ")
        .replace(/(, )+$/, ""),
    )
    .join("\n");
}

/** The formulas of a scoring definition, as the engine applies them: nothing is hidden. */
export function formulaText(definition: ScoringDefinition): string {
  const weighted = (weights: ReadonlyArray<{ component: string; weight: number }>) =>
    weights
      .filter((weight) => weight.weight > 0)
      .map((weight) => `${weight.component} × ${weight.weight}`)
      .join(" + ") || "—";
  return [
    `OPPORTUNITY_SCORE = (${weighted(definition.opportunity)}) / Σ weight of the known inputs`,
    `FINAL_LEAD_SCORE = (${weighted(definition.final)}) / Σ weight of the known inputs`,
    `Gates first · priority ≥ ${definition.priority_threshold} · review ≥ ${definition.review_threshold} · at least ${Math.round(definition.min_known_weight * 100)}% of the final weight known`,
  ].join("\n");
}

/** The version a profile or model shows: its active version, else its newest. */
export function shownVersion<V extends { id: string }>(
  versions: readonly V[],
  activeId: string | null,
): V | undefined {
  return versions.find((version) => version.id === activeId) ?? versions[0];
}
