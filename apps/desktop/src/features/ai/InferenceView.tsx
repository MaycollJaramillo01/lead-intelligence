import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import type {
  AiInferenceDetail,
  AiInputRef,
  AiReviewDecision,
  InferenceStatus,
} from "@lead/shared-types";
import { toast } from "sonner";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  DEVICE_KEYS,
  INFERENCE_STATUS_KEYS,
  PROFILE_KEYS,
  REVIEW_KEYS,
  STATUS_TONE,
  TASK_KEYS,
} from "@/features/ai/labels";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop, toIpcError, type IpcError } from "@/lib/ipc";
import { cn } from "@/lib/utils";

type DetailState = { detail: AiInferenceDetail; error: null } | { detail: null; error: IpcError };

const DECISIONS: readonly AiReviewDecision[] = ["accept", "reject", "reset"];
const CITATION_KEYS = new Set(["evidence_ids", "vertical_evidence_ids", "evidence_id"]);

function Heading({ children }: { children: ReactNode }) {
  return (
    <h4 className="text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
      {children}
    </h4>
  );
}

/** An inference status in words, with its stable code beside it. */
export function InferenceStatusBadge({ status }: { status: InferenceStatus }) {
  const t = useT();
  return (
    <span className={cn("inline-flex items-center gap-1 rounded border px-1", STATUS_TONE[status])}>
      {t(INFERENCE_STATUS_KEYS[status])}
      <span className="font-mono text-[10px] opacity-80">{status}</span>
    </span>
  );
}

/** A cited input: its id, and the exact text the model saw on hover and focus. */
function Citation({ id, inputs }: { id: string; inputs: Map<string, AiInputRef> }) {
  const input = inputs.get(id);
  return (
    <abbr
      title={input?.text ?? id}
      className="rounded border border-info/40 px-1 font-mono text-[10px] text-info no-underline"
    >
      {id}
    </abbr>
  );
}

/** Renders a validated answer generically: fields as rows, citations as chips. */
function OutputValue({ value, inputs }: { value: unknown; inputs: Map<string, AiInputRef> }) {
  if (value === null || value === undefined)
    return <span className="text-muted-foreground">—</span>;
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-muted-foreground">—</span>;
    return (
      <ul className="space-y-1">
        {value.map((item, index) => (
          <li key={index} className="rounded border p-1">
            <OutputValue value={item} inputs={inputs} />
          </li>
        ))}
      </ul>
    );
  }
  if (typeof value === "object") {
    return (
      <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5">
        {Object.entries(value as Record<string, unknown>).map(([key, item]) => (
          <div key={key} className="contents">
            <dt className="text-muted-foreground">{key}</dt>
            <dd className="min-w-0 break-words">
              {CITATION_KEYS.has(key) ? (
                <span className="flex flex-wrap gap-1">
                  {(Array.isArray(item) ? item : [item]).map((id) => (
                    <Citation key={String(id)} id={String(id)} inputs={inputs} />
                  ))}
                </span>
              ) : (
                <OutputValue value={item} inputs={inputs} />
              )}
            </dd>
          </div>
        ))}
      </dl>
    );
  }
  return <span>{String(value)}</span>;
}

/**
 * One local-model inference (phase 10): the answer as the model gave it and as it validated, every
 * input it cites with the exact text it saw, how it ran, and a person's review. The answer is never
 * presented as a fact: accepting it records that someone checked it against its inputs.
 */
export function InferenceView({ inferenceId }: { inferenceId: string }) {
  const t = useT();
  const language = useLanguage();
  const [state, setState] = useState<DetailState | null>(null);
  const [decision, setDecision] = useState<AiReviewDecision | "">("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let current = true;
    desktop.getAiInference({ inference_run_id: inferenceId }).then(
      (detail) => current && setState({ detail, error: null }),
      (cause: unknown) =>
        current && setState({ detail: null, error: toIpcError(cause, "get_ai_inference") }),
    );
    return () => {
      current = false;
    };
  }, [inferenceId]);

  if (state === null) return <StateBlock variant="loading" title={t("state.loading")} />;
  if (state.error !== null) {
    return (
      <StateBlock variant="error" title={t("ai.detailErrorTitle")} body={state.error.message} />
    );
  }
  const { detail } = state;
  const { run } = detail;
  const inputs = new Map(detail.inputs.map((input) => [input.ref, input]));
  const reviewable = run.status === "succeeded" || run.status === "insufficient_evidence";
  const canSubmit = !busy && reviewable && decision !== "" && reason.trim() !== "";

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    try {
      const updated = await desktop.reviewAiInference({
        inference_run_id: run.id,
        decision,
        reason: reason.trim(),
      });
      setState({ detail: updated, error: null });
      setDecision("");
      setReason("");
      toast.success(t("ai.reviewDone"));
    } catch (cause) {
      toast.error(t("ai.reviewFailed"), {
        description: toIpcError(cause, "review_ai_inference").message,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-label={t("ai.inferenceTitle")} className="space-y-3 text-xs">
      <Heading>{t("ai.inferenceTitle")}</Heading>
      <InlineNotice variant="partial">{t("ai.notAFact")}</InlineNotice>
      <div className="space-y-1 rounded-md border p-2">
        <p className="flex flex-wrap items-center gap-1.5">
          <span className="font-medium">{t(TASK_KEYS[run.task_type])}</span>
          <InferenceStatusBadge status={run.status} />
          <span className="rounded border px-1">{t(REVIEW_KEYS[run.review_status])}</span>
        </p>
        {detail.business_name && <p>{detail.business_name}</p>}
        <p className="text-[11px] text-muted-foreground">
          {t("ai.runLine", {
            model: run.model,
            profile: t(PROFILE_KEYS[run.profile]),
            device: t(DEVICE_KEYS[run.device]),
            attempts: run.attempts,
            duration: run.duration_ms ?? "—",
            tokens: `${run.prompt_tokens ?? "—"} / ${run.completion_tokens ?? "—"}`,
          })}
        </p>
        <p className="text-[11px] text-muted-foreground">
          {t("ai.versionsLine", {
            task: run.task_version,
            template: `${run.prompt_template_id} ${run.prompt_template_version}`,
            schema: run.output_schema_version,
            time: formatDateTime(run.created_at, language),
          })}
        </p>
        {run.error_code && (
          <p className="text-[11px] text-danger">
            {run.error_code}
            {run.error_message ? `: ${run.error_message}` : ""}
          </p>
        )}
        <details className="text-[11px] text-muted-foreground">
          <summary className="cursor-pointer">{t("ai.parameters")}</summary>
          <pre className="mt-1 overflow-auto rounded bg-muted p-1 font-mono text-[10px]">
            {JSON.stringify({ ...run.parameters_json, digest: run.model_digest }, null, 2)}
          </pre>
          <p className="mt-1 break-all font-mono">
            {t("ai.hashes", { input: run.input_sha256, cache: run.cache_key })}
          </p>
        </details>
      </div>

      {detail.warnings.length > 0 && (
        <ul className="space-y-0.5 text-[11px] text-warn">
          {detail.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}

      <div className="space-y-1">
        <Heading>{t("ai.inputs", { n: detail.inputs.length })}</Heading>
        <ul className="space-y-1">
          {detail.inputs.map((input) => (
            <li key={input.ref} className="rounded border p-1.5">
              <span className="mr-1 font-mono text-info">{input.ref}</span>
              <span className="text-muted-foreground">{input.kind}</span>
              <span className="block break-words">{input.text}</span>
              {input.source && (
                <span className="block break-all text-[10px] text-muted-foreground">
                  {input.source}
                </span>
              )}
            </li>
          ))}
        </ul>
      </div>

      <div className="space-y-1">
        <Heading>{t("ai.answers", { n: detail.outputs.length })}</Heading>
        {detail.outputs.length === 0 && (
          <p className="text-muted-foreground">{t("ai.noAnswers")}</p>
        )}
        {detail.outputs.map((output) => (
          <div key={output.id} className="space-y-1 rounded-md border p-2">
            <p className="text-[11px] text-muted-foreground">
              {t("ai.attempt", { n: output.attempt })}
            </p>
            {output.validated_output_json ? (
              <OutputValue value={output.validated_output_json} inputs={inputs} />
            ) : (
              <ul className="text-[11px] text-warn">
                {(Array.isArray(output.validation_errors_json?.errors)
                  ? output.validation_errors_json.errors
                  : []
                ).map((error) => (
                  <li key={String(error)}>{String(error)}</li>
                ))}
              </ul>
            )}
            <details className="text-[11px]">
              <summary className="cursor-pointer text-muted-foreground">
                {t("ai.rawOutput")}
              </summary>
              <pre className="mt-1 max-h-60 overflow-auto rounded bg-muted p-1 font-mono text-[10px] whitespace-pre-wrap">
                {output.raw_output}
              </pre>
            </details>
          </div>
        ))}
      </div>

      {reviewable && (
        <form onSubmit={(event) => void submit(event)} className="space-y-2 rounded-md border p-2">
          <Heading>{t("ai.reviewTitle")}</Heading>
          <p className="text-[11px] text-muted-foreground">{t("ai.reviewExplain")}</p>
          <label className="flex items-center gap-1.5">
            {t("ai.decision")}
            <select
              value={decision}
              onChange={(event) => setDecision(event.target.value as AiReviewDecision | "")}
              className="h-7 rounded-md border bg-transparent px-2 text-xs"
            >
              <option value="">—</option>
              {DECISIONS.map((value) => (
                <option key={value} value={value}>
                  {t(`ai.decision.${value}`)}
                </option>
              ))}
            </select>
          </label>
          <Label htmlFor={`ai-review-${run.id}`}>{t("ai.reason")}</Label>
          <Textarea
            id={`ai-review-${run.id}`}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            rows={2}
            maxLength={2000}
          />
          <Button type="submit" size="sm" className="h-7" disabled={!canSubmit}>
            {t("ai.saveReview")}
          </Button>
        </form>
      )}

      {detail.decisions.length > 0 && (
        <ul className="space-y-0.5 text-[11px] text-muted-foreground">
          {detail.decisions.map((item) => (
            <li key={item.id}>
              {t("ai.decisionLine", {
                decision: item.decision,
                actor: item.actor,
                reason: item.reason,
                time: formatDateTime(item.decided_at, language),
              })}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
