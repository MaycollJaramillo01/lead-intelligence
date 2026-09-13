import { useCallback, useEffect, useMemo, useState } from "react";
import type { RunControlAction, WebsiteAuditItem, WebsiteRun } from "@lead/shared-types";
import type { ColumnDef } from "@tanstack/react-table";
import { Ban, CirclePause, CirclePlay, Play } from "lucide-react";
import { toast } from "sonner";
import { DataTable } from "@/components/data/DataTable";
import { DateTime, Mono, Text } from "@/components/data/cells";
import { filterFieldsFrom, type FilterCondition } from "@/components/data/filters";
import { PagedFooter } from "@/components/data/PagedFooter";
import { JobProgress, type JobState } from "@/components/domain/JobProgress";
import { StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { WebsiteAuditPanel } from "@/features/website/WebsiteAuditPanel";
import { AUDIT_STATUS_KEYS } from "@/features/website/labels";
import { formatDateTime, useLanguage, useT, type Translator } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { useLoaded } from "@/lib/useLoaded";
import { usePreferences } from "@/stores/preferences";

const POLL_MS = 1000;
const LISTED = 500;
const ACTIVE: WebsiteRun["status"][] = ["queued", "running"];
const CONTROLS: Record<RunControlAction, { icon: typeof CirclePause; states: JobState[] }> = {
  pause: { icon: CirclePause, states: ["running"] },
  resume: { icon: CirclePlay, states: ["paused"] },
  cancel: { icon: Ban, states: ["queued", "running", "paused"] },
};

function buildColumns(t: Translator): ColumnDef<WebsiteAuditItem>[] {
  return [
    {
      id: "site",
      accessorKey: "site",
      header: t("website.column.site"),
      size: 240,
      cell: ({ row }) => <Mono value={row.original.site} />,
    },
    {
      id: "status",
      accessorFn: (row) => t(AUDIT_STATUS_KEYS[row.status]),
      header: t("website.column.status"),
      size: 100,
      cell: ({ getValue }) => <Text value={getValue<string>()} />,
    },
    {
      id: "pages",
      accessorFn: (row) =>
        t("website.pagesValue", { analyzed: row.pages_analyzed, expected: row.pages_expected }),
      header: t("website.column.pages"),
      size: 120,
      cell: ({ getValue }) => <Mono value={getValue<string>()} />,
    },
    {
      id: "coverage",
      accessorFn: (row) => `${Math.round(row.coverage * 100)}%`,
      header: t("website.column.coverage"),
      size: 90,
      cell: ({ getValue }) => <Mono value={getValue<string>()} />,
    },
    {
      id: "failed",
      accessorKey: "failed",
      header: t("website.column.failed"),
      size: 80,
      cell: ({ getValue }) => <Mono value={getValue<number>()} />,
    },
    {
      id: "warnings",
      accessorKey: "warnings",
      header: t("website.column.warnings"),
      size: 90,
      cell: ({ getValue }) => <Mono value={getValue<number>()} />,
    },
    {
      id: "unknown",
      accessorKey: "unknown",
      header: t("website.column.unknown"),
      size: 90,
      cell: ({ getValue }) => <Mono value={getValue<number>()} />,
    },
    {
      id: "crawled",
      accessorKey: "crawled_at",
      header: t("website.column.crawled"),
      size: 170,
      cell: ({ getValue }) => <DateTime value={getValue<string>()} />,
    },
    {
      id: "audited",
      accessorKey: "created_at",
      header: t("website.column.audited"),
      size: 170,
      cell: ({ getValue }) => <DateTime value={getValue<string>()} />,
    },
  ];
}

/**
 * Website intelligence (phase 08): the latest audit of every crawled site and, one audit at a time,
 * its findings with their evidence, coverage, subscores, pages and what changed since the previous
 * audit. Audits read what the crawler stored; nothing here fetches, submits or guesses.
 */
export function WebsiteIntelligenceView() {
  const t = useT();
  const language = useLanguage();
  const inspectorSize = usePreferences((state) => state.inspectorSize);
  const columns = useMemo(() => buildColumns(t), [t]);
  const filterFields = useMemo(() => filterFieldsFrom(columns), [columns]);
  const [conditions, setConditions] = useState<FilterCondition[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [opened, setOpened] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const fetchAudits = useCallback(
    () => desktop.listWebsiteAudits({ project_id: null, site: null, limit: LISTED, cursor: null }),
    [],
  );
  const auditList = useLoaded(fetchAudits, "list_website_audits");
  const fetchRuns = useCallback(
    () => desktop.listWebsiteRuns({ project_id: null, limit: 5, cursor: null }),
    [],
  );
  const runList = useLoaded(fetchRuns, "list_website_runs");
  const audits = auditList.data?.items ?? [];
  const runs = runList.data?.items ?? [];
  const latest = runs[0] ?? null;
  const error = auditList.error ?? runList.error;

  const { reload: reloadAudits } = auditList;
  const { reload: reloadRuns } = runList;
  const refresh = useCallback(async () => {
    await Promise.all([reloadAudits(), reloadRuns()]);
  }, [reloadAudits, reloadRuns]);

  // polled only while an audit runs: the worker never pushes, and an idle screen asks nothing
  const active = runs.some((run) => ACTIVE.includes(run.status));
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [active, refresh]);

  const start = async (projectId: string | null, sites: string[]) => {
    setBusy(true);
    try {
      const page = await desktop.startWebsiteAudit({ project_id: projectId, sites });
      toast.success(t("website.started", { n: page.items.length }));
      await refresh();
    } catch (cause) {
      toast.error(t("website.startFailed"), {
        description: toIpcError(cause, "start_website_audit").message,
      });
    } finally {
      setBusy(false);
    }
  };

  const control = async (jobId: string, action: RunControlAction) => {
    setBusy(true);
    try {
      await desktop.controlWebsiteAudit({ job_id: jobId, action });
      await refresh();
    } catch (cause) {
      toast.error(t("discovery.controlFailed"), {
        description: toIpcError(cause, "control_website_audit").message,
      });
    } finally {
      setBusy(false);
    }
  };

  const emptyState = error ? (
    <StateBlock
      variant={error.code === "desktop_unavailable" ? "offline" : "error"}
      title={t("website.errorTitle")}
      body={error.message}
      action={
        <Button variant="outline" size="sm" onClick={() => void refresh()}>
          {t("common.retry")}
        </Button>
      }
    />
  ) : (
    <StateBlock variant="empty" title={t("website.emptyTitle")} body={t("website.emptyBody")} />
  );
  const shown = opened ?? selected;

  return (
    <ResizablePanelGroup orientation="horizontal" className="min-h-0 flex-1">
      <ResizablePanel defaultSize={`${100 - inspectorSize}%`} minSize="40%">
        <div className="flex h-full min-h-0 flex-col">
          <div className="shrink-0 space-y-2 border-b p-2">
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                className="h-7"
                disabled={busy || active}
                onClick={() => void start(null, [])}
              >
                <Play aria-hidden="true" />
                {t("website.audit")}
              </Button>
              <p className="min-w-0 flex-1 text-[11px] text-muted-foreground">
                {t("website.explain")}
              </p>
            </div>
            {latest && (
              <section
                aria-label={t("website.runLabel")}
                className="space-y-1.5 rounded-md border p-2"
              >
                <JobProgress
                  job={{
                    id: latest.job_id,
                    name: t("website.runName", {
                      project: latest.project_name,
                      time: formatDateTime(latest.requested_at, language),
                    }),
                    state: latest.status,
                    done: latest.progress_done,
                    total: latest.progress_total,
                    errors: latest.events.filter((event) => event.level === "error").length,
                  }}
                />
                <div className="flex flex-wrap items-center gap-2">
                  {(Object.keys(CONTROLS) as RunControlAction[]).map((action) => {
                    const { icon: Icon, states } = CONTROLS[action];
                    if (!states.includes(latest.status)) return null;
                    return (
                      <Button
                        key={action}
                        variant="outline"
                        size="sm"
                        className="h-7"
                        disabled={busy}
                        onClick={() => void control(latest.job_id, action)}
                      >
                        <Icon aria-hidden="true" />
                        {t(`discovery.control.${action}`)}
                      </Button>
                    );
                  })}
                  <span className="text-[11px] text-muted-foreground">
                    {t("jobs.websiteDetail", {
                      sites: latest.stats.sites,
                      audited: latest.stats.audited,
                      unchanged: latest.stats.unchanged,
                      failed: latest.stats.failed,
                      unknown: latest.stats.unknown,
                    })}
                  </span>
                </div>
              </section>
            )}
          </div>
          <div className="min-h-0 flex-1">
            <DataTable
              tableId="website-audits"
              columns={columns}
              data={audits}
              getRowId={(row) => row.id}
              filterFields={filterFields}
              conditions={conditions}
              onConditionsChange={setConditions}
              pinnedLeft={["site"]}
              activeRowId={selected}
              onActivateRow={(id) => {
                setSelected(id);
                setOpened(null);
              }}
              emptyState={emptyState}
            />
          </div>
          <PagedFooter
            loaded={audits.length}
            total={auditList.data?.total ?? 0}
            hasMore={false}
            loading={false}
            onLoadMore={() => undefined}
          />
        </div>
      </ResizablePanel>
      <ResizableHandle withHandle />
      <ResizablePanel defaultSize={`${inspectorSize}%`} minSize="20%" maxSize="60%">
        <aside
          aria-label={t("common.inspector")}
          className="h-full min-h-0 overflow-auto border-l bg-card p-3"
        >
          {shown === null ? (
            <StateBlock
              variant="empty"
              title={t("website.inspectorEmptyTitle")}
              body={t("website.inspectorEmptyBody")}
            />
          ) : (
            <WebsiteAuditPanel
              key={shown}
              auditId={shown}
              busy={busy}
              onOpen={setOpened}
              onAuditAgain={(projectId, site) => void start(projectId, [site])}
            />
          )}
        </aside>
      </ResizablePanel>
    </ResizablePanelGroup>
  );
}
