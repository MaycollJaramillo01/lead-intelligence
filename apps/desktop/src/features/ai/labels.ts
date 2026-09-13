import type {
  AiActivity,
  AiCircuitState,
  AiDevice,
  AiProfile,
  AiReviewStatus,
  AiTaskType,
  AiUnavailableReason,
  InferenceStatus,
} from "@lead/shared-types";
import type { TranslationKey } from "@/lib/i18n";

export const TASKS: readonly AiTaskType[] = [
  "evidence_extraction",
  "business_classification",
  "finding_synthesis",
  "opportunity_reasoning",
  "language_detection",
  "visual_review",
];
export const PROFILES: readonly AiProfile[] = ["fast", "balanced", "quality"];
export const INFERENCE_STATUSES: readonly InferenceStatus[] = [
  "queued",
  "running",
  "succeeded",
  "insufficient_evidence",
  "invalid_output",
  "failed",
  "cancelled",
];
export const REVIEW_STATUSES: readonly AiReviewStatus[] = ["unreviewed", "accepted", "rejected"];

export const TASK_KEYS: Record<AiTaskType, TranslationKey> = {
  evidence_extraction: "ai.task.evidence_extraction",
  business_classification: "ai.task.business_classification",
  finding_synthesis: "ai.task.finding_synthesis",
  opportunity_reasoning: "ai.task.opportunity_reasoning",
  language_detection: "ai.task.language_detection",
  visual_review: "ai.task.visual_review",
};
export const PROFILE_KEYS: Record<AiProfile, TranslationKey> = {
  fast: "ai.profile.fast",
  balanced: "ai.profile.balanced",
  quality: "ai.profile.quality",
};
export const DEVICE_KEYS: Record<AiDevice, TranslationKey> = {
  gpu: "ai.device.gpu",
  partial: "ai.device.partial",
  cpu: "ai.device.cpu",
  unknown: "ai.device.unknown",
};
export const INFERENCE_STATUS_KEYS: Record<InferenceStatus, TranslationKey> = {
  queued: "ai.status.queued",
  running: "ai.status.running",
  succeeded: "ai.status.succeeded",
  insufficient_evidence: "ai.status.insufficient_evidence",
  invalid_output: "ai.status.invalid_output",
  failed: "ai.status.failed",
  cancelled: "ai.status.cancelled",
};
export const REVIEW_KEYS: Record<AiReviewStatus, TranslationKey> = {
  unreviewed: "ai.review.unreviewed",
  accepted: "ai.review.accepted",
  rejected: "ai.review.rejected",
};
export const REASON_KEYS: Record<AiUnavailableReason, TranslationKey> = {
  task_disabled: "ai.reason.task_disabled",
  no_model_assigned: "ai.reason.no_model_assigned",
  runtime_unavailable: "ai.reason.runtime_unavailable",
  model_not_installed: "ai.reason.model_not_installed",
  no_vision_model: "ai.reason.no_vision_model",
  circuit_open: "ai.reason.circuit_open",
};
export const CIRCUIT_KEYS: Record<AiCircuitState, TranslationKey> = {
  closed: "ai.circuit.closed",
  open: "ai.circuit.open",
  half_open: "ai.circuit.half_open",
};
export const ACTIVITY_KEYS: Record<AiActivity, TranslationKey> = {
  idle: "ai.activity.idle",
  running: "ai.activity.running",
  succeeded: "ai.activity.succeeded",
  failed: "ai.activity.failed",
  cancelled: "ai.activity.cancelled",
};

export const STATUS_TONE: Record<InferenceStatus, string> = {
  queued: "text-muted-foreground",
  running: "text-info",
  succeeded: "text-ok",
  insufficient_evidence: "text-neutral",
  invalid_output: "text-warn",
  failed: "text-danger",
  cancelled: "text-muted-foreground",
};

export function megabytes(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${value.toLocaleString("en-US")} MiB`;
}

export function gigabytes(bytes: number | null | undefined): string {
  return bytes === null || bytes === undefined ? "—" : `${(bytes / 1024 ** 3).toFixed(2)} GiB`;
}
