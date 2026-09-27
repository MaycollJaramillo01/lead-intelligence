import { describe, expect, it } from "vitest";
import type { ContactPoint, IcpDefinition, ProblemType } from "@lead/shared-types";
import { BUSINESS_TYPES } from "@/features/campaigns/businessTypes";
import type { Translator } from "@/lib/i18n";
import {
  buildCampaignRequest,
  buildIcpRequest,
  buildPipelineRequest,
  candidateLimit,
  clientColumnLabels,
  clientExportDraft,
  clientListQuery,
  contactsOf,
  DEFAULT_RADIUS_KM,
  latinoFirst,
  LIST_REASON,
  offerOf,
  outcomeRequest,
  stagesFor,
  tierKey,
  whyProblems,
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
    id: "0",
    contact_type: "phone",
    value_raw: "",
    origin: "published",
    is_primary: false,
    do_not_contact: false,
    validation_status: "observed",
    ...values,
  }) as ContactPoint;
/** A translator that shows which key it was asked for. */
const keys: Translator = (key) => `<${key}>`;

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
  const search = { campaign_id: "campaign", campaign_name: buildCampaignRequest(draft).name };
  const campaign = { kind: "condition", field: "campaign", operator: "has", value: "campaign" };
  const usable = [
    { kind: "condition", field: "suppressed", operator: "is_false", value: null },
    { kind: "condition", field: "usable_channels", operator: "gt", value: 0 },
  ];

  it("lists only what the search's campaign found, Latino evidence first when asked", () => {
    expect(latinoFirst(search.campaign_name)).toBe(true);
    expect(latinoFirst(buildCampaignRequest({ ...draft, latinoOnly: false }).name)).toBe(false);

    const query = clientListQuery("run", search);
    expect(query.score_run_id).toBe("run");
    expect(query.sort.map((key) => key.field)).toEqual(["latino_confidence", "total_score"]);
    expect(query.filters.children).toEqual([
      campaign,
      { kind: "condition", field: "tier", operator: "in", value: ["priority", "review"] },
      ...usable,
    ]);
  });

  it("narrows the list, and so its file, to what the filters keep", () => {
    const query = clientListQuery("run", search, { phone: true, email: true, callFirst: true });
    expect(query.filters.children).toEqual([
      campaign,
      { kind: "condition", field: "tier", operator: "in", value: ["priority"] },
      ...usable,
      { kind: "condition", field: "reachable_by", operator: "has", value: "phone" },
      { kind: "condition", field: "reachable_by", operator: "has", value: "email" },
    ]);
  });

  it("counts every lead the search found, whatever the filters", () => {
    const plain = { campaign_id: "campaign", campaign_name: "Buscar clientes · roofing" };
    const found = clientListQuery(
      null,
      plain,
      { phone: true, email: false, callFirst: true },
      true,
    );
    expect(found.sort.map((key) => key.field)).toEqual(["total_score"]);
    expect(found.filters.children).toEqual([campaign]);
    expect(found).toMatchObject({ limit: 1, with_total: true });
  });

  it("shows the phone and the email the file writes, never a guess", () => {
    expect(
      contactsOf([
        point({ contact_type: "email", value_raw: "hola@negocio.com", is_primary: true }),
        point({ id: "1", value_raw: "305 555 0100", validation_status: "invalid" }),
        point({ id: "2", value_raw: "305 555 0199" }),
        point({ id: "4", value_raw: "305 555 0111", validation_status: "likely_valid" }),
        point({ id: "3", value_raw: "305 555 0142", validation_status: "likely_valid" }),
      ]),
    ).toEqual({ phone: "305 555 0142", email: "hola@negocio.com" });
    expect(
      contactsOf([
        point({ value_raw: "305 555 0100", do_not_contact: true }),
        point({ contact_type: "email", value_raw: "info@negocio.com", origin: "hypothesis" }),
      ]),
    ).toEqual({ phone: null, email: null });
    expect(offerOf("simple_google")).toBe("google");
    expect(offerOf("custom_offer")).toBeNull();
    expect(offerOf(null)).toBeNull();
  });

  it("says why to call with at most two observed problems, the heaviest first", () => {
    const problems: ProblemType[] = ["weak_local_seo", "no_online_booking", "no_website"];
    expect(whyProblems("no_online_booking", problems)).toEqual([
      "no_online_booking",
      "weak_local_seo",
    ]);
    expect(whyProblems(null, problems)).toEqual(["weak_local_seo", "no_online_booking"]);
    expect(tierKey("priority")).toBe("clientList.tier.priority");
    expect(tierKey("excluded")).toBeNull();
  });

  it("records an action through every stage the funnel allows, only while it applies", () => {
    expect(stagesFor("contacted", "not_contacted")).toEqual(["attempted"]);
    expect(stagesFor("contacted", "attempted")).toEqual([]);
    expect(stagesFor("interested", "not_contacted")).toEqual([
      "attempted",
      "connected",
      "qualified",
    ]);
    expect(stagesFor("interested", "connected")).toEqual(["qualified"]);
    expect(stagesFor("interested", "qualified")).toEqual([]);
    expect(stagesFor("interested", "won")).toEqual([]);
    expect(stagesFor("not_interested", "attempted")).toEqual(["not_interested"]);
    expect(stagesFor("not_interested", "not_interested")).toEqual([]);
    expect(outcomeRequest("run", "business", "attempted", "phone")).toMatchObject({
      score_run_id: "run",
      business_id: "business",
      action: "record_outcome",
      stage: "attempted",
      channel: "phone",
      reason: LIST_REASON,
      lost_reason: null,
      occurred_at: null,
    });
  });

  it("downloads the same clients as the list, in words, with the number to call first", () => {
    const query = clientListQuery("run", search);
    const file = clientExportDraft(query, (key) => `[${key}]`, clientColumnLabels(keys));
    // the worker streams every lead the query matches, so its limit bounds the screen only
    expect(file.query).toBe(query);
    expect(file.columns.map((column) => column.key)).toEqual([
      "name",
      "phone",
      "email",
      "tier",
      "top_problem",
      "offer",
      "locality",
      "region",
      "domain",
      "total_score",
    ]);
    expect(file.columns.map((column) => column.header)).toEqual(
      file.columns.map((column) => `[${column.key}]`),
    );
    const labels = Object.fromEntries(file.columns.map((column) => [column.key, column.labels]));
    expect(labels.name).toBeNull();
    expect(labels.tier).toEqual({
      priority: "<clientList.tier.priority>",
      review: "<clientList.tier.review>",
      low: "<clientList.tier.low>",
    });
    expect(labels.top_problem?.no_website).toBe("<clientList.why.no_website>");
    expect(Object.keys(labels.top_problem ?? {})).toHaveLength(8);
    expect(labels.offer?.simple_booking).toBe("<clientSearch.offer.booking>");
    expect(file).toMatchObject({
      source_kind: "query",
      format: "csv",
      bom: true,
      include_suppressed: false,
      preset_key: null,
    });
  });
});
