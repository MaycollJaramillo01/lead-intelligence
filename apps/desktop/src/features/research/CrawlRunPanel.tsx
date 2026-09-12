import type { ReactNode } from "react";
import type { CrawlRun, RunControlAction } from "@lead/shared-types";
import { Ban, CirclePause, CirclePlay } from "lucide-react";
import { JobProgress, type JobState } from "@/components/domain/JobProgress";
import { InlineNotice } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { formatBytes, reasonLabel } from "@/features/research/settings";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const CONTROLS: Record<RunControlAction, { icon: typeof CirclePause; states: JobState[] }> = {
  pause: { icon: CirclePause, states: ["running"] },
  resume: { icon: CirclePlay, states: ["paused"] },
  cancel: { icon: Ban, states: ["queued", "running", "paused"] },
};

function Fact({ label, children, tone }: { label: string; children: ReactNode; tone?: string }) {
  return (
    <div className="flex min-w-0 flex-col">
      <dt className="text-[10px] tracking-wider text-muted-foreground uppercase">{label}</dt>
      <dd className={cn("truncate font-mono text-xs tabular-nums", tone)}>{children}</dd>
    </div>
  );
}

interface CrawlRunPanelProps {
  run: CrawlRun;
  busy: boolean;
  selected: boolean;
  onSelect: () => void;
  onControl: (action: RunControlAction) => void;
}

/** One crawl: progress by site, budget spent and left, HTTP against browser, and what went wrong. */
export function CrawlRunPanel({ run, busy, selected, onSelect, onControl }: CrawlRunPanelProps) {
  const t = useT();
  const language = useLanguage();
  const { metrics, settings } = run;
  const left = Math.max(0, settings.max_requests - metrics.requests);
  const latency =
    metrics.latency_p50_ms === null
      ? "—"
      : `${metrics.latency_p50_ms} / ${metrics.latency_p95_ms} ms`;

  return (
    <section
      aria-label={t("research.runLabel")}
      className={cn("space-y-2 rounded-md border p-2", selected && "border-primary")}
    >
      <JobProgress
        job={{
          id: run.job_id,
          name: t("research.runName", {
            source: run.campaign_name ?? t("research.addresses"),
            time: formatDateTime(run.requested_at, language),
          }),
          state: run.status,
          done: run.sites_done,
          total: run.site_count,
          ratePerMinute: metrics.pages_per_minute,
          errors: metrics.pages_failed,
        }}
      />

      <div className="flex flex-wrap items-center gap-2">
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
        {!selected && (
          <Button variant="ghost" size="sm" className="h-7" onClick={onSelect}>
            {t("research.showPages")}
          </Button>
        )}
      </div>

      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Fact label={t("research.budget")}>
          {t("research.budgetValue", { used: metrics.requests, left })}
        </Fact>
        <Fact label={t("research.pages")}>
          {t("research.pagesValue", { http: metrics.http_pages, browser: metrics.browser_pages })}
        </Fact>
        <Fact label={t("research.queue")}>{metrics.queued}</Fact>
        <Fact label={t("research.bytes")}>{formatBytes(metrics.bytes)}</Fact>
        <Fact label={t("research.latency")}>{latency}</Fact>
        <Fact label={t("research.cacheHits")}>{metrics.cache_hits}</Fact>
        <Fact label={t("research.retries")}>{metrics.retries}</Fact>
        <Fact label={t("research.blocked")} tone={metrics.blocked > 0 ? "text-warn" : undefined}>
          {metrics.blocked}
        </Fact>
        <Fact label={t("research.fallbacks")}>{metrics.browser_fallbacks}</Fact>
        <Fact label={t("research.skipped")}>{metrics.urls_skipped}</Fact>
        <Fact label={t("research.duplicates")}>{metrics.duplicates}</Fact>
        <Fact label={t("research.perSite")}>
          {t("research.perSiteValue", {
            pages: settings.max_pages_per_site,
            depth: settings.max_depth,
          })}
        </Fact>
      </dl>

      {run.in_flight.length > 0 && (
        <div>
          <h4 className="text-[10px] tracking-wider text-muted-foreground uppercase">
            {t("research.inFlight")}
          </h4>
          <ul className="space-y-0.5">
            {run.in_flight.slice(0, 5).map((url) => (
              <li key={url} className="truncate font-mono text-[11px]">
                {url}
              </li>
            ))}
          </ul>
        </div>
      )}

      {run.errors.length > 0 && (
        <InlineNotice variant="partial">
          <span className="font-medium">{t("research.errors")}</span>
          <ul className="mt-1 space-y-0.5">
            {run.errors.map((group) => (
              <li key={group.reason} className="flex min-w-0 gap-2 text-[11px]">
                <span className="font-mono">{group.count}×</span>
                <span className="shrink-0">{reasonLabel(t, group.reason)}</span>
                <span className="truncate font-mono text-muted-foreground">
                  {group.example_url}
                </span>
              </li>
            ))}
          </ul>
        </InlineNotice>
      )}

      {run.sites.some((site) => site.error_code !== null) && (
        <ul className="space-y-0.5">
          {run.sites
            .filter((site) => site.error_code !== null)
            .slice(0, 8)
            .map((site) => (
              <li key={site.source_run_id} className="flex min-w-0 gap-2 text-[11px] text-danger">
                <span className="truncate font-mono">{site.site}</span>
                <span className="shrink-0">{reasonLabel(t, site.error_code)}</span>
              </li>
            ))}
        </ul>
      )}
    </section>
  );
}
