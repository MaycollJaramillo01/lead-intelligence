import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { PageScroll, PageShell, Section } from "@/components/layout/page-chrome";
import { StateBlock } from "@/components/states/StateBlock";
import { JobProgress, type JobSummary } from "@/components/domain/JobProgress";
import { Button } from "@/components/ui/button";
import { formatDateTime, useLanguage, useT, type TranslationKey } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { useLoaded } from "@/lib/useLoaded";

const POLL_MS = 2000;
const LISTED = 25;

type JobKind = "discovery" | "crawl" | "resolution" | "latino" | "website" | "contacts";

interface Listed {
  kind: JobKind;
  requestedAt: string;
  summary: Omit<JobSummary, "name">;
  label: string;
  detail: string;
}

const MODULE: Record<JobKind, { path: string; name: TranslationKey }> = {
  discovery: { path: "/discovery", name: "nav.discovery" },
  crawl: { path: "/research-center", name: "nav.research_center" },
  resolution: { path: "/businesses", name: "nav.businesses" },
  latino: { path: "/businesses", name: "nav.businesses" },
  website: { path: "/research-center", name: "nav.research_center" },
  contacts: { path: "/research-center", name: "nav.research_center" },
};

/** Every job the worker knows, newest first, whichever engine runs it. Nothing here is simulated. */
export function JobsScreen() {
  const t = useT();
  const language = useLanguage();
  const [busy, setBusy] = useState(false);

  const fetchJobs = useCallback(async (): Promise<Listed[]> => {
    const query = { project_id: null, campaign_id: null, limit: LISTED, cursor: null };
    const byProject = { project_id: null, limit: LISTED, cursor: null };
    const [discovery, crawls, resolutions, evaluations, audits, checks] = await Promise.all([
      desktop.listDiscoveryRuns(query),
      desktop.listCrawlRuns(query),
      desktop.listResolutionRuns(byProject),
      desktop.listLatinoRuns(byProject),
      desktop.listWebsiteRuns(byProject),
      desktop.listContactRuns(byProject),
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
      const control = {
        discovery: desktop.controlDiscovery,
        crawl: desktop.controlCrawl,
        resolution: desktop.controlResolution,
        latino: desktop.controlLatino,
        website: desktop.controlWebsiteAudit,
        contacts: desktop.controlContacts,
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
                    <Link to={MODULE[job.kind].path} className="text-primary hover:underline">
                      {t(MODULE[job.kind].name)}
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
