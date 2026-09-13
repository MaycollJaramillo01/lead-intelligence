import { describe, expect, it } from "vitest";
import type {
  Business,
  BusinessDetail,
  BusinessListItem,
  BusinessPage,
  DatasetListItem,
  DatasetPage,
  DatasetSnapshot,
  LatinoDetail,
  LatinoEvidence,
  Provenance,
} from "@lead/shared-types";
import detailFixture from "@lead/shared-types/fixtures/business_detail.json";
import pageFixture from "@lead/shared-types/fixtures/business_page.json";
import datasetFixture from "@lead/shared-types/fixtures/dataset_page.json";
import latinoFixture from "@lead/shared-types/fixtures/latino_detail.json";

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

  it("timestamps use the single contract format", () => {
    const stamps = JSON.stringify(detailFixture).match(/"\w+_at":"[^"]*"/g) ?? [];
    expect(stamps.length).toBeGreaterThan(0);
    for (const stamp of stamps)
      expect(stamp).toMatch(/"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z"$/);
  });
});
