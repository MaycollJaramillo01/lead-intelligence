import { useCallback, useEffect, useState } from "react";
import type { AiTaskType } from "@lead/shared-types";
import { Sparkles } from "lucide-react";
import { toast } from "sonner";
import { InlineNotice } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { InferenceStatusBadge, InferenceView } from "@/features/ai/InferenceView";
import { REVIEW_KEYS, TASKS, TASK_KEYS } from "@/features/ai/labels";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { useLoaded } from "@/lib/useLoaded";
import { cn } from "@/lib/utils";

const POLL_MS = 2000;
const LISTED = 20;
const DEFAULT_TASKS: readonly AiTaskType[] = ["business_classification", "evidence_extraction"];

/**
 * Local AI for one business (phase 10): run tasks on its stored evidence and read what the model
 * answered, each answer next to the inputs it cites. Model output never replaces a deterministic
 * result here; it is shown as a model's answer, to be reviewed.
 */
export function AiPanel({ businessId }: { businessId: string }) {
  const t = useT();
  const language = useLanguage();
  const [wanted, setWanted] = useState<AiTaskType[]>([...DEFAULT_TASKS]);
  const [opened, setOpened] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const fetchState = useCallback(
    () =>
      Promise.all([
        desktop.listAiInferences({
          task_type: null,
          status: null,
          business_id: businessId,
          review_status: null,
          limit: LISTED,
          cursor: null,
        }),
        desktop.listAiRuns({ project_id: null, limit: 10, cursor: null }),
      ]),
    [businessId],
  );
  const { data, error, reload: load } = useLoaded(fetchState, "list_ai_inferences");
  const page = data?.[0] ?? null;
  // polled only while an analysis runs: the worker never pushes
  const active = (data?.[1].items ?? []).some(
    (run) => run.status === "queued" || run.status === "running",
  );
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(timer);
  }, [active, load]);

  const run = async () => {
    setBusy(true);
    try {
      const started = await desktop.startAi({
        project_id: null,
        business_ids: [businessId],
        task_types: wanted,
      });
      toast.success(t("ai.startedForBusiness", { n: started.items.length }));
      await load();
    } catch (cause) {
      toast.error(t("ai.startFailed"), { description: toIpcError(cause, "start_ai").message });
    } finally {
      setBusy(false);
    }
  };

  const toggle = (task: AiTaskType) =>
    setWanted((current) =>
      current.includes(task) ? current.filter((value) => value !== task) : [...current, task],
    );

  return (
    <section aria-label={t("ai.panelTitle")} className="space-y-2 text-xs">
      <h4 className="text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
        {t("ai.panelTitle")}
      </h4>
      <InlineNotice variant="partial">{t("ai.panelExplain")}</InlineNotice>
      <fieldset className="flex flex-wrap gap-x-3 gap-y-1">
        <legend className="sr-only">{t("ai.tasks")}</legend>
        {TASKS.map((task) => (
          <label key={task} className="flex items-center gap-1">
            <input type="checkbox" checked={wanted.includes(task)} onChange={() => toggle(task)} />
            {t(TASK_KEYS[task])}
          </label>
        ))}
      </fieldset>
      <Button
        size="sm"
        className="h-7"
        disabled={busy || wanted.length === 0}
        onClick={() => void run()}
      >
        <Sparkles aria-hidden="true" />
        {t("ai.analyse")}
      </Button>
      {error && <p className="text-danger">{error.message}</p>}
      {page !== null && page.items.length === 0 && (
        <p className="text-muted-foreground">{t("ai.noInferences")}</p>
      )}
      <ul className="space-y-1">
        {page?.items.map((item) => (
          <li key={item.id}>
            <button
              type="button"
              aria-expanded={opened === item.id}
              onClick={() => setOpened(opened === item.id ? null : item.id)}
              className={cn(
                "flex w-full flex-wrap items-center gap-1.5 rounded border p-1.5 text-left",
                opened === item.id && "border-primary/60",
              )}
            >
              <span className="font-medium">{t(TASK_KEYS[item.task_type])}</span>
              <InferenceStatusBadge status={item.status} />
              <span className="rounded border px-1">{t(REVIEW_KEYS[item.review_status])}</span>
              <span className="text-[11px] text-muted-foreground">
                {`${item.model} · ${formatDateTime(item.created_at, language)}`}
              </span>
            </button>
            {opened === item.id && (
              <div className="mt-1 rounded-md border p-2">
                <InferenceView inferenceId={item.id} />
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
