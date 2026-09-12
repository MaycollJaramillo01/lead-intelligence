import { useState, type ReactNode } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { PanelRight } from "lucide-react";
import { DataTable } from "@/components/data/DataTable";
import type { FilterCondition, FilterField } from "@/components/data/filters";
import { PageActions, PageShell } from "@/components/layout/page-chrome";
import { Button } from "@/components/ui/button";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { InspectorPanel } from "@/features/businesses/InspectorPanel";
import { useT } from "@/lib/i18n";
import { usePreferences } from "@/stores/preferences";
import { useUiStore } from "@/stores/ui";

interface TableWorkspaceProps<TData> {
  tableId: string;
  columns: ColumnDef<TData>[];
  data: TData[];
  getRowId: (row: TData) => string;
  filterFields: FilterField[];
  /** Shown when there are no rows: the empty state, or the error that explains why. */
  emptyState: ReactNode;
  isLoading?: boolean;
  /** Screen actions placed in the top bar next to the inspector toggle. */
  actions?: ReactNode;
  /** Below the table, e.g. the paging bar. */
  footer?: ReactNode;
  pinnedLeft?: string[];
}

/** Table plus resizable inspector: inspect one entity without losing the list. */
export function TableWorkspace<TData>({
  tableId,
  columns,
  data,
  getRowId,
  filterFields,
  emptyState,
  isLoading = false,
  actions,
  footer,
  pinnedLeft = ["name"],
}: TableWorkspaceProps<TData>) {
  const t = useT();
  const [conditions, setConditions] = useState<FilterCondition[]>([]);
  const inspectorOpen = useUiStore((state) => state.inspectorOpen);
  const inspectedId = useUiStore((state) => state.inspectedId);
  const inspect = useUiStore((state) => state.inspect);
  const toggleInspector = useUiStore((state) => state.toggleInspector);
  const inspectorSize = usePreferences((state) => state.inspectorSize);
  const setInspectorSize = usePreferences((state) => state.setInspectorSize);

  return (
    <PageShell>
      <PageActions>
        {actions}
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
            <div className="min-h-0 flex-1">
              <DataTable
                tableId={tableId}
                columns={columns}
                data={data}
                getRowId={getRowId}
                filterFields={filterFields}
                conditions={conditions}
                onConditionsChange={setConditions}
                pinnedLeft={pinnedLeft}
                activeRowId={inspectedId}
                onActivateRow={inspect}
                isLoading={isLoading}
                emptyState={emptyState}
              />
            </div>
            {footer}
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
    </PageShell>
  );
}
