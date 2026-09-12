import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { MatchListItem, MatchRisk, MatchSide, MatchStatus } from "@lead/shared-types";
import type { ColumnDef } from "@tanstack/react-table";
import { Play } from "lucide-react";
import { toast } from "sonner";
import { DataTable } from "@/components/data/DataTable";
import { Mono, TestDataBadge, Text } from "@/components/data/cells";
import { filterFieldsFrom, type FilterCondition } from "@/components/data/filters";
import { PagedFooter } from "@/components/data/PagedFooter";
import { PageActions, PageShell } from "@/components/layout/page-chrome";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { MatchReview } from "@/features/resolution/MatchReview";
import {
  MATCH_RISKS,
  MATCH_STATUSES,
  RISK_KEYS,
  STATUS_KEYS,
  reasonLabel,
} from "@/features/resolution/labels";
import { useT, type Translator } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { useKeysetPages } from "@/lib/useKeysetPages";
import { useLoaded } from "@/lib/useLoaded";
import { usePreferences } from "@/stores/preferences";

const PAGE_SIZE = 100;
const RUN_POLL_MS = 1000;

const place = (side: MatchSide) => [side.locality, side.region].filter(Boolean).join(", ");

function buildColumns(t: Translator): ColumnDef<MatchListItem>[] {
  return [
    {
      id: "pair",
      accessorFn: (row) => `${row.left.display_name} ↔ ${row.right.display_name}`,
      header: t("resolution.column.pair"),
      size: 380,
      cell: ({ row, getValue }) => (
        <span className="flex min-w-0 items-center gap-1.5">
          <Text value={getValue<string>()} />
          {row.original.is_synthetic && <TestDataBadge />}
        </span>
      ),
    },
    {
      id: "place",
      accessorFn: (row) =>
        place(row.left) === place(row.right)
          ? place(row.left) || null
          : `${place(row.left) || "—"} / ${place(row.right) || "—"}`,
      header: t("resolution.column.place"),
      size: 180,
      cell: ({ getValue }) => <Text value={getValue<string | null>()} />,
    },
    {
      id: "score",
      accessorFn: (row) => Math.round(row.score * 100),
      header: t("resolution.column.score"),
      size: 80,
      cell: ({ getValue }) => <Mono value={`${getValue<number>()}%`} />,
    },
    {
      id: "risk",
      accessorFn: (row) => t(RISK_KEYS[row.risk]),
      header: t("resolution.column.risk"),
      size: 90,
      cell: ({ getValue }) => <Text value={getValue<string>()} />,
    },
    {
      id: "status",
      accessorFn: (row) => t(STATUS_KEYS[row.status]),
      header: t("resolution.column.status"),
      size: 120,
      cell: ({ getValue }) => <Text value={getValue<string>()} />,
    },
    {
      id: "reasons",
      accessorFn: (row) =>
        row.reasons
          .slice(0, 3)
          .map((code) => reasonLabel(t, code))
          .join(", "),
      header: t("resolution.column.reasons"),
      size: 340,
      cell: ({ getValue }) => <Text value={getValue<string>() || null} />,
    },
  ];
}

/**
 * The review queue of possible duplicates: filter by status, risk, score and source, run the
 * resolution, and compare one pair side by side. Merges are never hidden and can all be undone.
 */
export function DuplicatesView({ toggle }: { toggle: ReactNode }) {
  const t = useT();
  const inspectorSize = usePreferences((state) => state.inspectorSize);
  const columns = useMemo(() => buildColumns(t), [t]);
  const filterFields = useMemo(() => filterFieldsFrom(columns), [columns]);
  const [conditions, setConditions] = useState<FilterCondition[]>([]);
  const [status, setStatus] = useState<MatchStatus | "">("pending");
  const [risk, setRisk] = useState<MatchRisk | "">("");
  const [minScore, setMinScore] = useState("");
  const [source, setSource] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const fetchSources = useCallback(() => desktop.listSources({ project_id: null }), []);
  const sources = useLoaded(fetchSources, "list_sources");

  const fetchPage = useCallback(
    (cursor: string | null) =>
      desktop.listMatches({
        project_id: null,
        status: status || null,
        risk: risk || null,
        min_score: minScore === "" ? null : Math.min(100, Math.max(0, Number(minScore))) / 100,
        connector_key: source || null,
        limit: PAGE_SIZE,
        cursor,
      }),
    [status, risk, minScore, source],
  );
  const pages = useKeysetPages(fetchPage, "list_matches");

  const fetchRuns = useCallback(
    () => desktop.listResolutionRuns({ project_id: null, limit: 5, cursor: null }),
    [],
  );
  const runs = useLoaded(fetchRuns, "list_resolution_runs");
  const latest = runs.data?.items[0] ?? null;
  const active = (runs.data?.items ?? []).some(
    (run) => run.status === "queued" || run.status === "running",
  );
  const reloadRuns = runs.reload;
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => void reloadRuns(), RUN_POLL_MS);
    return () => clearInterval(timer);
  }, [active, reloadRuns]);
  // when a run stops moving, the queue it produced is read again
  const wasActive = useRef(active);
  const reloadPages = pages.reload;
  useEffect(() => {
    if (wasActive.current && !active) void reloadPages();
    wasActive.current = active;
  }, [active, reloadPages]);

  const start = async () => {
    setStarting(true);
    try {
      const started = await desktop.startResolution({ project_id: null });
      toast.success(t("resolution.started", { n: started.items.length }));
      await reloadRuns();
    } catch (cause) {
      toast.error(t("resolution.startFailed"), {
        description: toIpcError(cause, "start_resolution").message,
      });
    } finally {
      setStarting(false);
    }
  };

  const emptyState = pages.error ? (
    <StateBlock
      variant={pages.error.code === "desktop_unavailable" ? "offline" : "error"}
      title={t("resolution.errorTitle")}
      body={pages.error.message}
      action={
        <Button variant="outline" size="sm" onClick={() => void pages.reload()}>
          {t("common.retry")}
        </Button>
      }
    />
  ) : (
    <StateBlock
      variant="empty"
      title={t("resolution.emptyTitle")}
      body={t("resolution.emptyBody")}
    />
  );

  const select = "h-7 rounded-md border bg-transparent px-2 text-xs";
  return (
    <PageShell>
      <PageActions>
        {toggle}
        <Button
          size="sm"
          className="h-7"
          disabled={starting || active}
          onClick={() => void start()}
        >
          <Play aria-hidden="true" />
          {t("resolution.run")}
        </Button>
      </PageActions>

      <ResizablePanelGroup orientation="horizontal" className="min-h-0 flex-1">
        <ResizablePanel defaultSize={`${100 - inspectorSize}%`} minSize="40%">
          <div className="flex h-full min-h-0 flex-col">
            {latest && (
              <div className="border-b p-2">
                <InlineNotice variant={active ? "processing" : "success"}>
                  {active
                    ? t("resolution.running", {
                        stage: latest.stage,
                        done: latest.progress_done,
                        total: latest.progress_total ?? "?",
                      })
                    : t("resolution.lastRun", {
                        records: latest.stats.records,
                        pairs: latest.stats.pairs_compared,
                        clusters: latest.stats.clusters,
                        review: latest.stats.review,
                      })}
                </InlineNotice>
              </div>
            )}
            <div className="flex shrink-0 flex-wrap items-center gap-2 border-b px-2 py-1.5 text-xs">
              <label className="flex items-center gap-1">
                {t("resolution.filterStatus")}
                <select
                  value={status}
                  onChange={(event) => setStatus(event.target.value as MatchStatus | "")}
                  className={select}
                >
                  <option value="">{t("resolution.any")}</option>
                  {MATCH_STATUSES.map((value) => (
                    <option key={value} value={value}>
                      {t(STATUS_KEYS[value])}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-1">
                {t("resolution.filterRisk")}
                <select
                  value={risk}
                  onChange={(event) => setRisk(event.target.value as MatchRisk | "")}
                  className={select}
                >
                  <option value="">{t("resolution.any")}</option>
                  {MATCH_RISKS.map((value) => (
                    <option key={value} value={value}>
                      {t(RISK_KEYS[value])}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-1">
                {t("resolution.filterScore")}
                <input
                  type="number"
                  min={0}
                  max={100}
                  step={5}
                  value={minScore}
                  onChange={(event) => setMinScore(event.target.value)}
                  className={`${select} w-16`}
                />
              </label>
              <label className="flex items-center gap-1">
                {t("resolution.filterSource")}
                <select
                  value={source}
                  onChange={(event) => setSource(event.target.value)}
                  className={select}
                >
                  <option value="">{t("resolution.any")}</option>
                  {(sources.data?.sources ?? []).map((info) => (
                    <option key={info.metadata.key} value={info.metadata.key}>
                      {info.metadata.name}
                    </option>
                  ))}
                </select>
              </label>
              <span className="ml-auto max-w-md text-[11px] text-muted-foreground">
                {t("resolution.explain")}
              </span>
            </div>

            <div className="min-h-0 flex-1">
              <DataTable
                tableId="duplicates"
                columns={columns}
                data={pages.items}
                getRowId={(row) => row.id}
                filterFields={filterFields}
                conditions={conditions}
                onConditionsChange={setConditions}
                pinnedLeft={["pair"]}
                activeRowId={selected}
                onActivateRow={setSelected}
                isLoading={pages.loading && pages.items.length === 0 && !pages.error}
                emptyState={emptyState}
              />
            </div>
            <PagedFooter
              loaded={pages.items.length}
              total={pages.total}
              hasMore={pages.nextCursor !== null}
              loading={pages.loading}
              onLoadMore={() => void pages.loadMore()}
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
                title={t("resolution.inspectorEmptyTitle")}
                body={t("resolution.inspectorEmptyBody")}
              />
            ) : (
              <MatchReview
                key={selected}
                matchId={selected}
                onChanged={() => void pages.reload()}
              />
            )}
          </aside>
        </ResizablePanel>
      </ResizablePanelGroup>
    </PageShell>
  );
}
