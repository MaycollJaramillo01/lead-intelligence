import type {
  ConversionElementKind,
  WebsiteAuditStatus,
  WebsiteCategory,
  WebsiteFindingResult,
  WebsiteFindingSeverity,
} from "@lead/shared-types";
import { known } from "@/features/resolution/labels";
import type { TranslationKey, Translator } from "@/lib/i18n";

export const CATEGORIES: readonly WebsiteCategory[] = [
  "technical",
  "seo",
  "local_seo",
  "content",
  "conversion",
  "mobile",
  "accessibility",
  "measurement",
  "aeo_geo_readiness",
];
export const SEVERITIES: readonly WebsiteFindingSeverity[] = [
  "critical",
  "high",
  "medium",
  "low",
  "info",
];
/** Actionable results first, as the audit lists them. */
export const RESULTS: readonly WebsiteFindingResult[] = [
  "fail",
  "warning",
  "unknown",
  "pass",
  "not_applicable",
];
export const ACTIONABLE: ReadonlySet<WebsiteFindingResult> = new Set<WebsiteFindingResult>([
  "fail",
  "warning",
]);

export const CATEGORY_KEYS: Record<WebsiteCategory, TranslationKey> = {
  technical: "websiteCategory.technical",
  seo: "websiteCategory.seo",
  local_seo: "websiteCategory.local_seo",
  content: "websiteCategory.content",
  conversion: "websiteCategory.conversion",
  mobile: "websiteCategory.mobile",
  accessibility: "websiteCategory.accessibility",
  measurement: "websiteCategory.measurement",
  aeo_geo_readiness: "websiteCategory.aeo_geo_readiness",
};

export const RESULT_KEYS: Record<WebsiteFindingResult, TranslationKey> = {
  pass: "websiteResult.pass",
  fail: "websiteResult.fail",
  warning: "websiteResult.warning",
  unknown: "websiteResult.unknown",
  not_applicable: "websiteResult.not_applicable",
};

/** A result reads in words first; the border only repeats it, and unknown is never red. */
export const RESULT_TONE: Record<WebsiteFindingResult, string> = {
  fail: "border-danger/60 text-danger",
  warning: "border-warn/60 text-warn",
  unknown: "border-dashed text-muted-foreground",
  pass: "text-muted-foreground",
  not_applicable: "text-muted-foreground",
};

export const SEVERITY_KEYS: Record<WebsiteFindingSeverity, TranslationKey> = {
  critical: "websiteSeverity.critical",
  high: "websiteSeverity.high",
  medium: "websiteSeverity.medium",
  low: "websiteSeverity.low",
  info: "websiteSeverity.info",
};

export const AUDIT_STATUS_KEYS: Record<WebsiteAuditStatus, TranslationKey> = {
  complete: "websiteAuditStatus.complete",
  partial: "websiteAuditStatus.partial",
  empty: "websiteAuditStatus.empty",
};

export const ELEMENT_KEYS: Record<ConversionElementKind, TranslationKey> = {
  primary_cta: "websiteElement.primary_cta",
  secondary_cta: "websiteElement.secondary_cta",
  contact_form: "websiteElement.contact_form",
  booking_widget: "websiteElement.booking_widget",
  chat_widget: "websiteElement.chat_widget",
  click_to_call: "websiteElement.click_to_call",
  messaging_link: "websiteElement.messaging_link",
  other: "websiteElement.other",
};

export const gapLabel = (t: Translator, code: string) => known(t, "websiteGap", code);
export const noteLabel = (t: Translator, code: string) => known(t, "websiteNote", code);
export const deviceLabel = (t: Translator, code: string) => known(t, "websiteDevice", code);
export const technologyCategoryLabel = (t: Translator, code: string) =>
  known(t, "websiteTechnology", code);
