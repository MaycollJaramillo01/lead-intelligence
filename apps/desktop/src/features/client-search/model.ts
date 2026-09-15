import type {
  CampaignListItem,
  CampaignSaveRequest,
  ContactPoint,
  ExplorerCondition,
  ExplorerGroup,
  ExplorerQuery,
  ExportDraft,
  IcpDefinition,
  IcpSaveRequest,
  PipelineStage,
  PipelineStageView,
  PipelineStartRequest,
  ProblemType,
  ValidationStatus,
} from "@lead/shared-types";
import { emptyCriteria } from "@/features/discovery/criteria";
import { BUSINESS_TYPES, type BusinessType } from "@/features/campaigns/businessTypes";
import type { TranslationKey } from "@/lib/i18n";

export type OfferChoice = "website" | "conversion" | "booking" | "google" | "measurement";

export const OFFER_PROBLEMS: Record<OfferChoice, readonly ProblemType[]> = {
  website: ["no_website", "website_unreachable", "poor_mobile_experience"],
  conversion: ["weak_conversion_path"],
  booking: ["no_online_booking"],
  google: ["weak_local_seo", "weak_technical_seo"],
  measurement: ["measurement_not_detected"],
};

export interface ClientSearchDraft {
  offers: OfferChoice[];
  businessTypeId: string;
  latinoOnly: boolean;
  region: string;
  locality: string;
  radiusKm: number | null;
  wanted: 25 | 50 | 100;
}

export const candidateLimit = (wanted: number) => wanted * 12;
/** A city alone is only its own limits: Overture held 12 remodelers in Miami's, 64 within 40 km. */
export const DEFAULT_RADIUS_KM = 40;
/** The rows a result shows; its count covers every match. */
export const CLIENT_LIST_LIMIT = 100;
const LATINO_FIRST = " · latinos primero";
const UNUSABLE: ReadonlySet<ValidationStatus> = new Set(["invalid", "stale", "suppressed"]);

export function buildCampaignRequest(
  draft: ClientSearchDraft,
  existingId: string | null = null,
): CampaignSaveRequest {
  const type = BUSINESS_TYPES.find((item) => item.id === draft.businessTypeId);
  if (!type) throw new Error("Choose a business type");
  const candidates = candidateLimit(draft.wanted);
  const criteria = emptyCriteria();
  criteria.geography = [
    {
      country_code: "US",
      region: draft.region,
      metro: null,
      locality: draft.locality.trim(),
      postal_code: null,
      radius_km: draft.radiusKm ?? DEFAULT_RADIUS_KM,
    },
  ];
  criteria.categories = [...type.categories];
  criteria.verticals = [...type.naics];
  criteria.sources = [
    {
      connector_key: "overture.places",
      priority: 50,
      max_records: candidates,
      config_json: {},
    },
  ];
  criteria.limits = {
    max_candidates: candidates,
    max_requests: candidates,
    max_pages_per_source: Math.max(3, Math.ceil(candidates / 100)),
  };
  return {
    campaign_id: existingId,
    project_id: null,
    name: searchName(type, draft),
    objective: `Lista de ${draft.wanted} clientes`,
    criteria,
  };
}

export function searchName(type: BusinessType, draft: ClientSearchDraft): string {
  const name = `Buscar clientes · ${type.id} · ${draft.locality.trim()} · ${draft.region}`;
  return draft.latinoOnly ? name + LATINO_FIRST : name;
}

/** Whether a search asked for Latino businesses first: its name is where that choice is kept. */
export const latinoFirst = (campaignName: string | null) =>
  campaignName?.endsWith(LATINO_FIRST) === true;

export function buildIcpRequest(
  draft: ClientSearchDraft,
  template: IcpDefinition,
  offerNames: Record<OfferChoice, string>,
  existingId: string | null = null,
): IcpSaveRequest {
  const offers = draft.offers.map((choice) => ({
    key: `simple_${choice}`,
    name: offerNames[choice],
    description: offerNames[choice],
    solves: [...OFFER_PROBLEMS[choice]],
    capabilities: [],
    not_for_categories: [],
  }));
  return {
    profile_id: existingId,
    project_id: null,
    name: "Mi oferta",
    description: "Configurada desde Buscar clientes",
    definition: {
      ...template,
      // Latino evidence orders the list (`clientListQuery`) instead of being a requirement: as one
      // it excluded every business without published evidence, which is most of them.
      latino: "none",
      // Geography and category already bound the campaign; repeating them here would create
      // two independently editable filters that can contradict one another.
      geography: { ...template.geography, importance: "ignore", include: [], exclude: [] },
      categories: { ...template.categories, importance: "ignore", terms: [], excluded_terms: [] },
      problems: {
        ...template.problems,
        importance: "nice_to_have",
        wanted: [...new Set(offers.flatMap((offer) => offer.solves))],
      },
      offers,
    },
  };
}

export function buildPipelineRequest(
  projectId: string,
  campaignId: string,
  idempotencyKey: string,
): PipelineStartRequest {
  return {
    project_id: projectId,
    campaign_id: campaignId,
    check_mail_domains: false,
    priority: 0,
    idempotency_key: idempotencyKey,
  };
}

const condition = (
  field: ExplorerCondition["field"],
  operator: ExplorerCondition["operator"],
  value: ExplorerCondition["value"] = null,
): ExplorerCondition => ({ kind: "condition", field, operator, value });

/**
 * The clients of one search in a score run: priority or review, not on the do-not-contact list,
 * with a phone or email, best first (Latino evidence first when the search asked for it). With
 * `onlyFound`, every lead of the search whatever its tier, to say how many were checked.
 */
// ponytail: the explorer has no campaign filter, so the search's state and business types stand in
// for it: two searches of one type in one state share a list, and a place Overture matched only
// through a parent of its category stays out. A campaign filter in the explorer is the upgrade.
export function clientListQuery(
  scoreRunId: string | null,
  campaign: Pick<CampaignListItem, "name" | "criteria">,
  onlyFound = false,
): ExplorerQuery {
  const children: ExplorerGroup["children"] = [];
  const region = campaign.criteria.geography[0]?.region;
  if (region) children.push(condition("region", "eq", region));
  const terms = campaign.criteria.categories;
  if (terms.length > 0) {
    children.push({
      kind: "group",
      join: "or",
      negate: false,
      children: terms.map((term) => condition("category", "contains", term)),
    });
  }
  if (!onlyFound) {
    children.push(
      condition("tier", "in", ["priority", "review"]),
      condition("suppressed", "is_false"),
      condition("usable_channels", "gt", 0),
    );
  }
  return {
    score_run_id: scoreRunId,
    filters: { kind: "group", join: "and", negate: false, children },
    search: null,
    sort: latinoFirst(campaign.name)
      ? [
          { field: "latino_confidence", direction: "desc" },
          { field: "total_score", direction: "desc" },
        ]
      : [{ field: "total_score", direction: "desc" }],
    offset: 0,
    limit: onlyFound ? 1 : CLIENT_LIST_LIMIT,
    with_total: true,
  };
}

/** The columns of the file a search downloads, in the order a call list is read. */
export const CLIENT_COLUMNS = [
  "name",
  "phone",
  "email",
  "locality",
  "region",
  "domain",
  "top_problem",
  "total_score",
] as const;

/**
 * The file of a finished search: every client it matched, not only the ones the screen shows, each
 * with the phone or email to reach them. The headers come from the page, so the file speaks the
 * language of the screen that asked for it.
 */
export function clientExportDraft(
  query: ExplorerQuery,
  header: (key: (typeof CLIENT_COLUMNS)[number]) => string,
): ExportDraft {
  return {
    source_kind: "query",
    query,
    view_id: null,
    snapshot_id: null,
    format: "csv",
    columns: CLIENT_COLUMNS.map((key) => ({ key, header: header(key) })),
    // a spreadsheet reads the accents of these headers only with the byte-order mark
    bom: true,
    include_suppressed: false,
    suppressed_reason: null,
    preset_key: null,
    preset_version: null,
  };
}

/** What to call or write first: a phone before an email, the primary one before the others. */
export function contactOf(points: readonly ContactPoint[]): string | null {
  const usable = points
    .filter(
      (point) =>
        (point.contact_type === "phone" || point.contact_type === "email") &&
        !point.do_not_contact &&
        !UNUSABLE.has(point.validation_status),
    )
    .sort(
      (a, b) =>
        Number(b.contact_type === "phone") - Number(a.contact_type === "phone") ||
        Number(b.is_primary) - Number(a.is_primary),
    );
  return usable[0]?.value_raw ?? null;
}

/** The simple-search offer a lead was matched to (`simple_<choice>`), if it is one. */
export function offerOf(key: string | null): OfferChoice | null {
  const choice = key?.startsWith("simple_") ? key.slice("simple_".length) : null;
  return choice !== null && choice in OFFER_PROBLEMS ? (choice as OfferChoice) : null;
}

export const STAGE_MESSAGE: Record<PipelineStage, TranslationKey> = {
  discovery: "clientSearch.progress.discovery",
  acquisition: "clientSearch.progress.acquisition",
  normalization: "clientSearch.progress.deduplicate",
  entity_resolution: "clientSearch.progress.deduplicate",
  latino_evidence: "clientSearch.progress.latino",
  website_intelligence: "clientSearch.progress.website",
  contact_intelligence: "clientSearch.progress.contact",
  local_ai: "clientSearch.progress.ai",
  scoring: "clientSearch.progress.choose",
  review_export_eligibility: "clientSearch.progress.list",
};

export function visibleProgress(stages: PipelineStageView[]): PipelineStageView[] {
  return stages.filter(
    (stage, index) =>
      stage.stage !== "entity_resolution" || stages[index - 1]?.stage !== "normalization",
  );
}
