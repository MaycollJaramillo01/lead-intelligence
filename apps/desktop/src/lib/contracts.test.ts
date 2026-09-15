import { describe, expect, it } from "vitest";
import type {
  AiInferenceDetail,
  Business,
  BusinessDetail,
  BusinessListItem,
  BusinessPage,
  ContactChannel,
  ContactDetail,
  DatasetListItem,
  DatasetPage,
  DatasetSnapshot,
  FinalInput,
  LatinoDetail,
  LatinoEvidence,
  LeadScorecardDetail,
  OpportunityInput,
  Provenance,
  WebsiteAuditDetail,
  WebsiteFindingView,
} from "@lead/shared-types";
import aiFixture from "@lead/shared-types/fixtures/ai_inference_detail.json";
import detailFixture from "@lead/shared-types/fixtures/business_detail.json";
import contactFixture from "@lead/shared-types/fixtures/contact_detail.json";
import pageFixture from "@lead/shared-types/fixtures/business_page.json";
import datasetFixture from "@lead/shared-types/fixtures/dataset_page.json";
import latinoFixture from "@lead/shared-types/fixtures/latino_detail.json";
import scorecardFixture from "@lead/shared-types/fixtures/lead_scorecard_detail.json";
import websiteFixture from "@lead/shared-types/fixtures/website_audit_detail.json";

/*
 * TypeScript half of the contract round-trip. `satisfies Record<keyof T, true>` fails to compile
 * when a generated type gains or loses a field; the assertions fail when the fixtures produced by
 * the Python source of truth disagree with those types. Rust round-trips the same fixtures.
 */
const LIST_ITEM_KEYS = {
  id: true,
  project_id: true,
  display_name: true,
  status: true,
  lead_stage: true,
  locality: true,
  region: true,
  primary_domain: true,
  primary_phone: true,
  primary_category: true,
  source_count: true,
  evidence_count: true,
  member_count: true,
  conflict_count: true,
  latino_status: true,
  latino_confidence: true,
  latino_reviewed: true,
  last_seen_at: true,
  is_synthetic: true,
} satisfies Record<keyof BusinessListItem, true>;

const LATINO_DETAIL_KEYS = {
  business_id: true,
  root_business_id: true,
  effective: true,
  automatic: true,
  review_outdated: true,
  evidence: true,
  history: true,
  decisions: true,
  rules_version: true,
  extractor_version: true,
} satisfies Record<keyof LatinoDetail, true>;

const LATINO_EVIDENCE_KEYS = {
  evidence: true,
  claim: true,
  track: true,
  role: true,
  counted: true,
  source_reliability: true,
  freshness: true,
  freshness_label: true,
  organizational: true,
} satisfies Record<keyof LatinoEvidence, true>;

const WEBSITE_DETAIL_KEYS = {
  audit: true,
  summary: true,
  findings: true,
  technologies: true,
  conversion: true,
  diff: true,
  history: true,
} satisfies Record<keyof WebsiteAuditDetail, true>;

const WEBSITE_FINDING_KEYS = {
  rule_id: true,
  rule_version: true,
  title: true,
  category: true,
  result: true,
  severity: true,
  observed_value: true,
  expected_condition: true,
  remediation: true,
  commercial_relevance: true,
  confidence: true,
  evidence: true,
} satisfies Record<keyof WebsiteFindingView, true>;

const AI_DETAIL_KEYS = {
  run: true,
  business_name: true,
  outputs: true,
  inputs: true,
  warnings: true,
  decisions: true,
  is_synthetic: true,
} satisfies Record<keyof AiInferenceDetail, true>;

const CONTACT_DETAIL_KEYS = {
  business_id: true,
  root_business_id: true,
  channels: true,
  hypotheses: true,
  decision_makers: true,
  conflicts: true,
  contactability: true,
  business_suppression: true,
  decisions: true,
  rules_version: true,
  extractor_version: true,
} satisfies Record<keyof ContactDetail, true>;

const CONTACT_CHANNEL_KEYS = {
  key: true,
  contact_type: true,
  value: true,
  platform: true,
  level: true,
  location_id: true,
  decision_maker_id: true,
  role: true,
  role_or_department: true,
  origin: true,
  validation_status: true,
  validation_method: true,
  validated_at: true,
  signals: true,
  freshness: true,
  first_seen_at: true,
  last_seen_at: true,
  source_count: true,
  source_kinds: true,
  is_primary: true,
  suppression: true,
  shared_with: true,
  review: true,
  sources: true,
} satisfies Record<keyof ContactChannel, true>;

const BUSINESS_KEYS = {
  id: true,
  project_id: true,
  display_name: true,
  name_sort_key: true,
  status: true,
  lead_stage: true,
  merged_into_id: true,
  first_seen_at: true,
  last_seen_at: true,
  created_at: true,
  updated_at: true,
  deleted_at: true,
  deleted_reason: true,
} satisfies Record<keyof Business, true>;

const PROVENANCE_KEYS = {
  extraction_id: true,
  extractor: true,
  extractor_version: true,
  extracted_at: true,
  raw_record_id: true,
  source_run_id: true,
  acquired_page_id: true,
  connector_key: true,
  connector_name: true,
  connector_kind: true,
  method: true,
  source_url: true,
  external_id: true,
  acquired_at: true,
} satisfies Record<keyof Provenance, true>;

const DETAIL_KEYS = {
  business: true,
  is_synthetic: true,
  names: true,
  locations: true,
  websites: true,
  contacts: true,
  classifications: true,
  evidence: true,
  members: true,
  canonical: true,
  merges: true,
} satisfies Record<keyof BusinessDetail, true>;

const DATASET_ITEM_KEYS = {
  dataset: true,
  is_synthetic: true,
  snapshot_count: true,
  latest_snapshot: true,
} satisfies Record<keyof DatasetListItem, true>;

const SNAPSHOT_KEYS = {
  id: true,
  dataset_id: true,
  version: true,
  row_count: true,
  duckdb_table: true,
  parquet_path: true,
  schema_version: true,
  filters_json: true,
  created_at: true,
  query_json: true,
  cutoff_at: true,
  pipeline_versions_json: true,
  quality_json: true,
} satisfies Record<keyof DatasetSnapshot, true>;

const keys = (value: object) => Object.keys(value).sort();
const expected = (shape: object) => Object.keys(shape).sort();

describe("data contract fixtures match the generated TypeScript types", () => {
  it("business pages", () => {
    const page = pageFixture as BusinessPage;
    expect(keys(page)).toEqual(["items", "next_cursor", "total"]);
    for (const item of page.items) expect(keys(item)).toEqual(expected(LIST_ITEM_KEYS));
  });

  it("business detail with provenance", () => {
    const detail = detailFixture as BusinessDetail;
    expect(keys(detail)).toEqual(expected(DETAIL_KEYS));
    expect(keys(detail.business)).toEqual(expected(BUSINESS_KEYS));
    for (const observed of [...detail.names, ...detail.evidence, ...detail.contacts]) {
      expect(keys(observed.provenance)).toEqual(expected(PROVENANCE_KEYS));
    }
  });

  it("dataset pages", () => {
    const page = datasetFixture as DatasetPage;
    for (const item of page.items) {
      expect(keys(item)).toEqual(expected(DATASET_ITEM_KEYS));
      expect(keys(item.latest_snapshot ?? {})).toEqual(expected(SNAPSHOT_KEYS));
    }
  });

  it("latino evidence detail with provenance", () => {
    const detail = latinoFixture as LatinoDetail;
    expect(keys(detail)).toEqual(expected(LATINO_DETAIL_KEYS));
    expect(detail.evidence.length).toBeGreaterThan(0);
    for (const entry of detail.evidence) {
      expect(keys(entry)).toEqual(expected(LATINO_EVIDENCE_KEYS));
      expect(keys(entry.evidence.provenance)).toEqual(expected(PROVENANCE_KEYS));
    }
  });

  it("website audit detail with evidence per finding", () => {
    const detail = websiteFixture as WebsiteAuditDetail;
    expect(keys(detail)).toEqual(expected(WEBSITE_DETAIL_KEYS));
    expect(detail.summary.subscores).toHaveLength(9);
    for (const finding of detail.findings) {
      expect(keys(finding)).toEqual(expected(WEBSITE_FINDING_KEYS));
    }
    expect(detail.findings.some((finding) => finding.evidence.length > 0)).toBe(true);
  });

  it("contact detail keeps hypotheses apart, with the provenance of every source", () => {
    const detail = contactFixture as ContactDetail;
    expect(keys(detail)).toEqual(expected(CONTACT_DETAIL_KEYS));
    for (const channel of [...detail.channels, ...detail.hypotheses]) {
      expect(keys(channel)).toEqual(expected(CONTACT_CHANNEL_KEYS));
      for (const source of channel.sources) {
        expect(keys(source.provenance)).toEqual(expected(PROVENANCE_KEYS));
      }
    }
    expect(detail.hypotheses.length).toBeGreaterThan(0);
    expect(detail.hypotheses.every((c) => c.origin === "hypothesis" && !c.is_primary)).toBe(true);
    expect(detail.channels.some((c) => c.origin === "hypothesis")).toBe(false);
  });

  it("an AI answer cites only inputs the model was given, and is stored raw next to it", () => {
    const detail = aiFixture as AiInferenceDetail;
    expect(keys(detail)).toEqual(expected(AI_DETAIL_KEYS));
    const given = new Set(detail.inputs.map((input) => input.ref));
    const validated = detail.outputs.filter((output) => output.validated_output_json !== null);
    expect(validated.length).toBe(1);
    const cited = JSON.stringify(validated[0]?.validated_output_json).match(/"[A-Z]\d+"/g) ?? [];
    expect(cited.length).toBeGreaterThan(0);
    for (const id of cited) expect(given.has(id.replaceAll('"', ""))).toBe(true);
    expect(detail.outputs.every((output) => output.raw_output.length > 0)).toBe(true);
  });

  it("a scorecard adds up to its total and counts no feature without evidence", () => {
    const { scorecard } = scorecardFixture as LeadScorecardDetail;
    const final = new Set<string>([
      "ICP_MATCH",
      "LATINO_CONFIDENCE",
      "OPPORTUNITY_SCORE",
      "CONTACTABILITY",
    ] satisfies FinalInput[]);
    const opportunityInputs = new Set<string>([
      "DIGITAL_GAP",
      "BUSINESS_HEALTH",
      "BUYING_CAPACITY",
      "COMMERCIAL_INTENT",
    ] satisfies OpportunityInput[]);
    const contributions = (inputs: ReadonlySet<string>) =>
      scorecard.components
        .filter((view) => inputs.has(view.component))
        .reduce((sum, view) => sum + (view.contribution ?? 0), 0);
    const opportunity =
      scorecard.components.find((view) => view.component === "OPPORTUNITY_SCORE")?.value ?? null;
    if (scorecard.total_score === null || opportunity === null) {
      throw new Error("the fixture lead is scored");
    }
    // each contribution is rounded to four decimals, so their sum may drift by a few of those
    expect(Math.abs(contributions(final) - scorecard.total_score)).toBeLessThanOrEqual(0.01);
    expect(Math.abs(contributions(opportunityInputs) - opportunity)).toBeLessThanOrEqual(0.01);
    const known = scorecard.features.filter((feature) => feature.state === "known");
    expect(known.length).toBeGreaterThan(0);
    for (const feature of known) expect(feature.evidence.length).toBeGreaterThan(0);
    for (const feature of scorecard.features.filter((item) => item.state !== "known")) {
      expect([feature.value, feature.points]).toEqual([null, null]);
    }
  });

  it("timestamps use the single contract format", () => {
    const stamps = JSON.stringify(detailFixture).match(/"\w+_at":"[^"]*"/g) ?? [];
    expect(stamps.length).toBeGreaterThan(0);
    for (const stamp of stamps)
      expect(stamp).toMatch(/"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z"$/);
  });
});
