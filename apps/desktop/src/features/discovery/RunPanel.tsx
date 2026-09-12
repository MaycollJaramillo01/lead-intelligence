import type { DiscoveryRun, RunControlAction } from "@lead/shared-types";
import { Ban, CirclePause, CirclePlay } from "lucide-react";
import { InlineNotice } from "@/components/states/StateBlock";
import { JobProgress, type JobState } from "@/components/domain/JobProgress";
import { Button } from "@/components/ui/button";
import { formatDateTime, useLanguage, useT, type TranslationKey } from "@/lib/i18n";

/** Structured error codes the worker sends; anything unknown is shown as the code itself. */
const ERROR_LABEL: Record<string, TranslationKey> = {
  rate_limited: "discoveryError.rate_limited",
  auth_required: "discoveryError.auth_required",
  blocked: "discoveryError.blocked",
  invalid_query: "discoveryError.invalid_query",
  schema_changed: "discoveryError.schema_changed",
  network_error: "discoveryError.network_error",
  source_unavailable: "discoveryError.source_unavailable",
};

const CONTROLS: Record<RunControlAction, { icon: typeof CirclePause; states: JobState[] }> = {
  pause: { icon: CirclePause, states: ["running"] },
  resume: { icon: CirclePlay, states: ["paused"] },
  cancel: { icon: Ban, states: ["queued", "running", "paused"] },
};

interface RunPanelProps {
  run: DiscoveryRun;
  busy: boolean;
  onControl: (action: RunControlAction) => void;
}

/** One run: its progress, what each source did, and the last thing it said. */
export function RunPanel({ run, busy, onControl }: RunPanelProps) {
  const t = useT();
  const language = useLanguage();
  const errors = run.events.filter((event) => event.level === "error");
  const firstError = errors[0]?.error_code ?? "";
  const label = (code: string) => {
    const key = ERROR_LABEL[code];
    return key ? t(key) : code;
  };

  return (
    <div className="space-y-2">
      <JobProgress
        job={{
          id: run.job_id,
          name: t("discovery.runName", {
            campaign: run.campaign_name,
            time: formatDateTime(run.requested_at, language),
          }),
          state: run.status,
          done: run.progress_done,
          total: run.progress_total,
          errors: run.error_count,
        }}
      />

      <div className="flex flex-wrap gap-2">
        {(Object.keys(CONTROLS) as RunControlAction[]).map((action) => {
          const { icon: Icon, states } = CONTROLS[action];
          if (!states.includes(run.status)) return null;
          return (
            <Button
              key={action}
              variant="outline"
              size="sm"
              className="h-7"
              disabled={busy}
              onClick={() => onControl(action)}
            >
              <Icon aria-hidden="true" />
              {t(`discovery.control.${action}`)}
            </Button>
          );
        })}
      </div>

      <ul className="space-y-1">
        {run.sources.map((source) => (
          <li key={source.source_run_id} className="rounded border px-2 py-1.5 text-xs">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="truncate font-medium">{source.connector_name}</span>
              <span className="text-[11px] text-muted-foreground">
                {t(`sourceRunStatus.${source.status}`)}
              </span>
            </div>
            <dl className="mt-0.5 flex flex-wrap gap-x-3 text-[11px] text-muted-foreground">
              <div className="flex gap-1">
                <dt>{t("discovery.pages")}</dt>
                <dd className="font-mono text-foreground">{source.pages_done}</dd>
              </div>
              <div className="flex gap-1">
                <dt>{t("discovery.records")}</dt>
                <dd className="font-mono text-foreground">{source.records_seen}</dd>
              </div>
              <div className="flex gap-1">
                <dt>{t("discovery.candidates")}</dt>
                <dd className="font-mono text-foreground">{source.candidate_count}</dd>
              </div>
              <div className="flex gap-1">
                <dt>{t("discovery.duplicates")}</dt>
                <dd className="font-mono text-foreground">{source.duplicate_count}</dd>
              </div>
              <div className="flex gap-1">
                <dt>{t("discovery.requests")}</dt>
                <dd className="font-mono text-foreground">{source.request_count}</dd>
              </div>
            </dl>
            {source.error_code && (
              <p className="mt-1 text-[11px] text-danger">
                {label(source.error_code)}: {source.error_message}
              </p>
            )}
          </li>
        ))}
      </ul>

      {errors.length > 0 && (
        <InlineNotice variant="partial">
          {t("discovery.errorsNotice", { n: errors.length, code: label(firstError) })}
        </InlineNotice>
      )}
    </div>
  );
}
