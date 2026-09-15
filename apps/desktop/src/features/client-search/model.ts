import type {
  CampaignSaveRequest,
  IcpDefinition,
  IcpSaveRequest,
  PipelineStage,
  PipelineStageView,
  PipelineStartRequest,
  ProblemType,
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
      radius_km: draft.radiusKm,
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
  return `Buscar clientes · ${type.id} · ${draft.locality.trim()} · ${draft.region}`;
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
      latino: draft.latinoOnly ? "market_affinity" : "none",
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
  review_export_eligibility: "clientSearch.progress.choose",
};

export function visibleProgress(stages: PipelineStageView[]): PipelineStageView[] {
  return stages.filter(
    (stage, index) =>
      stage.stage !== "entity_resolution" || stages[index - 1]?.stage !== "normalization",
  );
}
