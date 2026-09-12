import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  CampaignListItem,
  CandidateListItem,
  DiscoveryRun,
  ImportPreview,
  RunControlAction,
} from "@lead/shared-types";
import type { ColumnDef } from "@tanstack/react-table";
import { Play, Upload } from "lucide-react";
import { toast } from "sonner";
import { DataTable } from "@/components/data/DataTable";
import { DateTime, Mono, Text } from "@/components/data/cells";
import { filterFieldsFrom, type FilterCondition } from "@/components/data/filters";
import { PageActions, PageShell } from "@/components/layout/page-chrome";
import { PagedFooter } from "@/components/data/PagedFooter";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { CandidateInspector } from "@/features/discovery/CandidateInspector";
import { RunPanel } from "@/features/discovery/RunPanel";
import { selectionFor } from "@/features/discovery/criteria";
import { useT, type Translator } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { useLoaded } from "@/lib/useLoaded";
import { usePreferences } from "@/stores/preferences";

const PAGE_SIZE = 200;
const RUN_POLL_MS = 1000;
const FILE_IMPORT_KEY = "file.import";
const MAX_IMPORT_CHARS = 4_000_000;
const ACTIVE: DiscoveryRun["status"][] = ["queued", "running"];

interface ImportInput {
  filename: string;
  content: string;
  preview: ImportPreview;
}

function buildColumns(t: Translator): ColumnDef<CandidateListItem>[] {
  return [
    {
      id: "name",
      accessorKey: "name_raw",
      header: t("discovery.column.name"),
      size: 260,
      cell: ({ row }) => (
        <span className="flex min-w-0 items-center gap-1.5">
          <Text value={row.original.name_raw} />
          {row.original.is_duplicate && (
            <span className="shrink-0 rounded border px-1 text-[10px] text-muted-foreground">
              {t("discovery.duplicate")}
            </span>
          )}
        </span>
      ),
    },
    {
      id: "locality",
      accessorKey: "locality",
      header: t("discovery.column.locality"),
      size: 140,
      cell: ({ getValue }) => <Text value={getValue<string | null>()} />,
    },
    {
      id: "region",
      accessorKey: "region",
      header: t("discovery.column.region"),
      size: 80,
      cell: ({ getValue }) => <Text value={getValue<string | null>()} />,
    },
    {
      id: "category",
      accessorKey: "category_raw",
      header: t("discovery.column.category"),
      size: 200,
      cell: ({ getValue }) => <Text value={getValue<string | null>()} />,
    },
    {
      id: "domain",
      accessorKey: "domain",
      header: t("discovery.column.domain"),
      size: 200,
      cell: ({ getValue }) => <Mono value={getValue<string | null>()} />,
    },
    {
      id: "source",
      accessorKey: "connector_name",
      header: t("discovery.column.source"),
      size: 200,
      cell: ({ getValue }) => <Text value={getValue<string>()} />,
    },
    {
      id: "discovered",
      accessorKey: "discovered_at",
      header: t("discovery.column.discovered"),
      size: 180,
      cell: ({ getValue }) => <DateTime value={getValue<string>()} />,
    },
  ];
}

/**
 * Discovery: pick a campaign, run it, watch it, and look at what came back. Candidates are
 * references a source returned - the interface never calls them leads.
 */
export function DiscoveryScreen() {
  const t = useT();
  const inspectorSize = usePreferences((state) => state.inspectorSize);
  const columns = useMemo(() => buildColumns(t), [t]);
  const filterFields = useMemo(() => filterFieldsFrom(columns), [columns]);
  const [conditions, setConditions] = useState<FilterCondition[]>([]);

  const [chosen, setChosen] = useState<string | null>(null);
  const [includeDuplicates, setIncludeDuplicates] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [imported, setImported] = useState<ImportInput | null>(null);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const fetchCampaigns = useCallback(
    () => desktop.listCampaigns({ project_id: null, limit: 100, cursor: null }),
    [],
  );
  const campaignList = useLoaded(fetchCampaigns, "list_campaigns");
  const campaigns: CampaignListItem[] = campaignList.data?.items ?? [];
  const campaignId = chosen ?? campaigns[0]?.id ?? null;
  const campaign = campaigns.find((item) => item.id === campaignId) ?? null;
  const needsFile =
    campaign !== null && selectionFor(campaign.criteria, FILE_IMPORT_KEY) !== undefined;

  const fetchRun = useCallback(async () => {
    if (campaignId === null) return { runs: [] as DiscoveryRun[], candidates: null };
    const [runPage, candidatePage] = await Promise.all([
      desktop.listDiscoveryRuns({
        project_id: null,
        campaign_id: campaignId,
        limit: 10,
        cursor: null,
      }),
      desktop.listCandidates({
        project_id: null,
        campaign_id: campaignId,
        source_run_id: null,
        search: null,
        include_duplicates: includeDuplicates,
        limit: PAGE_SIZE,
        cursor: null,
      }),
    ]);
    return { runs: runPage.items, candidates: candidatePage };
  }, [campaignId, includeDuplicates]);
  const { data, error: runError, reload: refresh } = useLoaded(fetchRun, "list_discovery_runs");
  const runs = data?.runs ?? [];
  const candidates = data?.candidates?.items ?? [];
  const total = data?.candidates?.total ?? 0;
  const error = campaignList.error ?? runError;

  // while a run is moving, the interface follows it; when nothing moves, it stops asking
  const active = runs.some((run) => ACTIVE.includes(run.status));
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => void refresh(), RUN_POLL_MS);
    return () => clearInterval(timer);
  }, [active, refresh]);

  const pickFile = async (file: File) => {
    if (file.size > MAX_IMPORT_CHARS) {
      toast.error(t("discovery.fileTooLarge"));
      return;
    }
    const content = await file.text();
    try {
      const preview = await desktop.previewImport({
        format: null,
        filename: file.name,
        content,
        mapping: null,
      });
      setImported({ filename: file.name, content, preview });
    } catch (cause) {
      toast.error(t("discovery.previewFailed"), {
        description: toIpcError(cause, "preview_import").message,
      });
    }
  };

  const start = async () => {
    if (campaignId === null) return;
    setBusy(true);
    try {
      await desktop.startDiscovery({
        campaign_id: campaignId,
        connector_keys: [],
        inputs: imported
          ? {
              [FILE_IMPORT_KEY]: {
                filename: imported.filename,
                format: imported.preview.format,
                content: imported.content,
                mapping: imported.preview.suggested_mapping,
              },
            }
          : {},
      });
      toast.success(t("discovery.started"));
      await refresh();
      await campaignList.reload();
    } catch (cause) {
      toast.error(t("discovery.startFailed"), {
        description: toIpcError(cause, "start_discovery").message,
      });
    } finally {
      setBusy(false);
    }
  };

  const control = async (jobId: string, action: RunControlAction) => {
    setBusy(true);
    try {
      await desktop.controlDiscovery({ job_id: jobId, action });
      await refresh();
    } catch (cause) {
      toast.error(t("discovery.controlFailed"), {
        description: toIpcError(cause, "control_discovery").message,
      });
    } finally {
      setBusy(false);
    }
  };

  const emptyState = error ? (
    <StateBlock
      variant={error.code === "desktop_unavailable" ? "offline" : "error"}
      title={t("discovery.errorTitle")}
      body={error.message}
      action={
        <Button variant="outline" size="sm" onClick={() => void refresh()}>
          {t("common.retry")}
        </Button>
      }
    />
  ) : campaigns.length === 0 ? (
    <StateBlock
      variant="empty"
      title={t("discovery.noCampaignsTitle")}
      body={t("discovery.noCampaignsBody")}
    />
  ) : (
    <StateBlock variant="empty" title={t("discovery.emptyTitle")} body={t("discovery.emptyBody")} />
  );

  return (
    <PageShell>
      <PageActions>
        <label className="flex items-center gap-1.5 text-xs">
          <span className="sr-only">{t("discovery.campaign")}</span>
          <select
            value={campaignId ?? ""}
            onChange={(event) => setChosen(event.target.value || null)}
            aria-label={t("discovery.campaign")}
            className="h-7 rounded-md border bg-transparent px-2 text-xs"
          >
            {campaigns.length === 0 && <option value="">{t("discovery.noCampaign")}</option>}
            {campaigns.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        {needsFile && (
          <>
            <input
              ref={fileInput}
              type="file"
              accept=".csv,.tsv,.json,.jsonl,.ndjson,text/csv,application/json"
              className="sr-only"
              aria-label={t("discovery.chooseFile")}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void pickFile(file);
              }}
            />
            <Button
              variant="outline"
              size="sm"
              className="h-7"
              onClick={() => fileInput.current?.click()}
            >
              <Upload aria-hidden="true" />
              {imported ? imported.filename : t("discovery.chooseFile")}
            </Button>
          </>
        )}
        <Button
          size="sm"
          className="h-7"
          disabled={busy || campaignId === null || (needsFile && imported === null)}
          onClick={() => void start()}
        >
          <Play aria-hidden="true" />
          {t("discovery.start")}
        </Button>
      </PageActions>

      <ResizablePanelGroup orientation="horizontal" className="min-h-0 flex-1">
        <ResizablePanel defaultSize={`${100 - inspectorSize}%`} minSize="40%">
          <div className="flex h-full min-h-0 flex-col">
            {imported && (
              <div className="border-b p-2">
                <InlineNotice variant={imported.preview.usable_rows === 0 ? "partial" : "success"}>
                  {t("discovery.previewSummary", {
                    file: imported.filename,
                    rows: imported.preview.total_rows,
                    usable: imported.preview.usable_rows,
                    columns: imported.preview.columns.join(", "),
                  })}
                </InlineNotice>
                {imported.preview.problems.length > 0 && (
                  <ul className="mt-1 space-y-0.5 text-[11px] text-warn">
                    {imported.preview.problems.slice(0, 5).map((problem, index) => (
                      <li key={index}>
                        {problem.row !== null && `${t("discovery.row")} ${problem.row}: `}
                        {problem.message}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            {runs.length > 0 && (
              <div className="max-h-64 shrink-0 space-y-2 overflow-auto border-b p-2">
                {runs.slice(0, 3).map((run) => (
                  <RunPanel
                    key={run.job_id}
                    run={run}
                    busy={busy}
                    onControl={(action) => void control(run.job_id, action)}
                  />
                ))}
              </div>
            )}

            <div className="flex shrink-0 items-center gap-2 border-b px-2 py-1.5">
              <Checkbox
                id="include-duplicates"
                checked={includeDuplicates}
                onCheckedChange={(checked) => setIncludeDuplicates(checked === true)}
              />
              <Label htmlFor="include-duplicates" className="text-xs">
                {t("discovery.includeDuplicates")}
              </Label>
              <span className="ml-auto text-[11px] text-muted-foreground">
                {t("discovery.candidatesAreNotLeads")}
              </span>
            </div>

            <div className="min-h-0 flex-1">
              <DataTable
                tableId="candidates"
                columns={columns}
                data={candidates}
                getRowId={(row) => row.id}
                filterFields={filterFields}
                conditions={conditions}
                onConditionsChange={setConditions}
                pinnedLeft={["name"]}
                activeRowId={selected}
                onActivateRow={setSelected}
                emptyState={emptyState}
              />
            </div>
            <PagedFooter
              loaded={candidates.length}
              total={total}
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
                title={t("discovery.inspectorEmptyTitle")}
                body={t("discovery.inspectorEmptyBody")}
              />
            ) : (
              <CandidateInspector key={selected} candidateId={selected} />
            )}
          </aside>
        </ResizablePanel>
      </ResizablePanelGroup>
    </PageShell>
  );
}
