import type {
  FieldAgreement,
  MatchAction,
  MatchOutcome,
  MatchRisk,
  MatchStatus,
} from "@lead/shared-types";
import type { TranslationKey, Translator } from "@/lib/i18n";
import { en } from "@/lib/locales/en";

export const MATCH_STATUSES: readonly MatchStatus[] = ["pending", "deferred", "merged", "separate"];
export const MATCH_RISKS: readonly MatchRisk[] = ["low", "medium", "high"];
export const MATCH_ACTIONS: readonly MatchAction[] = ["merge", "keep_separate", "defer", "undo"];

export const STATUS_KEYS: Record<MatchStatus, TranslationKey> = {
  pending: "matchStatus.pending",
  deferred: "matchStatus.deferred",
  merged: "matchStatus.merged",
  separate: "matchStatus.separate",
};

export const RISK_KEYS: Record<MatchRisk, TranslationKey> = {
  low: "matchRisk.low",
  medium: "matchRisk.medium",
  high: "matchRisk.high",
};

export const OUTCOME_KEYS: Record<MatchOutcome, TranslationKey> = {
  auto_merge: "matchOutcome.auto_merge",
  manual_review: "matchOutcome.manual_review",
  distinct: "matchOutcome.distinct",
};

export const AGREEMENT_KEYS: Record<FieldAgreement, TranslationKey> = {
  match: "agreement.match",
  partial: "agreement.partial",
  conflict: "agreement.conflict",
  missing: "agreement.missing",
};

export const ACTION_KEYS: Record<MatchAction, TranslationKey> = {
  merge: "resolution.action.merge",
  keep_separate: "resolution.action.keep_separate",
  defer: "resolution.action.defer",
  undo: "resolution.action.undo",
};

/** A code in words when this build knows it; an unknown code is shown as it is, not hidden. */
export function known(t: Translator, prefix: string, code: string): string {
  const key = `${prefix}.${code}`;
  return key in en ? t(key as TranslationKey) : code;
}

export const reasonLabel = (t: Translator, code: string) => known(t, "resolutionReason", code);
export const fieldLabel = (t: Translator, field: string) => known(t, "comparisonField", field);
export const decisionLabel = (t: Translator, decision: string) =>
  known(t, "resolution.action", decision);
