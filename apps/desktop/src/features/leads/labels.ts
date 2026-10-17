import type {
  BriefSection,
  BulkAction,
  CanonicalField,
  EvidenceRefKind,
  ExplorerField,
  ExplorerOperator,
  ExplorerRow,
  RefreshScope,
  ResearchStatus,
  ResearchTaskStatus,
  SalesReadyCriterion,
  TimelineKind,
} from "@lead/shared-types";
import { STATUS_KEYS } from "@/features/contacts/labels";
import { LATINO_STATUS_KEYS } from "@/features/latino/labels";
import {
  CHANNEL_KEYS,
  PROBLEM_KEYS,
  STAGE_KEYS,
  TIER_KEYS,
  percent,
  scoreText,
} from "@/features/scoring/labels";
import { AUDIT_STATUS_KEYS } from "@/features/website/labels";
import type { TranslationKey, Translator } from "@/lib/i18n";
import { en } from "@/lib/locales/en";

export const FIELD_KEYS: Record<ExplorerField, TranslationKey> = {
  name: "explorer.field.name",
  locality: "explorer.field.locality",
  region: "explorer.field.region",
  category: "explorer.field.category",
  domain: "explorer.field.domain",
  lead_stage: "explorer.field.lead_stage",
  research_status: "explorer.field.research_status",
  assignee: "explorer.field.assignee",
  tag: "explorer.field.tag",
  tier: "explorer.field.tier",
  rank: "explorer.field.rank",
  total_score: "explorer.field.total_score",
  confidence: "explorer.field.confidence",
  data_coverage: "explorer.field.data_coverage",
  icp_match: "explorer.field.icp_match",
  opportunity_score: "explorer.field.opportunity_score",
  digital_gap: "explorer.field.digital_gap",
  business_health: "explorer.field.business_health",
  buying_capacity: "explorer.field.buying_capacity",
  contactability: "explorer.field.contactability",
  commercial_intent: "explorer.field.commercial_intent",
  data_confidence: "explorer.field.data_confidence",
  latino_status: "explorer.field.latino_status",
  latino_confidence: "explorer.field.latino_confidence",
  main_channel: "explorer.field.main_channel",
  channel_status: "explorer.field.channel_status",
  usable_channels: "explorer.field.usable_channels",
  problem: "explorer.field.problem",
  top_problem: "explorer.field.top_problem",
  offer: "explorer.field.offer",
  facts_age_days: "explorer.field.facts_age_days",
  audit_status: "explorer.field.audit_status",
  conflicts: "explorer.field.conflicts",
  suppressed: "explorer.field.suppressed",
  stale: "explorer.field.stale",
  partial: "explorer.field.partial",
  campaign: "explorer.field.campaign",
  reachable_by: "explorer.field.reachable_by",
  repeated: "explorer.field.repeated",
};

export const OPERATOR_KEYS: Record<ExplorerOperator, TranslationKey> = {
  eq: "explorer.op.eq",
  neq: "explorer.op.neq",
  in: "explorer.op.in",
  not_in: "explorer.op.not_in",
  lt: "explorer.op.lt",
  lte: "explorer.op.lte",
  gt: "explorer.op.gt",
  gte: "explorer.op.gte",
  contains: "explorer.op.contains",
  starts_with: "explorer.op.starts_with",
  is_null: "explorer.op.is_null",
  is_not_null: "explorer.op.is_not_null",
  is_true: "explorer.op.is_true",
  is_false: "explorer.op.is_false",
  has: "explorer.op.has",
  has_not: "explorer.op.has_not",
};

export const RESEARCH_STATUSES: readonly ResearchStatus[] = [
  "sales_ready",
  "needs_research",
  "disqualified",
];
export const RESEARCH_STATUS_KEYS: Record<ResearchStatus, TranslationKey> = {
  sales_ready: "research.status.sales_ready",
  needs_research: "research.status.needs_research",
  disqualified: "research.status.disqualified",
};

export const CRITERION_KEYS: Record<SalesReadyCriterion, TranslationKey> = {
  not_suppressed: "research.criterion.not_suppressed",
  active_business: "research.criterion.active_business",
  scored_recently: "research.criterion.scored_recently",
  qualifying_tier: "research.criterion.qualifying_tier",
  verified_opportunity: "research.criterion.verified_opportunity",
  usable_contact: "research.criterion.usable_contact",
  contact_conflicts_resolved: "research.criterion.contact_conflicts_resolved",
  checklist_complete: "research.criterion.checklist_complete",
};

export const TASK_STATUS_KEYS: Record<ResearchTaskStatus, TranslationKey> = {
  open: "research.task.open",
  done: "research.task.done",
  dismissed: "research.task.dismissed",
};

export const REFRESH_SCOPES: readonly RefreshScope[] = ["pages", "contacts", "evidence"];
export const REFRESH_SCOPE_KEYS: Record<RefreshScope, TranslationKey> = {
  pages: "research.scope.pages",
  contacts: "research.scope.contacts",
  evidence: "research.scope.evidence",
};

export const CORRECTABLE_FIELDS: readonly CanonicalField[] = [
  "name",
  "location",
  "website",
  "phone",
  "email",
  "category",
];
export const CORRECTABLE_FIELD_KEYS: Record<CanonicalField, TranslationKey> = {
  name: "research.field.name",
  location: "research.field.location",
  website: "research.field.website",
  phone: "research.field.phone",
  email: "research.field.email",
  category: "research.field.category",
};

export const BULK_ACTIONS: readonly BulkAction[] = [
  "add_tag",
  "remove_tag",
  "assign",
  "unassign",
  "needs_research",
  "disqualify",
];
export const BULK_ACTION_KEYS: Record<BulkAction, TranslationKey> = {
  add_tag: "bulk.action.add_tag",
  remove_tag: "bulk.action.remove_tag",
  assign: "bulk.action.assign",
  unassign: "bulk.action.unassign",
  needs_research: "bulk.action.needs_research",
  disqualify: "bulk.action.disqualify",
};

export const TIMELINE_KINDS: readonly TimelineKind[] = [
  "source",
  "change",
  "analysis",
  "review",
  "research",
  "outcome",
];
export const TIMELINE_KIND_KEYS: Record<TimelineKind, TranslationKey> = {
  source: "timeline.kind.source",
  change: "timeline.kind.change",
  analysis: "timeline.kind.analysis",
  review: "timeline.kind.review",
  research: "timeline.kind.research",
  outcome: "timeline.kind.outcome",
};

export const BRIEF_SECTION_KEYS: Record<BriefSection, TranslationKey> = {
  business_snapshot: "brief.section.business_snapshot",
  icp_match: "brief.section.icp_match",
  verified_opportunity: "brief.section.verified_opportunity",
  evidence: "brief.section.evidence",
  recommended_offer: "brief.section.recommended_offer",
  contact_route: "brief.section.contact_route",
  decision_maker: "brief.section.decision_maker",
  conversation_hooks: "brief.section.conversation_hooks",
  risks_unknowns: "brief.section.risks_unknowns",
  last_verified: "brief.section.last_verified",
};

export const EVIDENCE_KIND_KEYS: Record<EvidenceRefKind, TranslationKey> = {
  business: "evidence.kind.business",
  evidence_item: "evidence.kind.evidence_item",
  website_audit: "evidence.kind.website_audit",
  website_finding: "evidence.kind.website_finding",
  website_technology: "evidence.kind.website_technology",
  contact_channel: "evidence.kind.contact_channel",
  decision_maker: "evidence.kind.decision_maker",
  latino_assessment: "evidence.kind.latino_assessment",
  inference_run: "evidence.kind.inference_run",
  score_run: "evidence.kind.score_run",
};

/** A code the worker names (why a section is empty, why a lead was skipped, a warning): its label
 * when this build has one, else the code itself, never hidden. */
export function codeText(
  t: Translator,
  prefix:
    | "brief.reason"
    | "bulk.skip"
    | "export.warning"
    | "research.matchedBy"
    | "pipeline.skipped"
    | "pipeline.pressure"
    | "snapshot.kept"
    | "snapshot.qualityError",
  code: string,
): string {
  const key = `${prefix}.${code}`;
  return key in en ? t(key as TranslationKey) : code;
}

/** Labels of the codes an enum or set field holds. */
const VALUE_KEYS: Partial<Record<ExplorerField, Record<string, TranslationKey>>> = {
  lead_stage: STAGE_KEYS,
  research_status: RESEARCH_STATUS_KEYS,
  tier: TIER_KEYS,
  latino_status: LATINO_STATUS_KEYS,
  main_channel: CHANNEL_KEYS,
  reachable_by: CHANNEL_KEYS,
  channel_status: STATUS_KEYS,
  problem: PROBLEM_KEYS,
  top_problem: PROBLEM_KEYS,
  audit_status: AUDIT_STATUS_KEYS,
};

/** A stored code of a field in words, or the code itself when this build has no label for it. */
export function valueText(t: Translator, field: ExplorerField, value: string): string {
  const key = VALUE_KEYS[field]?.[value];
  return key ? t(key) : value;
}

/** Components the scoring engine estimates from proxies: always shown labelled as estimates. */
export const ESTIMATE_FIELDS: ReadonlySet<ExplorerField> = new Set<ExplorerField>([
  "business_health",
  "buying_capacity",
]);

/** One field of a lead as the explorer shows it; null when the value is unknown. */
export function cellText(t: Translator, row: ExplorerRow, field: ExplorerField): string | null {
  switch (field) {
    case "name":
      return row.display_name;
    case "locality":
    case "region":
    case "category":
    case "domain":
    case "assignee":
    case "offer":
      return row[field];
    case "tag":
      return row.tags.length > 0 ? row.tags.join(", ") : null;
    case "lead_stage":
    case "tier":
    case "latino_status":
      return valueText(t, field, row[field]);
    case "research_status":
    case "main_channel":
    case "channel_status":
    case "top_problem":
    case "audit_status": {
      const value = row[field];
      return value === null ? null : valueText(t, field, value);
    }
    case "problem":
      return row.problems.length > 0
        ? row.problems.map((problem) => valueText(t, field, problem)).join(", ")
        : null;
    case "confidence":
    case "data_coverage":
    case "latino_confidence": {
      const value = row[field];
      return value === null ? null : percent(value);
    }
    case "total_score":
    case "icp_match":
    case "opportunity_score":
    case "digital_gap":
    case "business_health":
    case "buying_capacity":
    case "contactability":
    case "commercial_intent":
    case "data_confidence": {
      const value = row[field];
      return value === null ? null : scoreText(value);
    }
    case "rank":
      return row.rank === null ? null : String(row.rank);
    case "usable_channels":
    case "facts_age_days":
    case "conflicts":
      return String(row[field]);
    case "suppressed":
    case "stale":
    case "partial":
      return t(row[field] ? "common.yes" : "common.no");
    case "campaign":
    case "reachable_by":
    case "repeated":
      return null; // filters only: a lead shows neither (phase 16)
  }
}
