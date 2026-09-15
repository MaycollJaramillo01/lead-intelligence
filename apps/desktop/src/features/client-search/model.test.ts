import { describe, expect, it } from "vitest";
import type { IcpDefinition } from "@lead/shared-types";
import { BUSINESS_TYPES } from "@/features/campaigns/businessTypes";
import {
  buildCampaignRequest,
  buildIcpRequest,
  buildPipelineRequest,
  candidateLimit,
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

  it("creates an offer profile without duplicating campaign filters", () => {
    const request = buildIcpRequest(draft, template, {
      website: "Websites",
      conversion: "Conversion",
      booking: "Booking",
      google: "Google",
      measurement: "Measurement",
    });
    expect(request.definition.geography.importance).toBe("ignore");
    expect(request.definition.categories.terms).toEqual([]);
    expect(request.definition.latino).toBe("market_affinity");
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
