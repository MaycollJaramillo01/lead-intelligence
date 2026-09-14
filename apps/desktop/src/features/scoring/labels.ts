import type {
  AttemptResult,
  ContactQuality,
  ContactType,
  CriterionImportance,
  DigitalMaturity,
  FeatureState,
  GateOutcome,
  LatinoRequirement,
  LeadAction,
  LeadStage,
  LeadTier,
  ProblemType,
  ScoreComponent,
} from "@lead/shared-types";
import type { TranslationKey, Translator } from "@/lib/i18n";
import { en } from "@/lib/locales/en";

export const TIERS: readonly LeadTier[] = [
  "priority",
  "review",
  "low",
  "insufficient_data",
  "excluded",
];
export const TIER_KEYS: Record<LeadTier, TranslationKey> = {
  priority: "scoring.tier.priority",
  review: "scoring.tier.review",
  low: "scoring.tier.low",
  insufficient_data: "scoring.tier.insufficient_data",
  excluded: "scoring.tier.excluded",
};

export const PROBLEMS: readonly ProblemType[] = [
  "no_website",
  "website_unreachable",
  "weak_conversion_path",
  "no_online_booking",
  "weak_local_seo",
  "weak_technical_seo",
  "poor_mobile_experience",
  "measurement_not_detected",
];
export const PROBLEM_KEYS: Record<ProblemType, TranslationKey> = {
  no_website: "scoring.problem.no_website",
  website_unreachable: "scoring.problem.website_unreachable",
  weak_conversion_path: "scoring.problem.weak_conversion_path",
  no_online_booking: "scoring.problem.no_online_booking",
  weak_local_seo: "scoring.problem.weak_local_seo",
  weak_technical_seo: "scoring.problem.weak_technical_seo",
  poor_mobile_experience: "scoring.problem.poor_mobile_experience",
  measurement_not_detected: "scoring.problem.measurement_not_detected",
};

export const COMPONENT_KEYS: Record<ScoreComponent, TranslationKey> = {
  ICP_MATCH: "scoring.component.ICP_MATCH",
  LATINO_CONFIDENCE: "scoring.component.LATINO_CONFIDENCE",
  BUSINESS_HEALTH: "scoring.component.BUSINESS_HEALTH",
  DIGITAL_GAP: "scoring.component.DIGITAL_GAP",
  BUYING_CAPACITY: "scoring.component.BUYING_CAPACITY",
  CONTACTABILITY: "scoring.component.CONTACTABILITY",
  COMMERCIAL_INTENT: "scoring.component.COMMERCIAL_INTENT",
  DATA_CONFIDENCE: "scoring.component.DATA_CONFIDENCE",
  OPPORTUNITY_SCORE: "scoring.component.OPPORTUNITY_SCORE",
  FINAL_LEAD_SCORE: "scoring.component.FINAL_LEAD_SCORE",
};

export const IMPORTANCES: readonly CriterionImportance[] = ["must_have", "nice_to_have", "ignore"];
export const IMPORTANCE_KEYS: Record<CriterionImportance, TranslationKey> = {
  must_have: "scoring.importance.must_have",
  nice_to_have: "scoring.importance.nice_to_have",
  ignore: "scoring.importance.ignore",
};

export const LATINO_REQUIREMENTS: readonly LatinoRequirement[] = [
  "none",
  "market_affinity",
  "verified_latino_owned",
];
export const LATINO_REQUIREMENT_KEYS: Record<LatinoRequirement, TranslationKey> = {
  none: "scoring.latino.none",
  market_affinity: "scoring.latino.market_affinity",
  verified_latino_owned: "scoring.latino.verified_latino_owned",
};

export const MATURITIES: readonly DigitalMaturity[] = ["low", "medium", "high"];
export const MATURITY_KEYS: Record<DigitalMaturity, TranslationKey> = {
  low: "scoring.maturity.low",
  medium: "scoring.maturity.medium",
  high: "scoring.maturity.high",
};

export const CHANNEL_TYPES: readonly ContactType[] = [
  "phone",
  "email",
  "contact_form",
  "booking_form",
  "social_profile",
];
export const CHANNEL_KEYS: Record<ContactType, TranslationKey> = {
  phone: "scoring.channel.phone",
  email: "scoring.channel.email",
  contact_form: "scoring.channel.contact_form",
  booking_form: "scoring.channel.booking_form",
  social_profile: "scoring.channel.social_profile",
};

export const GATE_OUTCOME_KEYS: Record<GateOutcome, TranslationKey> = {
  pass: "scoring.outcome.pass",
  fail: "scoring.outcome.fail",
  unknown: "scoring.outcome.unknown",
};

export const FEATURE_STATE_KEYS: Record<FeatureState, TranslationKey> = {
  known: "scoring.state.known",
  unknown: "scoring.state.unknown",
  not_applicable: "scoring.state.not_applicable",
};

export const LEAD_STAGES: readonly LeadStage[] = [
  "not_contacted",
  "attempted",
  "connected",
  "qualified",
  "meeting_booked",
  "proposal_sent",
  "won",
  "lost",
  "invalid_contact",
  "not_interested",
];
export const STAGE_KEYS: Record<LeadStage, TranslationKey> = {
  not_contacted: "scoring.stage.not_contacted",
  attempted: "scoring.stage.attempted",
  connected: "scoring.stage.connected",
  qualified: "scoring.stage.qualified",
  meeting_booked: "scoring.stage.meeting_booked",
  proposal_sent: "scoring.stage.proposal_sent",
  won: "scoring.stage.won",
  lost: "scoring.stage.lost",
  invalid_contact: "scoring.stage.invalid_contact",
  not_interested: "scoring.stage.not_interested",
};

export const LEAD_ACTIONS: readonly LeadAction[] = [
  "confirm",
  "dismiss",
  "reset",
  "record_attempt",
  "record_outcome",
];
export const ACTION_KEYS: Record<LeadAction, TranslationKey> = {
  confirm: "scoring.action.confirm",
  dismiss: "scoring.action.dismiss",
  reset: "scoring.action.reset",
  record_outcome: "scoring.action.record_outcome",
  record_attempt: "scoring.action.record_attempt",
};

export const ATTEMPT_RESULTS: readonly AttemptResult[] = [
  "no_answer",
  "left_message",
  "reached",
  "replied",
  "bounced",
  "wrong_contact",
  "call_back_later",
  "meeting_set",
  "proposal_sent",
  "won",
  "lost",
];
export const ATTEMPT_RESULT_KEYS: Record<AttemptResult, TranslationKey> = {
  no_answer: "scoring.attempt.no_answer",
  left_message: "scoring.attempt.left_message",
  reached: "scoring.attempt.reached",
  replied: "scoring.attempt.replied",
  bounced: "scoring.attempt.bounced",
  wrong_contact: "scoring.attempt.wrong_contact",
  call_back_later: "scoring.attempt.call_back_later",
  meeting_set: "scoring.attempt.meeting_set",
  proposal_sent: "scoring.attempt.proposal_sent",
  won: "scoring.attempt.won",
  lost: "scoring.attempt.lost",
};

export const CONTACT_QUALITIES: readonly ContactQuality[] = [
  "decision_maker",
  "gatekeeper",
  "wrong_person",
  "unreachable",
  "unknown",
];
export const CONTACT_QUALITY_KEYS: Record<ContactQuality, TranslationKey> = {
  decision_maker: "scoring.quality.decision_maker",
  gatekeeper: "scoring.quality.gatekeeper",
  wrong_person: "scoring.quality.wrong_person",
  unreachable: "scoring.quality.unreachable",
  unknown: "scoring.quality.unknown",
};

/** A code the engine names (a gate, criterion, feature, risk, warning or decision): its label when
 * this build has one, else the code itself, never hidden. */
export function codeLabel(t: Translator, code: string): string {
  const key = `scoring.code.${code}`;
  return key in en ? t(key as TranslationKey) : code;
}

export const percent = (value: number) => `${Math.round(value * 100)}%`;

export const scoreText = (value: number | null) => (value === null ? "—" : value.toFixed(1));
