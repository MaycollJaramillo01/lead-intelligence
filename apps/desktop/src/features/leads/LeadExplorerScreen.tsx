import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import type {
  ExplorerCatalog,
  ExplorerField,
  ExplorerQuery,
  ExplorerRow,
  SavedViewItem,
} from "@lead/shared-types";
import { Columns3, Download, GitCompare, Layers, ListFilter, PanelRight, Tags } from "lucide-react";
import { Link, useSearchParams } from "react-router";
import { PageActions, PageShell } from "@/components/layout/page-chrome";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { InspectorPanel } from "@/features/businesses/InspectorPanel";
import { prefetchBusiness } from "@/features/businesses/prefetch";
import { BulkDialog } from "@/features/leads/BulkDialog";
import { ColumnsDialog } from "@/features/leads/ColumnsDialog";
import { CompareDialog } from "@/features/leads/CompareDialog";
import { ExportPreviewDialog } from "@/features/leads/ExportPreviewDialog";
import { FilterBuilder } from "@/features/leads/FilterBuilder";
import { LeadGrid } from "@/features/leads/LeadGrid";
import {
  conditionCount,
  definitionOf,
  emptyGroup,
  fieldMap,
  matchKey,
  orderedColumns,
  queryOf,
  readyFilters,
  reconcileColumns,
  stateOf,
  toggleSort,
  type ExplorerState,
} from "@/features/leads/query";
import { useExplorerSession } from "@/features/leads/store";
import { useLeadWindows } from "@/features/leads/useLeadWindows";
import { ViewsDialog } from "@/features/leads/ViewsDialog";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop } from "@/lib/ipc";
import { useLoaded } from "@/lib/useLoaded";
import { usePreferences } from "@/stores/preferences";
import { useUiStore } from "@/stores/ui";

const SEARCH_DELAY_MS = 300;
const TOP = { scrollTop: 0, active: 0 };

type DialogName = "filters" | "columns" | "views" | "bulk" | "export" | "compare";

/** The layout the analyst left when its schema is still current, else the catalog's default view. */
function initialState(catalog: ExplorerCatalog): ExplorerState {
  const { definition } = usePreferences.getState().explorer;
  const stored = definition?.schema_version === catalog.view_schema_version ? definition : null;
  const initial = stateOf(stored ?? catalog.default_view);
  return { ...initial, columns: reconcileColumns(initial.columns, catalog.default_view.columns) };
}

/**
 * The Lead Explorer (phase 12): every lead of a score run, filtered, searched and sorted in the store
 * and read window by window, with the business inspector beside it. The layout survives restarts;
 * the selection is kept by id while the analyst pages, sorts or opens a business. A `?run=` link
 * opens that run. Nothing here contacts anyone.
 */
export function LeadExplorerScreen() {
  const t = useT();
  const fetchCatalog = useCallback(
    () => desktop.getExplorerCatalog({ scope: "lead_explorer" }),
    [],
  );
  const { data: catalog, error, reload } = useLoaded(fetchCatalog, "get_explorer_catalog");
  if (catalog) return <Explorer catalog={catalog} />;
  return (
    <PageShell>
      {error ? (
        <StateBlock
          variant={error.code === "desktop_unavailable" ? "offline" : "error"}
          title={t("leads.errorTitle")}
          body={error.message}
          action={
            <Button variant="outline" size="sm" onClick={() => void reload()}>
              {t("common.retry")}
            </Button>
          }
        />
      ) : (
        <StateBlock variant="loading" title={t("state.loading")} />
      )}
    </PageShell>
  );
}

function Explorer({ catalog }: { catalog: ExplorerCatalog }) {
  const t = useT();
  const language = useLanguage();
  const [params, setParams] = useSearchParams();
  const runParam = params.get("run");
  const setLayout = usePreferences((state) => state.setExplorer);
  const inspectorSize = usePreferences((state) => state.inspectorSize);
  const setInspectorSize = usePreferences((state) => state.setInspectorSize);
  const inspectorOpen = useUiStore((state) => state.inspectorOpen);
  const inspectedId = useUiStore((state) => state.inspectedId);
  const inspect = useUiStore((state) => state.inspect);
  const toggleInspector = useUiStore((state) => state.toggleInspector);
  const selected = useExplorerSession((state) => state.selected);
  const allMatching = useExplorerSession((state) => state.allMatching);
  const toggle = useExplorerSession((state) => state.toggle);
  const selectAll = useExplorerSession((state) => state.selectAll);
  const clearSelection = useExplorerSession((state) => state.clear);
  const keepPosition = useExplorerSession((state) => state.keep);

  const [state, setState] = useState(() => initialState(catalog));
  const [searchText, setSearchText] = useState(state.search);
  const [viewId, setViewId] = useState(() => usePreferences.getState().explorer.viewId);
  const [viewName, setViewName] = useState<string | null>(null);
  const [dialog, setDialog] = useState<DialogName | null>(null);
  const [saved] = useState(() => useExplorerSession.getState().position);

  // a `?run=` link wins over the run the layout remembers
  const effective = useMemo(
    () => (runParam ? { ...state, scoreRunId: runParam } : state),
    [state, runParam],
  );

  // the search queries once the analyst stops typing
  useEffect(() => {
    const timer = setTimeout(
      () =>
        setState((current) =>
          current.search === searchText ? current : { ...current, search: searchText },
        ),
      SEARCH_DELAY_MS,
    );
    return () => clearTimeout(timer);
  }, [searchText]);

  useEffect(() => {
    setLayout({ definition: definitionOf(effective), viewId });
  }, [effective, viewId, setLayout]);

  const fields = useMemo(() => fieldMap(catalog), [catalog]);
  const queryText = JSON.stringify(queryOf(effective, fields, 0, true));
  const query = useMemo(() => JSON.parse(queryText) as ExplorerQuery, [queryText]);
  const windows = useLeadWindows(query);
  const { total, run } = windows;
  const matching = matchKey(query);
  const allSelected = allMatching === matching;

  // "every matching lead" belongs to one set of filters and one search
  useEffect(() => {
    if (allMatching !== null && allMatching !== matching) selectAll(null);
  }, [matching, allMatching, selectAll]);

  const columns = useMemo(() => orderedColumns(state.columns), [state.columns]);
  const exportFields = useMemo<ExplorerField[]>(
    () => columns.map((column) => column.field),
    [columns],
  );
  const isSelected = useCallback((row: ExplorerRow) => selected.has(row.business_id), [selected]);
  const sortable = useCallback(
    (field: ExplorerField) => fields.get(field)?.sortable ?? false,
    [fields],
  );
  const onPosition = useCallback(
    (scrollTop: number, active: number) => keepPosition({ key: queryText, scrollTop, active }),
    [queryText, keepPosition],
  );

  const clearRunLink = () => {
    if (runParam) setParams({}, { replace: true });
  };

  const sortBy = (field: ExplorerField, additive: boolean) =>
    setState((current) => ({
      ...current,
      sort: toggleSort(current.sort, field, additive, catalog.max_sort),
    }));

  const open = (row: ExplorerRow, index: number) => {
    inspect(row.business_id);
    const next = windows.rowAt(index + 1);
    if (next) prefetchBusiness(next.business_id);
  };

  const newestRun = () => {
    setState((current) => ({ ...current, scoreRunId: null }));
    clearRunLink();
  };

  const resetLayout = () => {
    setState(stateOf(catalog.default_view));
    setSearchText("");
    setViewId(null);
    setViewName(null);
    clearRunLink();
  };

  const applyView = (item: SavedViewItem) => {
    const next = stateOf(item.definition);
    setState({ ...next, columns: reconcileColumns(next.columns, catalog.default_view.columns) });
    setSearchText(next.search);
    setViewId(item.view.id);
    setViewName(item.view.name);
    setDialog(null);
    clearRunLink();
  };

  const close = (isOpen: boolean) => {
    if (!isOpen) setDialog(null);
  };

  const filterCount = conditionCount(readyFilters(state.filters, fields));
  const selectedCount = allSelected ? (total ?? 0) : selected.size;
  const tooMany = !allSelected && selected.size > catalog.bulk_max_ids;
  const canCompare = !allSelected && selected.size >= 2 && selected.size <= catalog.max_compared;

  let body: ReactNode = <StateBlock variant="loading" title={t("state.loading")} />;
  if (windows.error) {
    body = (
      <StateBlock
        variant={windows.error.code === "desktop_unavailable" ? "offline" : "error"}
        title={t("leads.errorTitle")}
        body={windows.error.message}
        action={
          <div className="flex flex-wrap justify-center gap-1">
            <Button variant="outline" size="sm" onClick={windows.reload}>
              {t("common.retry")}
            </Button>
            {effective.scoreRunId !== null && (
              <Button variant="ghost" size="sm" onClick={newestRun}>
                {t("explorer.newestRun")}
              </Button>
            )}
            <Button variant="ghost" size="sm" onClick={resetLayout}>
              {t("explorer.resetLayout")}
            </Button>
          </div>
        }
      />
    );
  } else if (total === 0) {
    body =
      run === null ? (
        <StateBlock
          variant="empty"
          title={t("leads.emptyTitle")}
          body={t("leads.emptyBody")}
          action={
            <Link to="/scoring-models" className="text-xs text-primary hover:underline">
              {t("nav.scoring_models")}
            </Link>
          }
        />
      ) : (
        <StateBlock
          variant="empty"
          title={t("explorer.noMatchesTitle")}
          body={t("explorer.noMatchesBody")}
          action={
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setState((current) => ({ ...current, filters: emptyGroup(), search: "" }));
                setSearchText("");
              }}
            >
              {t("filter.clear")}
            </Button>
          }
        />
      );
  } else if (total !== null) {
    body = (
      <LeadGrid
        key={queryText}
        label={t("explorer.gridLabel")}
        columns={columns}
        total={total}
        rowAt={windows.rowAt}
        ensure={windows.ensure}
        sort={state.sort}
        sortable={sortable}
        onSort={sortBy}
        isSelected={isSelected}
        allSelected={allSelected}
        onToggle={(row) => toggle(row.business_id)}
        onOpen={open}
        initial={saved.key === queryText ? saved : TOP}
        onPosition={onPosition}
      />
    );
  }

  return (
    <PageShell>
      <PageActions>
        <Button variant="outline" size="sm" className="h-7" onClick={() => setDialog("views")}>
          <Layers aria-hidden="true" />
          {t("explorer.views")}
        </Button>
        <Button variant="outline" size="sm" className="h-7" onClick={() => setDialog("columns")}>
          <Columns3 aria-hidden="true" />
          {t("explorer.columns")}
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-7"
          disabled={!total}
          onClick={() => setDialog("export")}
        >
          <Download aria-hidden="true" />
          {t("explorer.export")}
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-7"
          disabled={selectedCount === 0 || tooMany}
          title={tooMany ? t("explorer.tooManySelected", { n: catalog.bulk_max_ids }) : undefined}
          onClick={() => setDialog("bulk")}
        >
          <Tags aria-hidden="true" />
          {t("explorer.bulk")}
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-7"
          disabled={!canCompare}
          title={canCompare ? undefined : t("compare.pick")}
          onClick={() => setDialog("compare")}
        >
          <GitCompare aria-hidden="true" />
          {t("explorer.compare")}
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-7"
          aria-pressed={inspectorOpen}
          onClick={toggleInspector}
        >
          <PanelRight aria-hidden="true" />
          {inspectorOpen ? t("common.closeInspector") : t("common.openInspector")}
        </Button>
      </PageActions>

      <ResizablePanelGroup orientation="horizontal" className="min-h-0 flex-1">
        <ResizablePanel defaultSize={`${100 - inspectorSize}%`} minSize="40%">
          <div className="flex h-full min-h-0 flex-col">
            <div className="shrink-0 space-y-1.5 border-b p-2 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <Input
                  type="search"
                  aria-label={t("explorer.searchLabel")}
                  placeholder={t("explorer.searchPlaceholder")}
                  value={searchText}
                  maxLength={200}
                  onChange={(event) => setSearchText(event.target.value)}
                  className="h-7 w-56 text-xs"
                />
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7"
                  onClick={() => setDialog("filters")}
                >
                  <ListFilter aria-hidden="true" />
                  {filterCount > 0
                    ? t("explorer.filtersCount", { n: filterCount })
                    : t("explorer.filters")}
                </Button>
                <span aria-live="polite" className="text-[11px] text-muted-foreground">
                  {total === null ? t("explorer.counting") : t("explorer.total", { total })}
                </span>
                {state.sort.length === 0 && (
                  <span className="text-[11px] text-muted-foreground">
                    {t("explorer.rankOrder")}
                  </span>
                )}
                {viewName && (
                  <span className="rounded border px-1 text-[11px]">
                    {t("explorer.viewName", { name: viewName })}
                  </span>
                )}
                {selectedCount > 0 && (
                  <span className="flex flex-wrap items-center gap-1">
                    <span className="font-medium">
                      {allSelected
                        ? t("explorer.allMatching", { total: total ?? 0 })
                        : t("explorer.selected", { n: selected.size })}
                    </span>
                    {!allSelected &&
                      total !== null &&
                      total > selected.size &&
                      total <= catalog.bulk_max_matching && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-6 px-1.5 text-primary"
                          onClick={() => selectAll(matching)}
                        >
                          {t("explorer.selectAllMatching", { total })}
                        </Button>
                      )}
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 px-1.5"
                      onClick={clearSelection}
                    >
                      {t("explorer.clearSelection")}
                    </Button>
                  </span>
                )}
                <span className="ml-auto hidden text-[11px] text-muted-foreground xl:inline">
                  {t("explorer.keyboardHint")}
                </span>
              </div>
              {run && (
                <p className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                  <span>
                    {t("scoring.runLine", {
                      icp: run.icp_profile_name,
                      icpVersion: run.icp_profile_version,
                      model: run.scoring_model_name,
                      modelVersion: run.scoring_model_version,
                    })}
                  </span>
                  <span>
                    {run.finished_at
                      ? t("scoring.finishedAt", { time: formatDateTime(run.finished_at, language) })
                      : t("scoring.runOpen")}
                  </span>
                  {effective.scoreRunId !== null && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-5 px-1 text-[11px] text-primary"
                      onClick={newestRun}
                    >
                      {t("explorer.newestRun")}
                    </Button>
                  )}
                </p>
              )}
              {windows.stale && (
                <InlineNotice variant="processing">{t("explorer.refreshing")}</InlineNotice>
              )}
              {windows.failed > 0 && (
                <InlineNotice
                  variant="partial"
                  action={
                    <Button variant="outline" size="sm" className="h-6" onClick={windows.reload}>
                      {t("common.retry")}
                    </Button>
                  }
                >
                  {t("explorer.windowError")}
                </InlineNotice>
              )}
            </div>
            <div className="relative min-h-0 flex-1">{body}</div>
          </div>
        </ResizablePanel>
        {inspectorOpen && (
          <>
            <ResizableHandle withHandle />
            <ResizablePanel
              defaultSize={`${inspectorSize}%`}
              minSize="20%"
              maxSize="60%"
              onResize={(size) => {
                const next = Math.round(size.asPercentage);
                if (next !== inspectorSize) setInspectorSize(next);
              }}
            >
              <InspectorPanel entityId={inspectedId} />
            </ResizablePanel>
          </>
        )}
      </ResizablePanelGroup>

      {dialog === "filters" && (
        <FilterBuilder
          onOpenChange={close}
          catalog={catalog}
          filters={state.filters}
          onApply={(filters) => setState((current) => ({ ...current, filters }))}
        />
      )}
      {dialog === "columns" && (
        <ColumnsDialog
          open
          onOpenChange={close}
          columns={state.columns}
          onChange={(next) => setState((current) => ({ ...current, columns: next }))}
        />
      )}
      {dialog === "views" && (
        <ViewsDialog
          open
          onOpenChange={close}
          definition={{ ...definitionOf(effective), filters: readyFilters(state.filters, fields) }}
          activeViewId={viewId}
          onApply={applyView}
          onSaved={(item) => {
            setViewId(item.view.id);
            setViewName(item.view.name);
          }}
        />
      )}
      {dialog === "bulk" && (
        <BulkDialog
          open
          onOpenChange={close}
          target={
            allSelected
              ? { kind: "query", query, total: total ?? 0 }
              : { kind: "ids", ids: [...selected] }
          }
          onApplied={windows.reload}
        />
      )}
      {dialog === "export" && (
        <ExportPreviewDialog onOpenChange={close} query={query} columns={exportFields} />
      )}
      {dialog === "compare" && (
        <CompareDialog
          onOpenChange={close}
          ids={[...selected]}
          scoreRunId={effective.scoreRunId ?? run?.score_run_id ?? null}
        />
      )}
    </PageShell>
  );
}
