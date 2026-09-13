import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  CrawlRun,
  CrawlSettings,
  CrawlUrlItem,
  CrawlUrlStatus,
  RunControlAction,
} from "@lead/shared-types";
import type { ColumnDef } from "@tanstack/react-table";
import { Play, SlidersHorizontal } from "lucide-react";
import { toast } from "sonner";
import { DataTable } from "@/components/data/DataTable";
import { DateTime, Mono, Text } from "@/components/data/cells";
import { filterFieldsFrom, type FilterCondition } from "@/components/data/filters";
import { PageActions, PageShell } from "@/components/layout/page-chrome";
import { PagedFooter } from "@/components/data/PagedFooter";
import { StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { Textarea } from "@/components/ui/textarea";
import { ContactsView } from "@/features/contacts/ContactsView";
import { CrawlRunPanel } from "@/features/research/CrawlRunPanel";
import { CrawlUrlInspector } from "@/features/research/CrawlUrlInspector";
import {
  DEFAULT_CRAWL_SETTINGS,
  NUMERIC_SETTINGS,
  formatBytes,
  lines,
  reasonLabel,
} from "@/features/research/settings";
import { WebsiteIntelligenceView } from "@/features/website/WebsiteIntelligenceView";
import { useT, type Translator } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { useLoaded } from "@/lib/useLoaded";
import { usePreferences } from "@/stores/preferences";

const POLL_MS = 1000;
const URL_PAGE_SIZE = 500;
const ACTIVE: CrawlRun["status"][] = ["queued", "running"];
const STATUSES: CrawlUrlStatus[] = ["fetched", "skipped", "failed", "queued"];

function buildColumns(t: Translator): ColumnDef<CrawlUrlItem>[] {
  return [
    {
      id: "url",
      accessorKey: "url",
      header: t("research.column.url"),
      size: 320,
      cell: ({ row }) => <Mono value={row.original.url} />,
    },
    {
      id: "status",
      accessorFn: (row) => t(`crawlStatus.${row.status}`),
      header: t("research.column.status"),
      size: 100,
      cell: ({ getValue }) => <Text value={getValue<string>()} />,
    },
    {
      id: "reason",
      accessorFn: (row) => (row.reason ? reasonLabel(t, row.reason) : ""),
      header: t("research.column.reason"),
      size: 190,
      cell: ({ getValue }) => <Text value={getValue<string>() || null} />,
    },
    {
      id: "method",
      accessorFn: (row) => (row.method ? t(`acquisitionMethod.${row.method}`) : ""),
      header: t("research.column.method"),
      size: 130,
      cell: ({ getValue }) => <Text value={getValue<string>() || null} />,
    },
    {
      id: "fallback",
      accessorFn: (row) => (row.fallback_reason ? reasonLabel(t, row.fallback_reason) : ""),
      header: t("research.column.fallback"),
      size: 170,
      cell: ({ getValue }) => <Text value={getValue<string>() || null} />,
    },
    {
      id: "kind",
      accessorFn: (row) => `${t(`crawlTarget.${row.target}`)} · ${t(`pageKind.${row.page_kind}`)}`,
      header: t("research.column.kind"),
      size: 150,
      cell: ({ getValue }) => <Text value={getValue<string>()} />,
    },
    {
      id: "http",
      accessorKey: "http_status",
      header: t("research.column.http"),
      size: 70,
      cell: ({ getValue }) => <Mono value={getValue<number | null>()} />,
    },
    {
      id: "bytes",
      accessorFn: (row) => (row.byte_size === null ? "" : formatBytes(row.byte_size)),
      header: t("research.column.bytes"),
      size: 90,
      cell: ({ getValue }) => <Mono value={getValue<string>() || null} />,
    },
    {
      id: "title",
      accessorKey: "title",
      header: t("research.column.title"),
      size: 220,
      cell: ({ getValue }) => <Text value={getValue<string | null>()} />,
    },
    {
      id: "updated",
      accessorKey: "updated_at",
      header: t("research.column.updated"),
      size: 170,
      cell: ({ getValue }) => <DateTime value={getValue<string>()} />,
    },
  ];
}

/**
 * Research Center: turns candidates and addresses into acquired pages and parsed content, and shows
 * what the crawler is doing while it does it. Nothing here judges a business; it only acquires.
 */
export function ResearchCenterScreen() {
  const t = useT();
  const inspectorSize = usePreferences((state) => state.inspectorSize);
  const columns = useMemo(() => buildColumns(t), [t]);
  const filterFields = useMemo(() => filterFieldsFrom(columns), [columns]);
  const [conditions, setConditions] = useState<FilterCondition[]>([]);

  const [campaignId, setCampaignId] = useState("");
  const [addresses, setAddresses] = useState("");
  const [settings, setSettings] = useState<CrawlSettings>(DEFAULT_CRAWL_SETTINGS);
  const [showLimits, setShowLimits] = useState(false);
  const [chosenRun, setChosenRun] = useState<string | null>(null);
  const [status, setStatus] = useState<CrawlUrlStatus | "">("");
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<"crawl" | "website" | "contacts">("crawl");

  const fetchCampaigns = useCallback(
    () => desktop.listCampaigns({ project_id: null, limit: 100, cursor: null }),
    [],
  );
  const campaigns = useLoaded(fetchCampaigns, "list_campaigns").data?.items ?? [];

  const fetchRuns = useCallback(
    () => desktop.listCrawlRuns({ project_id: null, campaign_id: null, limit: 10, cursor: null }),
    [],
  );
  const runList = useLoaded(fetchRuns, "list_crawl_runs");
  const runs = runList.data?.items ?? [];
  const jobId = chosenRun ?? runs[0]?.job_id ?? null;

  const fetchUrls = useCallback(async () => {
    if (jobId === null) return null;
    return desktop.listCrawlUrls({
      job_id: jobId,
      source_run_id: null,
      status: status || null,
      limit: URL_PAGE_SIZE,
      cursor: null,
    });
  }, [jobId, status]);
  const urlList = useLoaded(fetchUrls, "list_crawl_urls");
  const urls = urlList.data?.items ?? [];
  const error = runList.error ?? urlList.error;

  const { reload: reloadRuns } = runList;
  const { reload: reloadUrls } = urlList;
  const refresh = useCallback(async () => {
    await Promise.all([reloadRuns(), reloadUrls()]);
  }, [reloadRuns, reloadUrls]);

  // polled only while a crawl moves: the worker never pushes, and an idle screen asks nothing
  const active = runs.some((run) => ACTIVE.includes(run.status));
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [active, refresh]);

  const seeds = lines(addresses);
  const canStart = !busy && (campaignId !== "" || seeds.length > 0);

  const start = async () => {
    setBusy(true);
    try {
      const run = await desktop.startCrawl({
        project_id: null,
        campaign_id: campaignId || null,
        urls: seeds,
        settings,
      });
      setChosenRun(run.job_id);
      toast.success(t("research.started", { sites: run.site_count }));
      await refresh();
    } catch (cause) {
      toast.error(t("research.startFailed"), {
        description: toIpcError(cause, "start_crawl").message,
      });
    } finally {
      setBusy(false);
    }
  };

  const control = async (id: string, action: RunControlAction) => {
    setBusy(true);
    try {
      await desktop.controlCrawl({ job_id: id, action });
      await refresh();
    } catch (cause) {
      toast.error(t("discovery.controlFailed"), {
        description: toIpcError(cause, "control_crawl").message,
      });
    } finally {
      setBusy(false);
    }
  };

  const setNumber = (key: (typeof NUMERIC_SETTINGS)[number]["key"], value: number, scale = 1) =>
    setSettings((current) => ({ ...current, [key]: Math.round(value * scale) }));

  const emptyState = error ? (
    <StateBlock
      variant={error.code === "desktop_unavailable" ? "offline" : "error"}
      title={t("research.errorTitle")}
      body={error.message}
      action={
        <Button variant="outline" size="sm" onClick={() => void refresh()}>
          {t("common.retry")}
        </Button>
      }
    />
  ) : (
    <StateBlock variant="empty" title={t("research.emptyTitle")} body={t("research.emptyBody")} />
  );

  return (
    <PageShell>
      <PageActions>
        <div
          role="group"
          aria-label={t("research.view")}
          className="flex items-center gap-0.5 rounded-md border p-0.5"
        >
          {(["crawl", "website", "contacts"] as const).map((value) => (
            <Button
              key={value}
              variant={view === value ? "outline" : "ghost"}
              size="sm"
              className="h-6"
              aria-pressed={view === value}
              onClick={() => setView(value)}
            >
              {t(`research.view.${value}`)}
            </Button>
          ))}
        </div>
        {view === "crawl" && (
          <>
            <label className="flex items-center gap-1.5 text-xs">
              <span className="sr-only">{t("discovery.campaign")}</span>
              <select
                value={campaignId}
                onChange={(event) => setCampaignId(event.target.value)}
                aria-label={t("discovery.campaign")}
                className="h-7 rounded-md border bg-transparent px-2 text-xs"
              >
                <option value="">{t("research.addressesOnly")}</option>
                {campaigns.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <Button
              variant="outline"
              size="sm"
              className="h-7"
              aria-pressed={showLimits}
              onClick={() => setShowLimits((open) => !open)}
            >
              <SlidersHorizontal aria-hidden="true" />
              {t("research.limits")}
            </Button>
            <Button size="sm" className="h-7" disabled={!canStart} onClick={() => void start()}>
              <Play aria-hidden="true" />
              {t("research.start")}
            </Button>
          </>
        )}
      </PageActions>

      {view === "website" ? (
        <WebsiteIntelligenceView />
      ) : view === "contacts" ? (
        <ContactsView />
      ) : (
        <ResizablePanelGroup orientation="horizontal" className="min-h-0 flex-1">
          <ResizablePanel defaultSize={`${100 - inspectorSize}%`} minSize="40%">
            <div className="flex h-full min-h-0 flex-col">
              <div className="max-h-[45%] shrink-0 space-y-2 overflow-auto border-b p-2">
                <div className="space-y-1">
                  <Label htmlFor="crawl-addresses" className="text-xs">
                    {t("research.addressesLabel")}
                  </Label>
                  <Textarea
                    id="crawl-addresses"
                    rows={2}
                    value={addresses}
                    placeholder={t("research.addressesPlaceholder")}
                    onChange={(event) => setAddresses(event.target.value)}
                    className="font-mono text-xs"
                  />
                  <p className="text-[11px] text-muted-foreground">{t("research.policy")}</p>
                </div>

                {showLimits && (
                  <fieldset className="space-y-2 rounded-md border p-2">
                    <legend className="px-1 text-xs font-medium">{t("research.limits")}</legend>
                    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                      {NUMERIC_SETTINGS.map(({ key, label, min, max, scale = 1 }) => (
                        <label key={key} className="flex flex-col gap-0.5 text-[11px]">
                          {t(label)}
                          <input
                            type="number"
                            min={min}
                            max={max}
                            value={settings[key] / scale}
                            onChange={(event) =>
                              setNumber(
                                key,
                                Math.min(max, Math.max(min, Number(event.target.value) || min)),
                                scale,
                              )
                            }
                            className="h-7 rounded-md border bg-transparent px-2 font-mono text-xs"
                          />
                        </label>
                      ))}
                      <label className="flex flex-col gap-0.5 text-[11px]">
                        {t("research.setting.browser")}
                        <select
                          value={settings.browser}
                          onChange={(event) =>
                            setSettings((current) => ({
                              ...current,
                              browser: event.target.value as CrawlSettings["browser"],
                            }))
                          }
                          className="h-7 rounded-md border bg-transparent px-2 text-xs"
                        >
                          {(["auto", "never", "always"] as const).map((mode) => (
                            <option key={mode} value={mode}>
                              {t(`browserMode.${mode}`)}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                    <div className="grid gap-2 sm:grid-cols-2">
                      {(["include_paths", "exclude_paths"] as const).map((key) => (
                        <label key={key} className="flex flex-col gap-0.5 text-[11px]">
                          {t(key === "include_paths" ? "research.include" : "research.exclude")}
                          <Textarea
                            rows={2}
                            value={settings[key].join("\n")}
                            placeholder="/blog/*"
                            onChange={(event) =>
                              setSettings((current) => ({
                                ...current,
                                [key]: lines(event.target.value).slice(0, 25),
                              }))
                            }
                            className="font-mono text-xs"
                          />
                        </label>
                      ))}
                    </div>
                    <div className="flex flex-wrap gap-4">
                      {(["include_subdomains", "screenshots"] as const).map((key) => (
                        <div key={key} className="flex items-center gap-1.5">
                          <Checkbox
                            id={`crawl-${key}`}
                            checked={settings[key]}
                            onCheckedChange={(checked) =>
                              setSettings((current) => ({ ...current, [key]: checked === true }))
                            }
                          />
                          <Label htmlFor={`crawl-${key}`} className="text-xs">
                            {t(`research.setting.${key}`)}
                          </Label>
                        </div>
                      ))}
                    </div>
                  </fieldset>
                )}

                {runs.slice(0, 3).map((run) => (
                  <CrawlRunPanel
                    key={run.job_id}
                    run={run}
                    busy={busy}
                    selected={run.job_id === jobId}
                    onSelect={() => {
                      setChosenRun(run.job_id);
                      setSelected(null);
                    }}
                    onControl={(action) => void control(run.job_id, action)}
                  />
                ))}
              </div>

              <div className="flex shrink-0 items-center gap-2 border-b px-2 py-1.5">
                <label className="flex items-center gap-1.5 text-xs">
                  {t("research.column.status")}
                  <select
                    value={status}
                    onChange={(event) => setStatus(event.target.value as CrawlUrlStatus | "")}
                    className="h-7 rounded-md border bg-transparent px-2 text-xs"
                  >
                    <option value="">{t("research.allStatuses")}</option>
                    {STATUSES.map((value) => (
                      <option key={value} value={value}>
                        {t(`crawlStatus.${value}`)}
                      </option>
                    ))}
                  </select>
                </label>
                <span className="ml-auto text-[11px] text-muted-foreground">
                  {t("research.notJudged")}
                </span>
              </div>

              <div className="min-h-0 flex-1">
                <DataTable
                  tableId="crawl-urls"
                  columns={columns}
                  data={urls}
                  getRowId={(row) => row.id}
                  filterFields={filterFields}
                  conditions={conditions}
                  onConditionsChange={setConditions}
                  pinnedLeft={["url"]}
                  activeRowId={selected}
                  onActivateRow={setSelected}
                  emptyState={emptyState}
                />
              </div>
              <PagedFooter
                loaded={urls.length}
                total={urlList.data?.total ?? 0}
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
              {selected === null ? (
                <StateBlock
                  variant="empty"
                  title={t("research.inspectorEmptyTitle")}
                  body={t("research.inspectorEmptyBody")}
                />
              ) : (
                <CrawlUrlInspector key={selected} crawlUrlId={selected} />
              )}
            </aside>
          </ResizablePanel>
        </ResizablePanelGroup>
      )}
    </PageShell>
  );
}
