// oxlint-disable jsx-a11y/control-has-associated-label -- the spacer cells below only exist
// to keep the virtualized scroll height correct; they sit inside aria-hidden rows.
import { useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
  type Column,
  type ColumnDef,
  type ColumnSizingState,
  type RowSelectionState,
  type SortingState,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import { FilterBar } from "@/components/data/FilterBar";
import {
  conditionsFilterFn,
  toColumnFilters,
  type FilterCondition,
  type FilterField,
} from "@/components/data/filters";
import { TableSkeleton } from "@/components/states/TableSkeleton";
import { Checkbox } from "@/components/ui/checkbox";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { usePreferences } from "@/stores/preferences";

/** Above this many rows the body is virtualized; below it the DOM stays simple and accessible. */
const VIRTUALIZE_ABOVE = 50;
const ROW_HEIGHT = { comfortable: 34, compact: 28 } as const;

export interface DataTableProps<TData> {
  /** Identifies the persisted column layout. */
  tableId: string;
  columns: ColumnDef<TData>[];
  data: TData[];
  getRowId: (row: TData) => string;
  filterFields: FilterField[];
  conditions: FilterCondition[];
  onConditionsChange: (conditions: FilterCondition[]) => void;
  emptyState: ReactNode;
  isLoading?: boolean;
  /** Columns pinned to the left, in order. */
  pinnedLeft?: string[];
  activeRowId?: string | null;
  onActivateRow?: (id: string | null) => void;
}

/** Defined outside the component so the cell renderers are not re-created on every render. */
function createSelectionColumn<TData>(labels: { all: string; row: string }): ColumnDef<TData> {
  return {
    id: "select",
    size: 36,
    enableSorting: false,
    enableHiding: false,
    enableResizing: false,
    header: ({ table }) => (
      <Checkbox
        checked={
          table.getIsAllRowsSelected()
            ? true
            : table.getIsSomeRowsSelected()
              ? "indeterminate"
              : false
        }
        onCheckedChange={(checked) => table.toggleAllRowsSelected(checked === true)}
        aria-label={labels.all}
      />
    ),
    cell: ({ row }) => (
      <Checkbox
        checked={row.getIsSelected()}
        onCheckedChange={(checked) => row.toggleSelected(checked === true)}
        onClick={(event) => event.stopPropagation()}
        aria-label={labels.row}
      />
    ),
  };
}

function pinnedStyle<TData>(column: Column<TData, unknown>) {
  if (!column.getIsPinned()) return undefined;
  return {
    position: "sticky" as const,
    left: column.getStart("left"),
    zIndex: 1,
  };
}

export function DataTable<TData>({
  tableId,
  columns,
  data,
  getRowId,
  filterFields,
  conditions,
  onConditionsChange,
  emptyState,
  isLoading = false,
  pinnedLeft = [],
  activeRowId = null,
  onActivateRow,
}: DataTableProps<TData>) {
  const t = useT();
  const density = usePreferences((state) => state.density);
  const storedVisibility = usePreferences((state) => state.columnVisibility[tableId]);
  const setColumnVisibility = usePreferences((state) => state.setColumnVisibility);
  const [sorting, setSorting] = useState<SortingState>([]);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [columnSizing, setColumnSizing] = useState<ColumnSizingState>({});
  const scrollRef = useRef<HTMLDivElement>(null);

  const selectionColumn = useMemo(
    () => createSelectionColumn<TData>({ all: t("common.selectAll"), row: t("common.selectRow") }),
    [t],
  );

  const allColumns = useMemo(() => [selectionColumn, ...columns], [columns, selectionColumn]);
  const columnFilters = useMemo(() => toColumnFilters(conditions), [conditions]);
  const filterFn = useMemo(() => conditionsFilterFn<TData>(), []);

  const table = useReactTable({
    data,
    columns: allColumns,
    getRowId,
    defaultColumn: { filterFn, minSize: 64, size: 160 },
    state: {
      sorting,
      rowSelection,
      columnSizing,
      columnFilters,
      columnVisibility: storedVisibility ?? {},
      columnPinning: { left: ["select", ...pinnedLeft], right: [] },
    },
    columnResizeMode: "onChange",
    enableRowSelection: true,
    onSortingChange: setSorting,
    onRowSelectionChange: setRowSelection,
    onColumnSizingChange: setColumnSizing,
    onColumnVisibilityChange: (updater) => {
      const current = storedVisibility ?? {};
      setColumnVisibility(tableId, typeof updater === "function" ? updater(current) : updater);
    },
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

  const rows = table.getRowModel().rows;
  const rowHeight = ROW_HEIGHT[density];
  const virtualized = rows.length > VIRTUALIZE_ABOVE;
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => rowHeight,
    overscan: 12,
  });
  const virtualItems = virtualized ? virtualizer.getVirtualItems() : [];
  const visibleRows = virtualized
    ? virtualItems.flatMap((item) => {
        const row = rows[item.index];
        return row ? [row] : [];
      })
    : rows;
  const paddingTop = virtualItems[0]?.start ?? 0;
  const lastItem = virtualItems[virtualItems.length - 1];
  const paddingBottom = lastItem ? virtualizer.getTotalSize() - lastItem.end : 0;

  const selectedCount = table.getSelectedRowModel().rows.length;

  const onRowKeyDown = (event: KeyboardEvent<HTMLTableRowElement>, id: string) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onActivateRow?.(id);
      return;
    }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const target = event.currentTarget;
    const sibling =
      event.key === "ArrowDown" ? target.nextElementSibling : target.previousElementSibling;
    if (sibling instanceof HTMLElement) sibling.focus();
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <FilterBar
        table={table}
        fields={filterFields}
        conditions={conditions}
        onConditionsChange={onConditionsChange}
        trailing={
          <span className="text-[11px] text-muted-foreground">
            {selectedCount > 0
              ? t("common.selectedCount", { count: selectedCount })
              : t("common.rowCount", { count: rows.length })}
          </span>
        }
      />

      <div className="relative min-h-0 flex-1">
        <div ref={scrollRef} className="absolute inset-0 overflow-auto">
          {isLoading ? (
            <TableSkeleton columns={Math.min(6, table.getVisibleLeafColumns().length)} />
          ) : (
            // fixed layout: columns keep their sizes, so a long cell truncates instead of
            // widening its column underneath the pinned one
            <table
              className="w-full table-fixed border-separate border-spacing-0 text-left"
              style={{ width: table.getTotalSize() }}
            >
              <thead className="sticky top-0 z-[2] bg-background">
                {table.getHeaderGroups().map((headerGroup) => (
                  <tr key={headerGroup.id}>
                    {headerGroup.headers.map((header) => {
                      const sorted = header.column.getIsSorted();
                      const canSort = header.column.getCanSort();
                      const label = String(header.column.columnDef.header ?? header.column.id);
                      return (
                        <th
                          key={header.id}
                          scope="col"
                          aria-sort={
                            sorted === "asc"
                              ? "ascending"
                              : sorted === "desc"
                                ? "descending"
                                : canSort
                                  ? "none"
                                  : undefined
                          }
                          className={cn(
                            "relative border-b bg-background px-[var(--cell-padding-x)] text-[11px] font-medium text-muted-foreground",
                            header.column.getIsPinned() && "bg-background",
                          )}
                          style={{
                            width: header.getSize(),
                            height: "var(--row-height)",
                            ...pinnedStyle(header.column),
                          }}
                        >
                          {header.isPlaceholder ? null : canSort ? (
                            <button
                              type="button"
                              onClick={header.column.getToggleSortingHandler()}
                              className="flex w-full items-center gap-1 text-left"
                            >
                              <span className="truncate">
                                {flexRender(header.column.columnDef.header, header.getContext())}
                              </span>
                              {sorted === "asc" ? (
                                <ArrowUp aria-hidden="true" className="size-3" />
                              ) : sorted === "desc" ? (
                                <ArrowDown aria-hidden="true" className="size-3" />
                              ) : (
                                <ChevronsUpDown aria-hidden="true" className="size-3 opacity-40" />
                              )}
                            </button>
                          ) : (
                            flexRender(header.column.columnDef.header, header.getContext())
                          )}
                          {header.column.getCanResize() && (
                            <div
                              role="separator"
                              aria-orientation="vertical"
                              aria-label={`${t("common.columns")}: ${label}`}
                              tabIndex={0}
                              onPointerDown={header.getResizeHandler()}
                              onKeyDown={(event) => {
                                const delta =
                                  event.key === "ArrowLeft"
                                    ? -16
                                    : event.key === "ArrowRight"
                                      ? 16
                                      : 0;
                                if (delta === 0) return;
                                event.preventDefault();
                                setColumnSizing((previous) => ({
                                  ...previous,
                                  [header.column.id]: Math.max(64, header.getSize() + delta),
                                }));
                              }}
                              className="absolute top-0 right-0 h-full w-1 cursor-col-resize select-none hover:bg-primary/50 focus-visible:bg-primary/70"
                            />
                          )}
                        </th>
                      );
                    })}
                  </tr>
                ))}
              </thead>
              <tbody>
                {rows.length === 0 ? null : (
                  <>
                    {paddingTop > 0 && (
                      <tr aria-hidden="true">
                        <td
                          colSpan={table.getVisibleLeafColumns().length}
                          style={{ height: paddingTop }}
                        />
                      </tr>
                    )}
                    {visibleRows.map((row) => {
                      const isActive = activeRowId === row.id;
                      return (
                        <tr
                          key={row.id}
                          tabIndex={0}
                          aria-selected={row.getIsSelected()}
                          onClick={() => onActivateRow?.(row.id)}
                          onKeyDown={(event) => onRowKeyDown(event, row.id)}
                          className={cn(
                            "cursor-default border-b outline-none hover:bg-accent/60 focus-visible:bg-accent",
                            isActive && "bg-accent",
                          )}
                          style={{ height: rowHeight }}
                        >
                          {row.getVisibleCells().map((cell) => (
                            <td
                              key={cell.id}
                              className={cn(
                                "truncate border-b px-[var(--cell-padding-x)]",
                                cell.column.getIsPinned() && "bg-background",
                              )}
                              style={{ width: cell.column.getSize(), ...pinnedStyle(cell.column) }}
                            >
                              {flexRender(cell.column.columnDef.cell, cell.getContext())}
                            </td>
                          ))}
                        </tr>
                      );
                    })}
                    {paddingBottom > 0 && (
                      <tr aria-hidden="true">
                        <td
                          colSpan={table.getVisibleLeafColumns().length}
                          style={{ height: paddingBottom }}
                        />
                      </tr>
                    )}
                  </>
                )}
              </tbody>
            </table>
          )}
        </div>
        {rows.length === 0 && !isLoading && (
          <div className="pointer-events-none absolute inset-x-0 top-[calc(var(--row-height)+1rem)] flex justify-center px-4">
            <div className="pointer-events-auto">{emptyState}</div>
          </div>
        )}
      </div>
    </div>
  );
}
