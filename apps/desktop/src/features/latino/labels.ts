import type {
  AssessmentMethod,
  EvidenceFreshness,
  EvidenceStatus,
  EvidenceStrength,
  LatinoReviewFilter,
  LatinoStatus,
  LatinoTrack,
  SourceKind,
} from "@lead/shared-types";
import { known } from "@/features/resolution/labels";
import type { TranslationKey, Translator } from "@/lib/i18n";

export const LATINO_STATUSES: readonly LatinoStatus[] = [
  "verified_latino_owned",
  "high_confidence_latino_owned",
  "probable_latino_owned",
  "spanish_market_affinity",
  "unknown",
];
/** Statuses that say who owns a business. Market affinity is not one of them. */
export const OWNERSHIP_STATUSES: ReadonlySet<LatinoStatus> = new Set<LatinoStatus>([
  "verified_latino_owned",
  "high_confidence_latino_owned",
  "probable_latino_owned",
]);
export const LATINO_TRACKS: readonly LatinoTrack[] = ["ownership", "contradiction", "market"];
export const REVIEW_FILTERS: readonly LatinoReviewFilter[] = ["reviewed", "unreviewed", "outdated"];
export const SOURCE_KINDS: readonly SourceKind[] = [
  "public_registry",
  "official_api",
  "platform_api",
  "directory",
  "business_website",
  "public_dataset",
  "file_import",
  "web_search",
  "manual_seed",
  "synthetic",
];

export const LATINO_STATUS_KEYS: Record<LatinoStatus, TranslationKey> = {
  verified_latino_owned: "latinoStatus.verified_latino_owned",
  high_confidence_latino_owned: "latinoStatus.high_confidence_latino_owned",
  probable_latino_owned: "latinoStatus.probable_latino_owned",
  spanish_market_affinity: "latinoStatus.spanish_market_affinity",
  unknown: "latinoStatus.unknown",
};

export const TRACK_KEYS: Record<LatinoTrack, TranslationKey> = {
  ownership: "latinoTrack.ownership",
  contradiction: "latinoTrack.contradiction",
  market: "latinoTrack.market",
};

export const EVIDENCE_STATUS_KEYS: Record<EvidenceStatus, TranslationKey> = {
  active: "evidenceStatus.active",
  stale: "evidenceStatus.stale",
  contradicted: "evidenceStatus.contradicted",
  revoked: "evidenceStatus.revoked",
};

export const STRENGTH_KEYS: Record<EvidenceStrength, TranslationKey> = {
  strong: "strength.strong",
  moderate: "strength.moderate",
  weak: "strength.weak",
};

export const FRESHNESS_KEYS: Record<EvidenceFreshness, TranslationKey> = {
  fresh: "freshness.fresh",
  aging: "freshness.aging",
  stale: "freshness.stale",
};

export const METHOD_KEYS: Record<AssessmentMethod, TranslationKey> = {
  rules: "latino.method.rules",
  manual_review: "latino.method.manual_review",
};

export const REVIEW_FILTER_KEYS: Record<LatinoReviewFilter, TranslationKey> = {
  reviewed: "latinoReview.reviewed",
  unreviewed: "latinoReview.unreviewed",
  outdated: "latinoReview.outdated",
};

export const SOURCE_KIND_KEYS: Record<SourceKind, TranslationKey> = {
  official_api: "sourceKind.official_api",
  public_dataset: "sourceKind.public_dataset",
  directory: "sourceKind.directory",
  public_registry: "sourceKind.public_registry",
  web_search: "sourceKind.web_search",
  platform_api: "sourceKind.platform_api",
  file_import: "sourceKind.file_import",
  manual_seed: "sourceKind.manual_seed",
  synthetic: "sourceKind.synthetic",
  business_website: "sourceKind.business_website",
};

export const claimLabel = (t: Translator, code: string) => known(t, "latinoClaim", code);
export const noteLabel = (t: Translator, code: string) => known(t, "latinoNote", code);
export const limitLabel = (t: Translator, code: string) => known(t, "latinoLimit", code);
export const latinoDecisionLabel = (t: Translator, code: string) =>
  known(t, "latino.decision", code);
