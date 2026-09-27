import { useState } from "react";
import type { DeadLetterAction, PipelineRun } from "@lead/shared-types";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { causeKey, openFailure, RETRY_REASON, SKIP_EFFECT, SKIP_REASON, STAGE_STEP } from "./model";

/**
 * A search that stopped, in plain words: at which step, why, what going on without it costs, and
 * «Reintentar» or «Saltar este paso» (phase 16). The engine's own message waits under «Detalles».
 */
export function SearchFailure({
  run,
  onResolved,
}: {
  run: PipelineRun;
  onResolved: () => Promise<void> | void;
}) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const letter = openFailure(run);
  if (!letter) {
    return <p className="text-sm text-muted-foreground">{t("clientSearch.failure.none")}</p>;
  }
  const effect = SKIP_EFFECT[letter.stage];

  const resolve = async (action: DeadLetterAction) => {
    setBusy(true);
    try {
      await desktop.resolveDeadLetter({
        job_id: run.job_id,
        task_id: letter.task_id,
        action,
        reason: action === "retry" ? RETRY_REASON : SKIP_REASON,
      });
      toast.success(
        t(action === "retry" ? "clientSearch.failure.retrying" : "clientSearch.failure.skipped"),
      );
      await onResolved();
    } catch (cause) {
      toast.error(t("clientSearch.failure.notResolved"), {
        description: toIpcError(cause, "resolve_dead_letter").message,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div role="alert" className="space-y-2 rounded-md border border-destructive/40 p-3 text-sm">
      <p className="font-medium">
        {t("clientSearch.failure.title", { step: t(STAGE_STEP[letter.stage]) })}
      </p>
      <p>
        {t(causeKey(letter.error_code))}
        {letter.retryable && ` ${t("clientSearch.failure.retryHelps")}`}
      </p>
      <p className="text-muted-foreground">
        {effect ? t(effect) : t("clientSearch.failure.cannotSkip")}
      </p>
      <div className="flex flex-wrap gap-2">
        <Button disabled={busy} onClick={() => void resolve("retry")}>
          {t("clientSearch.failure.retry")}
        </Button>
        {effect && (
          <Button variant="outline" disabled={busy} onClick={() => void resolve("skip")}>
            {t("clientSearch.failure.skip")}
          </Button>
        )}
      </div>
      <details className="text-xs text-muted-foreground">
        <summary className="cursor-pointer">{t("clientSearch.failure.details")}</summary>
        <p className="mt-1 break-words">{letter.error_message}</p>
      </details>
    </div>
  );
}
