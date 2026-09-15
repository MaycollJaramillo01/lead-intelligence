import type { CampaignListItem, DiscoveryCriteria } from "@lead/shared-types";
import { emptyCriteria } from "@/features/discovery/criteria";

/** What the builder edits. `campaignId` is null for a new campaign and for a copy of one. */
export interface CampaignDraft {
  campaignId: string | null;
  projectId: string | null;
  name: string;
  objective: string;
  criteria: DiscoveryCriteria;
}

export function newDraft(): CampaignDraft {
  const criteria = emptyCriteria();
  // one place ready to fill in: most campaigns search one US state or city
  criteria.geography = [
    {
      country_code: "US",
      region: null,
      metro: null,
      locality: null,
      postal_code: null,
      radius_km: null,
    },
  ];
  return { campaignId: null, projectId: null, name: "", objective: "", criteria };
}

export function draftFrom(campaign: CampaignListItem): CampaignDraft {
  return {
    campaignId: campaign.id,
    projectId: campaign.project_id,
    name: campaign.name,
    objective: campaign.objective ?? "",
    criteria: campaign.criteria,
  };
}

/** A copy carries the criteria but none of the history: it is a new campaign from the start. */
export function copyOf(campaign: CampaignListItem, suffix: string): CampaignDraft {
  return {
    ...draftFrom(campaign),
    campaignId: null,
    name: `${campaign.name} ${suffix}`.slice(0, 300),
  };
}
