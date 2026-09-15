import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import type {
  CampaignPage,
  DatasetPage,
  DeadLetter,
  DeadLetterAction,
  PipelinePage,
  PipelineRun,
  ResourceUsage,
  RunControlAction,
} from "@lead/shared-types";
import { toast } from "sonner";
import { JobProgress } from "@/components/domain/JobProgress";
import { Section } from "@/components/layout/page-chrome";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { codeText } from "@/features/leads/labels";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { jobPath, logsPath, snapshotPath } from "@/lib/links";
import { useLoaded } from "@/lib/useLoaded";

const POLL_MS = 2000;
const LISTED = 10;
const SELECT = "h-7 rounded-md border bg-transparent px-2 text-xs";

/** One key per start: a double click, or a retry after a timeout, returns the same pipeline. */
const newKey = () => `ui-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

interface Loaded {
  pipelines: PipelinePage;
  campaigns: CampaignPage;
  datasets: DatasetPage;
}

interface Target {
  value: string;
  label: string;
  project: string;
  campaign: string | null;
}

const gib = (mib: number | null) => (mib === null ? null : `${(mib / 1024).toFixed(1)} GiB`);

/**
 * Pipelines (phase 13): every stage runs the real engine job of its phase, in order, one stage per
 * project at a time, with retries, dead letters a person resolves, and this machine's resources.
 */
export function PipelinesPanel() {
  const t = useT();
  const load = useCallback(async (): Promise<Loaded> => {
    const [pipelines, campaigns, datasets] = await Promise.all([
      desktop.listPipelines({ project_id: null, limit: LISTED, cursor: null }),
      desktop.listCampaigns({ project_id: null, limit: 100, cursor: null }),
      desktop.listDatasets({ project_id: null, limit: 100, cursor: null }),
    ]);
    return { pipelines, campaigns, datasets };
  }, []);
  const { data, error, reload } = useLoaded(load, "list_pipelines");
  const [target, setTarget] = useState("");
  const [checkMail, setCheckMail] = useState(false);
  const [key, setKey] = useState(newKey);
  const [busy, setBusy] = useState(false);

  // a project is named by a pipeline that ran over it, else by its dataset or campaign
  const targets = useMemo((): Target[] => {
    if (!data) return [];
    const named = new Map(data.pipelines.items.map((run) => [run.project_id, run.project_name]));
    const projects = new Map<string, string>();
    for (const item of data.datasets.items) {
      const id = item.dataset.project_id;
      if (!projects.has(id)) projects.set(id, named.get(id) ?? item.dataset.name);
    }
    for (const campaign of data.campaigns.items) {
      const id = campaign.project_id;
      if (!projects.has(id)) projects.set(id, named.get(id) ?? campaign.name);
    }
    return [
      ...[...projects].map(([id, name]) => ({
        value: `project:${id}`,
        label: t("pipeline.projectOf", { name }),
        project: id,
        campaign: null,
      })),
      ...data.campaigns.items.map((campaign) => ({
        value: `campaign:${campaign.id}`,
        label: t("pipeline.withCampaign", { name: campaign.name }),
        project: campaign.project_id,
        campaign: campaign.id,
      })),
    ];
  }, [data, t]);
  const chosen = targets.find((option) => option.value === target) ?? targets[0];

  const active = (data?.pipelines.items ?? []).some(
    (run) => run.status === "queued" || run.status === "running",
  );
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => void reload(), POLL_MS);
    return () => clearInterval(timer);
  }, [active, reload]);

  const start = async () => {
    if (!chosen) return;
    setBusy(true);
    try {
      const run = await desktop.startPipeline({
        project_id: chosen.project,
        campaign_id: chosen.campaign,
        check_mail_domains: checkMail,
        priority: 0,
        idempotency_key: key,
      });
      toast.success(t("pipeline.started", { name: run.project_name }));
      setKey(newKey());
      await reload();
    } catch (cause) {
      // the key is kept: starting again after a timeout returns the pipeline already started
      toast.error(t("pipeline.startFailed"), {
        description: toIpcError(cause, "start_pipeline").message,
      });
    } finally {
      setBusy(false);
    }
  };

  const control = async (run: PipelineRun, action: RunControlAction) => {
    setBusy(true);
    try {
      await desktop.controlPipeline({ job_id: run.job_id, action });
      await reload();
    } catch (cause) {
      toast.error(t("pipeline.controlFailed"), {
        description: toIpcError(cause, "control_pipeline").message,
      });
    } finally {
      setBusy(false);
    }
  };

  const resolve = async (
    run: PipelineRun,
    letter: DeadLetter,
    action: DeadLetterAction,
    reason: string,
  ): Promise<boolean> => {
    setBusy(true);
    try {
      await desktop.resolveDeadLetter({
        job_id: run.job_id,
        task_id: letter.task_id,
        action,
        reason,
      });
      toast.success(t("pipeline.resolved"));
      await reload();
      return true;
    } catch (cause) {
      toast.error(t("pipeline.resolveFailed"), {
        description: toIpcError(cause, "resolve_dead_letter").message,
      });
      return false;
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section title={t("pipeline.title")} description={t("pipeline.help")}>
      {error ? (
        <StateBlock
          variant={error.code === "desktop_unavailable" ? "offline" : "error"}
          title={t("pipeline.errorTitle")}
          body={error.message}
          action={
            <Button variant="outline" size="sm" onClick={() => void reload()}>
              {t("common.retry")}
            </Button>
          }
        />
      ) : data === null ? (
        <StateBlock variant="loading" title={t("common.loading")} />
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-end gap-2 text-xs">
            <div className="space-y-0.5">
              <Label htmlFor="pipeline-target" className="text-xs">
                {t("pipeline.target")}
              </Label>
              <select
                id="pipeline-target"
                className={SELECT}
                value={chosen?.value ?? ""}
                disabled={targets.length === 0}
                onChange={(event) => setTarget(event.target.value)}
              >
                {targets.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
            <label className="flex h-7 items-center gap-1.5">
              <input
                type="checkbox"
                checked={checkMail}
                onChange={(event) => setCheckMail(event.target.checked)}
              />
              {t("pipeline.checkMail")}
            </label>
            <Button
              size="sm"
              className="h-7"
              disabled={busy || !chosen}
              onClick={() => void start()}
            >
              {t("pipeline.start")}
            </Button>
          </div>
          {targets.length === 0 && (
            <InlineNotice variant="empty">{t("pipeline.noTargets")}</InlineNotice>
          )}
          <Resources usage={data.pipelines.resources} />
          {data.pipelines.items.length === 0 ? (
            <p className="text-xs text-muted-foreground">{t("pipeline.none")}</p>
          ) : (
            <ul className="space-y-3">
              {data.pipelines.items.map((run) => (
                <li key={run.job_id}>
                  <PipelineCard
                    run={run}
                    busy={busy}
                    onControl={(action) => void control(run, action)}
                    onResolve={(letter, action, reason) => resolve(run, letter, action, reason)}
                  />
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Section>
  );
}

function Resources({ usage }: { usage: ResourceUsage }) {
  const t = useT();
  const unknown = t("common.unknown");
  return (
    <div className="space-y-1 rounded-md border p-2 text-[11px]">
      <p className="flex flex-wrap gap-x-4 gap-y-0.5 text-muted-foreground">
        <span>
          {t("pipeline.cpu", {
            count: usage.cpu_count,
            percent: usage.cpu_percent === null ? unknown : `${usage.cpu_percent.toFixed(0)}%`,
          })}
        </span>
        <span>
          {t("pipeline.ram", {
            free: gib(usage.ram_free_mib) ?? unknown,
            total: gib(usage.ram_total_mib) ?? unknown,
          })}
        </span>
        <span>
          {t("pipeline.vram", {
            free: gib(usage.vram_free_mib) ?? unknown,
            total: gib(usage.vram_total_mib) ?? unknown,
          })}
        </span>
        <span>
          {t("pipeline.disk", {
            free: gib(usage.disk_free_mib) ?? unknown,
            total: gib(usage.disk_total_mib) ?? unknown,
          })}
        </span>
        <span>{t("pipeline.browsers", { n: usage.active_browsers ?? unknown })}</span>
      </p>
      <p className="flex flex-wrap gap-x-3 font-mono">
        {usage.slots.map((slot) => (
          <span key={slot.resource}>
            {`${t(`pipeline.resource.${slot.resource}`)} ${slot.running}/${slot.limit}`}
          </span>
        ))}
      </p>
      {usage.pressure.map((code) => (
        <InlineNotice key={code} variant="partial">
          {codeText(t, "pipeline.pressure", code)}
        </InlineNotice>
      ))}
    </div>
  );
}

interface PipelineCardProps {
  run: PipelineRun;
  busy: boolean;
  onControl: (action: RunControlAction) => void;
  onResolve: (letter: DeadLetter, action: DeadLetterAction, reason: string) => Promise<boolean>;
}

function PipelineCard({ run, busy, onControl, onResolve }: PipelineCardProps) {
  const t = useT();
  const language = useLanguage();
  const open = run.dead_letters.filter((letter) => letter.review_status === "open");
  const name = [run.project_name, run.campaign_name, formatDateTime(run.requested_at, language)]
    .filter(Boolean)
    .join(" · ");
  return (
    <div className="space-y-1.5">
      <JobProgress
        job={{
          id: run.job_id,
          name: `${t("jobs.kind.pipeline")} · ${name}`,
          state: run.status,
          done: run.progress_done,
          total: run.progress_total,
          errors: open.length,
        }}
      />
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 px-1 text-[11px]">
        {run.actions.map((action) => (
          <Button
            key={action}
            size="sm"
            variant="outline"
            className="h-6 text-[11px]"
            disabled={busy}
            onClick={() => onControl(action)}
          >
            {t(`pipeline.action.${action}`)}
          </Button>
        ))}
        <span className="text-muted-foreground">{t("pipeline.counts", { ...run.counts })}</span>
        {run.eligible_leads !== null && (
          <span>{t("pipeline.eligible", { n: run.eligible_leads })}</span>
        )}
        {run.snapshot_id && (
          <Link to={snapshotPath(run.snapshot_id)} className="text-primary hover:underline">
            {t("pipeline.openSnapshot")}
          </Link>
        )}
        <Link to={logsPath(run.job_id)} className="text-primary hover:underline">
          {t("pipeline.events")}
        </Link>
      </div>
      {open.map((letter) => (
        <DeadLetterForm key={letter.id} letter={letter} busy={busy} onResolve={onResolve} />
      ))}
      <details className="rounded-md border px-2 py-1 text-[11px]">
        <summary className="cursor-pointer select-none">{t("pipeline.stages")}</summary>
        <table className="mt-1 w-full">
          <thead>
            <tr className="text-left text-muted-foreground">
              <th scope="col" className="py-0.5 pr-2 font-medium">
                {t("pipeline.stage")}
              </th>
              <th scope="col" className="pr-2 font-medium">
                {t("pipeline.status")}
              </th>
              <th scope="col" className="font-medium">
                {t("pipeline.detail")}
              </th>
            </tr>
          </thead>
          <tbody>
            {run.stages.map((stage) => {
              const last = stage.attempts.at(-1);
              const jobs = stage.attempts.flatMap((attempt) =>
                attempt.jobs.map((job) => ({ attempt: attempt.attempt, job })),
              );
              return (
                <tr key={stage.task_id} className="border-t align-top">
                  <td className="py-0.5 pr-2 whitespace-nowrap">
                    {t(`pipeline.stage.${stage.stage}`)}
                  </td>
                  <td className="pr-2">
                    {stage.skipped ? t("pipeline.skippedState") : stage.status}
                  </td>
                  <td>
                    <span className="flex flex-wrap gap-x-2">
                      {stage.skipped && (
                        <span>{codeText(t, "pipeline.skipped", stage.skipped)}</span>
                      )}
                      {last && (
                        <span>
                          {t("pipeline.attempts", { n: last.attempt, max: stage.max_attempts })}
                        </span>
                      )}
                      {last?.error_message && (
                        <span className="text-danger">{`${last.error_code ?? ""}: ${last.error_message}`}</span>
                      )}
                      {jobs.map(({ attempt, job }) => (
                        <Link
                          key={`${attempt}-${job.job_id}`}
                          to={jobPath(job.job_id)}
                          className="text-primary hover:underline"
                        >
                          {[
                            job.kind,
                            job.status,
                            job.reused ? t("pipeline.reused") : null,
                            job.eta_seconds !== null
                              ? t("pipeline.eta", { s: job.eta_seconds })
                              : null,
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        </Link>
                      ))}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </details>
    </div>
  );
}

interface DeadLetterFormProps {
  letter: DeadLetter;
  busy: boolean;
  onResolve: (letter: DeadLetter, action: DeadLetterAction, reason: string) => Promise<boolean>;
}

/** A stage that failed for good: a person retries or skips it, and says why. */
function DeadLetterForm({ letter, busy, onResolve }: DeadLetterFormProps) {
  const t = useT();
  const [reason, setReason] = useState("");
  const id = `dead-letter-${letter.id}`;
  const act = async (action: DeadLetterAction) => {
    if (await onResolve(letter, action, reason.trim())) setReason("");
  };
  return (
    <div
      role="group"
      aria-label={t("pipeline.deadLetter")}
      className="space-y-1 rounded-md border border-danger/40 p-2 text-[11px]"
    >
      <p className="font-medium text-danger">
        {t("pipeline.deadLetterLine", {
          stage: t(`pipeline.stage.${letter.stage}`),
          attempt: letter.attempt,
          code: letter.error_code,
        })}
      </p>
      <p>{letter.error_message}</p>
      <Label htmlFor={id} className="text-[11px]">
        {t("pipeline.reason")}
      </Label>
      <Textarea
        id={id}
        value={reason}
        maxLength={2000}
        onChange={(event) => setReason(event.target.value)}
        className="min-h-10 text-xs"
      />
      <div className="flex gap-1">
        <Button
          size="sm"
          className="h-6 text-[11px]"
          disabled={busy || !reason.trim()}
          onClick={() => void act("retry")}
        >
          {t("pipeline.retry")}
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="h-6 text-[11px]"
          disabled={busy || !reason.trim()}
          onClick={() => void act("skip")}
        >
          {t("pipeline.skip")}
        </Button>
      </div>
    </div>
  );
}
