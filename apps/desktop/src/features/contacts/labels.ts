import type {
  ContactAction,
  ContactFreshness,
  ContactLevel,
  ContactOrigin,
  ContactRole,
  ContactType,
  DecisionMakerRelationship,
  DecisionMakerReview,
  SuppressionKind,
  ValidationStatus,
} from "@lead/shared-types";
import { known } from "@/features/resolution/labels";
import type { TranslationKey, Translator } from "@/lib/i18n";

export const CONTACT_TYPES: readonly ContactType[] = [
  "phone",
  "email",
  "contact_form",
  "booking_form",
  "social_profile",
];
/** From what independent evidence supports down to what makes a channel unusable. */
export const VALIDATION_STATUSES: readonly ValidationStatus[] = [
  "likely_valid",
  "domain_valid",
  "structurally_valid",
  "observed",
  "unknown",
  "stale",
  "invalid",
  "suppressed",
];
export const ROLES: readonly ContactRole[] = ["general", "department", "role_address", "personal"];
export const FRESHNESS: readonly ContactFreshness[] = ["fresh", "aging", "stale"];
export const SUPPRESSION_KINDS: readonly SuppressionKind[] = [
  "email",
  "phone",
  "domain",
  "business",
];

export const CONTACT_TYPE_KEYS: Record<ContactType, TranslationKey> = {
  phone: "contactType.phone",
  email: "contactType.email",
  contact_form: "contactType.contact_form",
  booking_form: "contactType.booking_form",
  social_profile: "contactType.social_profile",
};

export const STATUS_KEYS: Record<ValidationStatus, TranslationKey> = {
  observed: "validationStatus.observed",
  structurally_valid: "validationStatus.structurally_valid",
  domain_valid: "validationStatus.domain_valid",
  likely_valid: "validationStatus.likely_valid",
  invalid: "validationStatus.invalid",
  unknown: "validationStatus.unknown",
  stale: "validationStatus.stale",
  suppressed: "validationStatus.suppressed",
};

/** A status reads in words first; the border only repeats it. Nothing is green: likely valid is
 * not a promise that anyone answers. */
export const STATUS_TONE: Record<ValidationStatus, string> = {
  likely_valid: "",
  domain_valid: "",
  structurally_valid: "",
  observed: "text-muted-foreground",
  unknown: "border-dashed text-muted-foreground",
  stale: "border-warn/60 text-warn",
  invalid: "border-danger/60 text-danger",
  suppressed: "border-danger/60 text-danger",
};

export const ROLE_KEYS: Record<ContactRole, TranslationKey> = {
  general: "contactRole.general",
  department: "contactRole.department",
  role_address: "contactRole.role_address",
  personal: "contactRole.personal",
};

export const LEVEL_KEYS: Record<ContactLevel, TranslationKey> = {
  business: "contactLevel.business",
  location: "contactLevel.location",
  person: "contactLevel.person",
};

export const FRESHNESS_KEYS: Record<ContactFreshness, TranslationKey> = {
  fresh: "contactFreshness.fresh",
  aging: "contactFreshness.aging",
  stale: "contactFreshness.stale",
};

export const ORIGIN_KEYS: Record<ContactOrigin, TranslationKey> = {
  published: "contactOrigin.published",
  user_import: "contactOrigin.user_import",
  hypothesis: "contactOrigin.hypothesis",
};

export const SUPPRESSION_KIND_KEYS: Record<SuppressionKind, TranslationKey> = {
  email: "suppressionKind.email",
  phone: "suppressionKind.phone",
  domain: "suppressionKind.domain",
  business: "suppressionKind.business",
};

export const REVIEW_KEYS: Record<DecisionMakerReview, TranslationKey> = {
  unreviewed: "personReview.unreviewed",
  ambiguous: "personReview.ambiguous",
  confirmed: "personReview.confirmed",
  rejected: "personReview.rejected",
};

export const RELATIONSHIP_KEYS: Record<DecisionMakerRelationship, TranslationKey> = {
  owner: "personRelationship.owner",
  founder: "personRelationship.founder",
  executive: "personRelationship.executive",
  partner: "personRelationship.partner",
  manager: "personRelationship.manager",
  unknown: "personRelationship.unknown",
};

export const ACTION_KEYS: Record<ContactAction, TranslationKey> = {
  validate: "contacts.action.validate",
  confirm: "contacts.action.confirm",
  mark_invalid: "contacts.action.mark_invalid",
  clear_review: "contacts.action.clear_review",
  mark_primary: "contacts.action.mark_primary",
  suppress: "contacts.action.suppress",
};

export const signalLabel = (t: Translator, code: string) => known(t, "contactSignal", code);
export const methodLabel = (t: Translator, code: string) => known(t, "contactMethod", code);
export const personReasonLabel = (t: Translator, code: string) => known(t, "personReason", code);
export const conflictLabel = (t: Translator, code: string) => known(t, "contactConflict", code);
export const componentLabel = (t: Translator, code: string) => known(t, "contactComponent", code);
export const gateLabel = (t: Translator, code: string) => known(t, "contactGate", code);
export const decisionLabel = (t: Translator, code: string) => known(t, "contactDecision", code);
