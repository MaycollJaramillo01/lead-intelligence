import { describe, expect, it } from "vitest";
import type { ContactPoint, IcpDefinition } from "@lead/shared-types";
import { BUSINESS_TYPES } from "@/features/campaigns/businessTypes";
import {
  buildCampaignRequest,
  buildIcpRequest,
  buildPipelineRequest,
  candidateLimit,
  clientExportDraft,
  clientListQuery,
  contactOf,
  DEFAULT_RADIUS_KM,
  latinoFirst,
  offerOf,
  type ClientSearchDraft,
} from "./model";

const draft: ClientSearchDraft = {
  offers: ["website", "booking"],
  businessTypeId: "roofing",
  latinoOnly: true,
  region: "TX",
  locality: "Houston",
  radiusKm: 25,
  wanted: 25,
};
const template: IcpDefinition = {
  schema_version: 1,
  geography: { importance: "ignore", weight: 1, include: [], exclude: [] },
  categories: { importance: "ignore", weight: 1, terms: [], excluded_terms: [] },
  size: { importance: "ignore", weight: 1, min_locations: 1 },
  freshness: { importance: "ignore", weight: 1, max_days_since_seen: 365 },
  reputation: { importance: "ignore", weight: 1, min_rating: null, min_reviews: null },
  digital_maturity: { importance: "ignore", weight: 1, wanted: [] },
  problems: { importance: "ignore", weight: 1, wanted: [] },
  channels: { importance: "ignore", weight: 1, any_of: [], require_decision_maker: false },
  buying_capacity: { importance: "ignore", weight: 1, min_proxies: 1 },
  latino: "none",
  exclusions: { names: [], domains: [], categories: [], regions: [], postal_codes: [] },
  offers: [],
};
const point = (values: Partial<ContactPoint>) =>
  ({
    contact_type: "phone",
    value_raw: "",
    is_primary: false,
    do_not_contact: false,
    validation_status: "observed",
    ...values,
  }) as ContactPoint;

describe("simple client search requests", () => {
  it("uses twelve candidates per requested client and Overture", () => {
    expect(candidateLimit(25)).toBe(300);
    const request = buildCampaignRequest(draft);
    expect(request.criteria.sources[0]).toMatchObject({
      connector_key: "overture.places",
      max_records: 300,
    });
    expect(request.criteria.geography[0]).toMatchObject({
      region: "TX",
      locality: "Houston",
      radius_km: 25,
    });
    expect(request.criteria.categories).toEqual(BUSINESS_TYPES[0]?.categories);
  });

  it("searches around the city when no radius is given", () => {
    const request = buildCampaignRequest({ ...draft, radiusKm: null });
    expect(request.criteria.geography[0]?.radius_km).toBe(DEFAULT_RADIUS_KM);
  });

  it("creates an offer profile that never requires Latino evidence", () => {
    const request = buildIcpRequest(draft, template, {
      website: "Websites",
      conversion: "Conversion",
      booking: "Booking",
      google: "Google",
      measurement: "Measurement",
    });
    expect(request.definition.geography.importance).toBe("ignore");
    expect(request.definition.categories.terms).toEqual([]);
    expect(request.definition.latino).toBe("none");
    expect(request.definition.offers[0]?.solves).toContain("no_website");
  });

  it("starts the existing process with an idempotency key", () => {
    expect(buildPipelineRequest("project", "campaign", "key")).toEqual({
      project_id: "project",
      campaign_id: "campaign",
      check_mail_domains: false,
      priority: 0,
      idempotency_key: "key",
    });
  });
});

describe("simple client search results", () => {
  it("keeps the Latino choice in the name and ranks Latino evidence first", () => {
    const latino = buildCampaignRequest(draft);
    const plain = buildCampaignRequest({ ...draft, latinoOnly: false });
    expect(latinoFirst(latino.name)).toBe(true);
    expect(latinoFirst(plain.name)).toBe(false);

    const query = clientListQuery("run", latino);
    expect(query.score_run_id).toBe("run");
    expect(query.sort.map((key) => key.field)).toEqual(["latino_confidence", "total_score"]);
    expect(query.filters.children).toEqual([
      { kind: "condition", field: "region", operator: "eq", value: "TX" },
      {
        kind: "group",
        join: "or",
        negate: false,
        children: [
          { kind: "condition", field: "category", operator: "contains", value: "roofing" },
        ],
      },
      { kind: "condition", field: "tier", operator: "in", value: ["priority", "review"] },
      { kind: "condition", field: "suppressed", operator: "is_false", value: null },
      { kind: "condition", field: "usable_channels", operator: "gt", value: 0 },
    ]);
  });

  it("counts every lead of the search when asked for what was found", () => {
    const found = clientListQuery(
      null,
      buildCampaignRequest({ ...draft, latinoOnly: false }),
      true,
    );
    expect(found.sort.map((key) => key.field)).toEqual(["total_score"]);
    expect(found.filters.children).toHaveLength(2);
    expect(found).toMatchObject({ limit: 1, with_total: true });
  });

  it("picks a usable phone before an email and reads the matched offer", () => {
    expect(
      contactOf([
        point({ contact_type: "email", value_raw: "hola@negocio.com", is_primary: true }),
        point({ value_raw: "305 555 0100", validation_status: "invalid" }),
        point({ value_raw: "305 555 0199" }),
      ]),
    ).toBe("305 555 0199");
    expect(contactOf([point({ value_raw: "305 555 0100", do_not_contact: true })])).toBeNull();
    expect(offerOf("simple_google")).toBe("google");
    expect(offerOf("custom_offer")).toBeNull();
    expect(offerOf(null)).toBeNull();
  });

  it("downloads the same clients as the list, with the number to call first", () => {
    const query = clientListQuery("run", buildCampaignRequest(draft));
    const file = clientExportDraft(query, (key) => `[${key}]`);
    // the worker streams every lead the query matches, so its limit bounds the screen only
    expect(file.query).toBe(query);
    expect(file.columns.map((column) => column.key).slice(0, 3)).toEqual([
      "name",
      "phone",
      "email",
    ]);
    expect(file.columns.map((column) => column.header)).toEqual(
      file.columns.map((column) => `[${column.key}]`),
    );
    expect(file).toMatchObject({
      source_kind: "query",
      format: "csv",
      bom: true,
      include_suppressed: false,
      preset_key: null,
    });
  });
});
