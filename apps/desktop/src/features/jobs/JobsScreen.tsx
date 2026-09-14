import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { toast } from "sonner";
import { PageScroll, PageShell, Section } from "@/components/layout/page-chrome";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import { JobProgress, type JobSummary } from "@/components/domain/JobProgress";
import { Button } from "@/components/ui/button";
import { formatDateTime, useLanguage, useT, type TranslationKey } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { scoreRunPath } from "@/lib/links";
import { useLoaded } from "@/lib/useLoaded";
import { cn } from "@/lib/utils";

const POLL_MS = 2000;
const LISTED = 25;

type JobKind =
  "discovery" | "crawl" | "resolution" | "latino" | "website" | "contacts" | "ai" | "scoring";

interface Listed {
  kind: JobKind;
  requestedAt: string;
  summary: Omit<JobSummary, "name">;
  label: string;
  detail: string;
  /** A scoring job's run, which opens in the Lead Explorer. */
  run?: string;
}

const MODULE: Record<JobKind, { path: string; name: TranslationKey }> = {
  discovery: { path: "/discovery", name: "nav.discovery" },
  crawl: { path: "/research-center", name: "nav.research_center" },
  resolution: { path: "/businesses", name: "nav.businesses" },
  latino: { path: "/businesses", name: "nav.businesses" },
  website: { path: "/research-center", name: "nav.research_center" },
  contacts: { path: "/research-center", name: "nav.research_center" },
  ai: { path: "/ai-models", name: "nav.ai_models" },
  scoring: { path: "/scoring-models", name: "nav.scoring_models" },
};

/** Every job the worker knows, newest first, whichever engine runs it. Nothing here is simulated. */
export function JobsScreen() {
  const t = useT();
  const language = useLanguage();
  const [busy, setBusy] = useState(false);

  const fetchJobs = useCallback(async (): Promise<Listed[]> => {
    const query = { project_id: null, campaign_id: null, limit: LISTED, cursor: null };
    const byProject = { project_id: null, limit: LISTED, cursor: null };
    const [discovery, crawls, resolutions, evaluations, audits, checks, analyses, scores] =
      await Promise.all([
        desktop.listDiscoveryRuns(query),
        desktop.listCrawlRuns(query),
        desktop.listResolutionRuns(byProject),
        desktop.listLatinoRuns(byProject),
        desktop.listWebsiteRuns(byProject),
        desktop.listContactRuns(byProject),
        desktop.listAiRuns(byProject),
        desktop.listScoreRuns(byProject),
      ]);
    const listed: Listed[] = [
      ...discovery.items.map((run) => ({
        kind: "discovery" as const,
        requestedAt: run.requested_at,
        label: run.campaign_name,
        detail: t("jobs.discoveryDetail", {
          candidates: run.candidate_count,
          requests: run.request_count,
        }),
        summary: {
          id: run.job_id,
          state: run.status,
          done: run.progress_done,
          total: run.progress_total,
          errors: run.error_count,
        },
      })),
      ...crawls.items.map((run) => ({
        kind: "crawl" as const,
        requestedAt: run.requested_at,
        label: run.campaign_name ?? t("research.addresses"),
        detail: t("jobs.crawlDetail", {
          pages: run.metrics.pages_fetched,
          requests: run.metrics.requests,
          fallbacks: run.metrics.browser_fallbacks,
        }),
        summary: {
          id: run.job_id,
          state: run.status,
          done: run.sites_done,
          total: run.site_count,
          ratePerMinute: run.metrics.pages_per_minute,
          errors: run.metrics.pages_failed,
        },
      })),
      ...resolutions.items.map((run) => ({
        kind: "resolution" as const,
        requestedAt: run.requested_at,
        label: run.project_name,
        detail: t("jobs.resolutionDetail", {
          records: run.stats.records,
          pairs: run.stats.pairs_compared,
          clusters: run.stats.clusters,
          review: run.stats.review,
        }),
        summary: {
          id: run.job_id,
          state: run.status,
          done: run.progress_done,
          total: run.progress_total,
          errors: run.events.filter((event) => event.level === "error").length,
        },
      })),
      ...evaluations.items.map((run) => ({
        kind: "latino" as const,
        requestedAt: run.requested_at,
        label: run.project_name,
        detail: t("jobs.latinoDetail", {
          businesses: run.stats.businesses,
          ownership: run.stats.verified + run.stats.high_confidence + run.stats.probable,
          affinity: run.stats.affinity,
          unknown: run.stats.unknown,
        }),
        summary: {
          id: run.job_id,
          state: run.status,
          done: run.progress_done,
          total: run.progress_total,
          errors: run.events.filter((event) => event.level === "error").length,
        },
      })),
      ...audits.items.map((run) => ({
        kind: "website" as const,
        requestedAt: run.requested_at,
        label: run.project_name,
        detail: t("jobs.websiteDetail", {
          sites: run.stats.sites,
          audited: run.stats.audited,
          unchanged: run.stats.unchanged,
          failed: run.stats.failed,
          unknown: run.stats.unknown,
        }),
        summary: {
          id: run.job_id,
          state: run.status,
          done: run.progress_done,
          total: run.progress_total,
          errors: run.events.filter((event) => event.level === "error").length,
        },
      })),
      ...checks.items.map((run) => ({
        kind: "contacts" as const,
        requestedAt: run.requested_at,
        label: run.project_name,
        detail: t("jobs.contactsDetail", {
          channels: run.stats.channels,
          likely: run.stats.likely_valid,
          invalid: run.stats.invalid,
          suppressed: run.stats.suppressed,
          hypotheses: run.stats.hypotheses,
        }),
        summary: {
          id: run.job_id,
          state: run.status,
          done: run.progress_done,
          total: run.progress_total,
          errors: run.events.filter((event) => event.level === "error").length,
        },
      })),
      ...analyses.items.map((run) => ({
        kind: "ai" as const,
        requestedAt: run.requested_at,
        label: run.project_name,
        detail: t("jobs.aiDetail", {
          done: run.stats.done,
          targets: run.stats.targets,
          succeeded: run.stats.succeeded,
          cached: run.stats.cache_hits,
          skipped: run.stats.skipped_no_input + run.stats.skipped_unavailable,
          failed: run.stats.failed + run.stats.invalid_output,
        }),
        summary: {
          id: run.job_id,
          state: run.status,
          done: run.progress_done,
          total: run.progress_total,
          errors: run.events.filter((event) => event.level === "error").length,
        },
      })),
      ...scores.items.map((run) => ({
        kind: "scoring" as const,
        requestedAt: run.requested_at,
        run: run.score_run_id,
        label: `${run.project_name} · ${run.icp_profile_name} v${run.icp_profile_version}`,
        detail: t("jobs.scoringDetail", {
          businesses: run.stats.businesses,
          priority: run.stats.priority,
          review: run.stats.review,
          insufficient: run.stats.insufficient_data,
          excluded: run.stats.excluded,
        }),
        summary: {
          id: run.job_id,
          state: run.status,
          done: run.progress_done,
          total: run.progress_total,
          errors: run.events.filter((event) => event.level === "error").length,
        },
      })),
    ];
    return listed.sort((a, b) => b.requestedAt.localeCompare(a.requestedAt));
  }, [t]);
  const { data, error, reload } = useLoaded(fetchJobs, "list_jobs");
  const jobs = data ?? [];
  const [params] = useSearchParams();
  const linked = params.get("job");
  const listed = linked === null || jobs.some((job) => job.summary.id === linked);

  // a `?job=` link brings its job into view once the list is read
  useEffect(() => {
    if (linked && data)
      document.getElementById(`job-${linked}`)?.scrollIntoView({ block: "center" });
  }, [linked, data]);

  const active = jobs.some(
    (job) => job.summary.state === "queued" || job.summary.state === "running",
  );
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => void reload(), POLL_MS);
    return () => clearInterval(timer);
  }, [active, reload]);

  const cancel = async (job: Listed) => {
    setBusy(true);
    try {
      const request = { job_id: job.summary.id, action: "cancel" as const };
      const control = {
        discovery: desktop.controlDiscovery,
        crawl: desktop.controlCrawl,
        resolution: desktop.controlResolution,
        latino: desktop.controlLatino,
        website: desktop.controlWebsiteAudit,
        contacts: desktop.controlContacts,
        ai: desktop.controlAi,
        scoring: desktop.controlScoring,
      }[job.kind];
      await control(request);
      await reload();
    } catch (cause) {
      toast.error(t("discovery.controlFailed"), {
        description: toIpcError(cause, "control").message,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <PageShell>
      <PageScroll>
        <Section title={t("nav.jobs")} description={t("desc.jobs")}>
          {error ? (
            <StateBlock
              variant={error.code === "desktop_unavailable" ? "offline" : "error"}
              title={t("jobs.errorTitle")}
              body={error.message}
              action={
                <Button variant="outline" size="sm" onClick={() => void reload()}>
                  {t("common.retry")}
                </Button>
              }
            />
          ) : data === null ? (
            <StateBlock variant="loading" title={t("common.loading")} />
          ) : jobs.length === 0 ? (
            <StateBlock variant="empty" title={t("jobs.emptyTitle")} body={t("jobs.emptyBody")} />
          ) : (
            <div className="space-y-2">
              {!listed && <InlineNotice variant="partial">{t("jobs.notListed")}</InlineNotice>}
              <ul className="space-y-2">
                {jobs.map((job) => (
                  <li
                    key={job.summary.id}
                    id={`job-${job.summary.id}`}
                    aria-current={job.summary.id === linked ? "true" : undefined}
                    className={cn(
                      "space-y-1",
                      job.summary.id === linked && "rounded-md ring-2 ring-primary/60",
                    )}
                  >
                    <JobProgress
                      job={{
                        ...job.summary,
                        name: `${t(`jobs.kind.${job.kind}`)} · ${job.label} · ${formatDateTime(job.requestedAt, language)}`,
                      }}
                      onCancel={busy ? undefined : () => void cancel(job)}
                    />
                    <p className="flex flex-wrap gap-2 px-1 text-[11px] text-muted-foreground">
                      <span>{job.detail}</span>
                      <Link to={MODULE[job.kind].path} className="text-primary hover:underline">
                        {t(MODULE[job.kind].name)}
                      </Link>
                      {job.run && (
                        <Link to={scoreRunPath(job.run)} className="text-primary hover:underline">
                          {t("nav.lead_explorer")}
                        </Link>
                      )}
                    </p>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Section>
      </PageScroll>
    </PageShell>
  );
}
