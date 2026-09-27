import type {
  CampaignSaveRequest,
  ContactPoint,
  ContactType,
  ExplorerCondition,
  ExplorerQuery,
  ExportDraft,
  IcpDefinition,
  IcpSaveRequest,
  LeadActionRequest,
  LeadStage,
  LeadTier,
  PipelineStage,
  PipelineStageView,
  PipelineStartRequest,
  ProblemType,
  ValidationStatus,
} from "@lead/shared-types";
import { emptyCriteria } from "@/features/discovery/criteria";
import { BUSINESS_TYPES, US_STATES, type BusinessType } from "@/features/campaigns/businessTypes";
import { PROBLEMS } from "@/features/scoring/labels";
import type { TranslationKey, Translator } from "@/lib/i18n";

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

export const WANTED = [25, 50, 100] as const satisfies readonly ClientSearchDraft["wanted"][];
export const candidateLimit = (wanted: number) => wanted * 12;
/** A city alone is only its own limits: Overture held 12 remodelers in Miami's, 64 within 40 km. */
export const DEFAULT_RADIUS_KM = 40;
/** The rows a result shows; its count covers every match. */
export const CLIENT_LIST_LIMIT = 100;
const LATINO_FIRST = " · latinos primero";
/** How far a published contact was checked, best first; any other status is no route. The order
 *  the worker writes a file with (`leads.index.STATUS_RANK`, `exports.best_contacts`). */
const STATUS_RANK: Partial<Record<ValidationStatus, number>> = {
  likely_valid: 0,
  domain_valid: 1,
  structurally_valid: 2,
  observed: 3,
  unknown: 4,
};

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

const SEARCH_PREFIX = "Buscar clientes · ";

/** What a search asked for, read back from the name `searchName` gave its campaign. */
export function parseSearchName(name: string | null) {
  if (!name?.startsWith(SEARCH_PREFIX)) return null;
  const latinoOnly = latinoFirst(name);
  const parts = name
    .slice(SEARCH_PREFIX.length, latinoOnly ? -LATINO_FIRST.length : undefined)
    .split(" · ");
  const [businessTypeId, locality, region] = parts;
  if (parts.length !== 3 || !businessTypeId || !locality || !region) return null;
  return { businessTypeId, locality, region, latinoOnly };
}

/** A search as the form asked for it (business type, place, «Latinos primero»); else its name. */
export function searchTitle(t: Translator, name: string | null): string | null {
  const asked = parseSearchName(name);
  if (!asked) return name;
  const type = BUSINESS_TYPES.find((item) => item.id === asked.businessTypeId);
  const state = US_STATES.find(([code]) => code === asked.region)?.[1] ?? asked.region;
  return [
    type ? t(type.label) : asked.businessTypeId,
    `${asked.locality}, ${state}`,
    asked.latinoOnly && t("clientSearch.latino"),
  ]
    .filter(Boolean)
    .join(" · ");
}

/** The offers a profile version sells, as the simple choices they were saved from. */
export function offersOf(definition: Record<string, unknown> | undefined): OfferChoice[] {
  const offers = definition?.offers;
  if (!Array.isArray(offers)) return [];
  return offers
    .map((offer: unknown) =>
      offerOf(offer && typeof offer === "object" && "key" in offer ? String(offer.key) : null),
    )
    .filter((choice): choice is OfferChoice => choice !== null);
}

/**
 * The form of a search to run it again: its name says what and where, its campaign the radius and
 * how many clients were asked for, and the offer profile it was scored with what was sold.
 */
export function draftOf(
  name: string | null,
  criteria: Pick<CampaignSaveRequest["criteria"], "geography" | "limits"> | undefined,
  offers: readonly OfferChoice[],
): ClientSearchDraft | null {
  const asked = parseSearchName(name);
  if (!asked) return null;
  const radius = criteria?.geography[0]?.radius_km ?? null;
  const wanted = WANTED.find((count) => candidateLimit(count) === criteria?.limits.max_candidates);
  return {
    ...asked,
    offers: [...offers],
    radiusKm: radius === DEFAULT_RADIUS_KM ? null : radius,
    wanted: wanted ?? 25,
  };
}

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

/** The simple filters of a list; the file downloads what they leave. */
export interface ClientFilters {
  phone: boolean;
  email: boolean;
  callFirst: boolean;
}

export const NO_FILTERS: ClientFilters = { phone: false, email: false, callFirst: false };

/** The search a list belongs to: the campaign that found its businesses. */
export interface SearchOf {
  campaign_id: string;
  campaign_name: string | null;
}

/**
 * The clients of one search in a score run: what its campaign found (D-099), priority or review,
 * not on the do-not-contact list, with a phone or email, best first (Latino evidence first when
 * the search asked for it). With `onlyFound`, every lead the campaign found whatever its tier, to
 * say how many were checked.
 */
export function clientListQuery(
  scoreRunId: string | null,
  search: SearchOf,
  filters: ClientFilters = NO_FILTERS,
  onlyFound = false,
): ExplorerQuery {
  const children: ExplorerCondition[] = [condition("campaign", "has", search.campaign_id)];
  if (!onlyFound) {
    children.push(
      condition("tier", "in", filters.callFirst ? ["priority"] : ["priority", "review"]),
      condition("suppressed", "is_false"),
      condition("usable_channels", "gt", 0),
    );
    if (filters.phone) children.push(condition("reachable_by", "has", "phone"));
    if (filters.email) children.push(condition("reachable_by", "has", "email"));
  }
  return {
    score_run_id: scoreRunId,
    filters: { kind: "group", join: "and", negate: false, children },
    search: null,
    sort: latinoFirst(search.campaign_name)
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
  "tier",
  "top_problem",
  "offer",
  "locality",
  "region",
  "domain",
  "total_score",
] as const;
export type ClientColumn = (typeof CLIENT_COLUMNS)[number];
type ColumnLabels = Partial<Record<ClientColumn, Record<string, string>>>;

const WORDED_TIERS = ["priority", "review", "low"] as const satisfies readonly LeadTier[];

/** A tier as the simple screens say it (whom to call first), when it has such words. */
export const tierKey = (tier: LeadTier): TranslationKey | null =>
  (WORDED_TIERS as readonly LeadTier[]).includes(tier)
    ? `clientList.tier.${tier as (typeof WORDED_TIERS)[number]}`
    : null;

/** An observed problem said as a reason to call; `no_website` never claims there is no site. */
export const whyKey = (problem: ProblemType): TranslationKey => `clientList.why.${problem}`;

/** Up to two observed problems, the one that weighed most first: why the client is worth a call. */
export const whyProblems = (top: ProblemType | null, problems: readonly ProblemType[]) =>
  [...new Set([...(top ? [top] : []), ...problems])].slice(0, 2);

/** The words a downloaded file writes instead of the codes of its columns (`ExportColumn.labels`). */
export function clientColumnLabels(t: Translator): ColumnLabels {
  const offers = Object.keys(OFFER_PROBLEMS) as OfferChoice[];
  return {
    tier: Object.fromEntries(WORDED_TIERS.map((tier) => [tier, t(`clientList.tier.${tier}`)])),
    top_problem: Object.fromEntries(
      PROBLEMS.map((problem) => {
        const why = t(whyKey(problem)); // a phrase on screen, a sentence in its own cell
        return [problem, why.charAt(0).toUpperCase() + why.slice(1)];
      }),
    ),
    offer: Object.fromEntries(
      offers.map((choice) => [`simple_${choice}`, t(`clientSearch.offer.${choice}`)]),
    ),
  };
}

/**
 * The file of a finished search: every client its query matches, not only the ones the screen
 * shows, each with the phone and email to reach them. Headers and words come from the page, so the
 * file speaks the language of the screen that asked for it.
 */
export function clientExportDraft(
  query: ExplorerQuery,
  header: (key: ClientColumn) => string,
  labels: ColumnLabels = {},
): ExportDraft {
  return {
    source_kind: "query",
    query,
    view_id: null,
    snapshot_id: null,
    format: "csv",
    columns: CLIENT_COLUMNS.map((key) => ({
      key,
      header: header(key),
      labels: labels[key] ?? null,
    })),
    // a spreadsheet reads the accents of these headers only with the byte-order mark
    bom: true,
    include_suppressed: false,
    suppressed_reason: null,
    preset_key: null,
    preset_version: null,
  };
}

export interface ClientContacts {
  phone: string | null;
  email: string | null;
}

/**
 * The phone and the email to show: of each kind, the published one checked furthest, the primary
 * before the rest, the oldest among equals. Never a guess from a pattern nor one marked do not
 * contact: the choice the downloaded file makes (`exports.best_contacts`).
 */
export function contactsOf(points: readonly ContactPoint[]): ClientContacts {
  const best = (kind: ContactType) =>
    points
      .filter(
        (point) =>
          point.contact_type === kind &&
          point.origin !== "hypothesis" &&
          !point.do_not_contact &&
          STATUS_RANK[point.validation_status] !== undefined,
      )
      .sort(
        (a, b) =>
          (STATUS_RANK[a.validation_status] ?? 0) - (STATUS_RANK[b.validation_status] ?? 0) ||
          Number(b.is_primary) - Number(a.is_primary) ||
          (a.id < b.id ? -1 : 1),
      )[0]?.value_raw ?? null;
  return { phone: best("phone"), email: best("email") };
}

/** The simple-search offer a lead was matched to (`simple_<choice>`), if it is one. */
export function offerOf(key: string | null): OfferChoice | null {
  const choice = key?.startsWith("simple_") ? key.slice("simple_".length) : null;
  return choice !== null && choice in OFFER_PROBLEMS ? (choice as OfferChoice) : null;
}

/** What a person records from the list: they got in touch, the client is interested, or not. */
export type ClientAction = "contacted" | "interested" | "not_interested";

const TOWARDS_INTEREST: readonly LeadStage[] = [
  "not_contacted",
  "attempted",
  "connected",
  "qualified",
];

/**
 * The stages a lead goes through to record an action, in the order the funnel allows them (an
 * interested client was reached first). Empty when the action no longer applies.
 */
export function stagesFor(action: ClientAction, stage: LeadStage): LeadStage[] {
  const at = TOWARDS_INTEREST.indexOf(stage);
  if (action === "contacted") return stage === "not_contacted" ? ["attempted"] : [];
  if (at < 0) return [];
  return action === "interested" ? TOWARDS_INTEREST.slice(at + 1) : ["not_interested"];
}

/** The audit reason of what the simple list records; the analyst's screens ask for one. */
export const LIST_REASON = "Registrado desde Tu lista de clientes";

export function outcomeRequest(
  scoreRunId: string,
  businessId: string,
  stage: LeadStage,
  channel: ContactType | null,
): LeadActionRequest {
  return {
    score_run_id: scoreRunId,
    business_id: businessId,
    action: "record_outcome",
    stage,
    lost_reason: null,
    reason: LIST_REASON,
    channel,
    attempt_result: null,
    value_amount: null,
    value_currency: null,
    contact_quality: null,
    notes: null,
    occurred_at: null,
  };
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
