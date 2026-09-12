import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { PageScroll, PageShell, Section } from "@/components/layout/page-chrome";
import { StateBlock } from "@/components/states/StateBlock";
import { JobProgress, type JobSummary } from "@/components/domain/JobProgress";
import { Button } from "@/components/ui/button";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { useLoaded } from "@/lib/useLoaded";

const POLL_MS = 2000;
const LISTED = 25;

type JobKind = "discovery" | "crawl";

interface Listed {
  kind: JobKind;
  requestedAt: string;
  summary: Omit<JobSummary, "name">;
  label: string;
  detail: string;
}

const MODULE: Record<JobKind, string> = { discovery: "/discovery", crawl: "/research-center" };

/** Every job the worker knows, newest first, whichever engine runs it. Nothing here is simulated. */
export function JobsScreen() {
  const t = useT();
  const language = useLanguage();
  const [busy, setBusy] = useState(false);

  const fetchJobs = useCallback(async (): Promise<Listed[]> => {
    const query = { project_id: null, campaign_id: null, limit: LISTED, cursor: null };
    const [discovery, crawls] = await Promise.all([
      desktop.listDiscoveryRuns(query),
      desktop.listCrawlRuns(query),
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
    ];
    return listed.sort((a, b) => b.requestedAt.localeCompare(a.requestedAt));
  }, [t]);
  const { data, error, reload } = useLoaded(fetchJobs, "list_jobs");
  const jobs = data ?? [];

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
      await (job.kind === "crawl"
        ? desktop.controlCrawl(request)
        : desktop.controlDiscovery(request));
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
            <ul className="space-y-2">
              {jobs.map((job) => (
                <li key={job.summary.id} className="space-y-1">
                  <JobProgress
                    job={{
                      ...job.summary,
                      name: `${t(`jobs.kind.${job.kind}`)} · ${job.label} · ${formatDateTime(job.requestedAt, language)}`,
                    }}
                    onCancel={busy ? undefined : () => void cancel(job)}
                  />
                  <p className="flex flex-wrap gap-2 px-1 text-[11px] text-muted-foreground">
                    <span>{job.detail}</span>
                    <Link to={MODULE[job.kind]} className="text-primary hover:underline">
                      {t(job.kind === "crawl" ? "nav.research_center" : "nav.discovery")}
                    </Link>
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </PageScroll>
    </PageShell>
  );
}
