import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
} from "react";
import type { ExplorerField, ExplorerRow, ExplorerSort, ViewColumn } from "@lead/shared-types";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import { TestDataBadge } from "@/components/data/cells";
import { ESTIMATE_FIELDS, FIELD_KEYS, cellText } from "@/features/leads/labels";
import { rowWindow, scrollTopFor } from "@/features/leads/query";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { usePreferences } from "@/stores/preferences";

const ROW_HEIGHT = { comfortable: 34, compact: 28 } as const;
const SELECT_WIDTH = 36;
const OVERSCAN = 8;
/** jsdom lays nothing out: without a measured height the grid assumes this one. */
const FALLBACK_VIEWPORT = 600;
const DEFAULT_WIDTH = 104;
const WIDTHS: Partial<Record<ExplorerField, number>> = {
  name: 320,
  locality: 130,
  region: 72,
  category: 150,
  domain: 190,
  lead_stage: 130,
  research_status: 130,
  assignee: 130,
  tag: 150,
  tier: 120,
  rank: 64,
  offer: 200,
  top_problem: 180,
  problem: 220,
  latino_status: 150,
  main_channel: 120,
  channel_status: 130,
  audit_status: 120,
  business_health: 150,
  buying_capacity: 150,
};
const NUMERIC: ReadonlySet<ExplorerField> = new Set<ExplorerField>([
  "rank",
  "total_score",
  "confidence",
  "data_coverage",
  "icp_match",
  "opportunity_score",
  "digital_gap",
  "business_health",
  "buying_capacity",
  "contactability",
  "commercial_intent",
  "data_confidence",
  "latino_confidence",
  "usable_channels",
  "facts_age_days",
  "conflicts",
]);

const widthOf = (column: ViewColumn) => column.width ?? WIDTHS[column.field] ?? DEFAULT_WIDTH;

function Badge({ tone, children }: { tone: "warn" | "danger"; children: ReactNode }) {
  return (
    <span
      className={cn(
        "shrink-0 rounded border px-1 text-[10px] leading-4",
        tone === "danger" ? "border-danger/60 text-danger" : "border-warn/60 text-warn",
      )}
    >
      {children}
    </span>
  );
}

function Cell({ row, field }: { row: ExplorerRow; field: ExplorerField }) {
  const t = useT();
  if (field === "name") {
    return (
      <span className="flex min-w-0 items-center gap-1">
        <span className="truncate font-medium">{row.display_name}</span>
        {row.is_synthetic && <TestDataBadge />}
        {row.suppressed && <Badge tone="danger">{t("explorer.badge.suppressed")}</Badge>}
        {row.stale && <Badge tone="warn">{t("explorer.badge.stale")}</Badge>}
        {row.partial && <Badge tone="warn">{t("explorer.badge.partial")}</Badge>}
        {row.conflicts > 0 && (
          <Badge tone="warn">{t("explorer.badge.conflicts", { n: row.conflicts })}</Badge>
        )}
      </span>
    );
  }
  const text = cellText(t, row, field);
  if (text === null) return <span className="text-muted-foreground">—</span>;
  return (
    <span
      title={field === "top_problem" ? (row.top_reason ?? undefined) : undefined}
      className={cn(
        "flex min-w-0 flex-1 items-center gap-1",
        NUMERIC.has(field) && "justify-end font-mono tabular-nums",
      )}
    >
      <span className="truncate">{text}</span>
      {ESTIMATE_FIELDS.has(field) && (
        <span className="shrink-0 font-sans text-[10px] text-muted-foreground">
          {t("explorer.badge.estimate")}
        </span>
      )}
    </span>
  );
}

interface LeadGridProps {
  label: string;
  /** The visible columns, pinned ones first. */
  columns: readonly ViewColumn[];
  total: number;
  rowAt: (index: number) => ExplorerRow | undefined;
  ensure: (first: number, last: number) => void;
  sort: readonly ExplorerSort[];
  sortable: (field: ExplorerField) => boolean;
  onSort: (field: ExplorerField, additive: boolean) => void;
  isSelected: (row: ExplorerRow) => boolean;
  /** Every lead of the query is selected: a single row cannot be toggled then. */
  allSelected: boolean;
  onToggle: (row: ExplorerRow) => void;
  onOpen: (row: ExplorerRow, index: number) => void;
  initial: { scrollTop: number; active: number };
  onPosition: (scrollTop: number, active: number) => void;
}

/**
 * The Lead Explorer's grid (phase 12). Only the rows in view exist in the DOM, and only their windows
 * are asked of the store, so a query matching a million leads renders as fast as one matching ten.
 * The grid keeps the focus and points at the active row: arrows, Page Up/Down, Home and End move,
 * Space selects by id and Enter opens. A header click sorts by that column; Shift adds a key.
 */
export function LeadGrid({
  label,
  columns,
  total,
  rowAt,
  ensure,
  sort,
  sortable,
  onSort,
  isSelected,
  allSelected,
  onToggle,
  onOpen,
  initial,
  onPosition,
}: LeadGridProps) {
  const t = useT();
  const id = useId();
  const density = usePreferences((state) => state.density);
  const rowHeight = ROW_HEIGHT[density];
  const scroller = useRef<HTMLDivElement>(null);
  const [restored] = useState(initial);
  const [scrollTop, setScrollTop] = useState(restored.scrollTop);
  const [active, setActive] = useState(restored.active);
  const [measured, setMeasured] = useState(0);

  useEffect(() => {
    const element = scroller.current;
    if (!element) return;
    element.scrollTop = restored.scrollTop;
    const observer = new ResizeObserver(() => setMeasured(element.clientHeight));
    observer.observe(element);
    return () => observer.disconnect();
  }, [restored]);

  // the sticky header takes one row of the measured height
  const viewport = Math.max(rowHeight, (measured || FALLBACK_VIEWPORT) - rowHeight);
  const { height, offset, start, first, last } = rowWindow(
    total,
    rowHeight,
    scrollTop,
    viewport,
    OVERSCAN,
  );
  const current = Math.max(0, Math.min(active, total - 1));

  useEffect(() => {
    if (last >= first) ensure(first, last);
  }, [ensure, first, last]);

  useEffect(() => {
    onPosition(offset, current);
  }, [onPosition, offset, current]);

  const widths = columns.map(widthOf);
  const totalWidth = SELECT_WIDTH + widths.reduce((sum, width) => sum + width, 0);
  const lefts: number[] = [];
  let left = SELECT_WIDTH;
  columns.forEach((column, position) => {
    lefts.push(left);
    if (column.pinned) left += widths[position] ?? 0;
  });

  const moveTo = (index: number) => {
    if (total === 0) return;
    const next = Math.min(Math.max(index, 0), total - 1);
    setActive(next);
    const top = scrollTopFor(next, total, rowHeight, viewport, offset);
    if (top !== offset) {
      setScrollTop(top);
      if (scroller.current) scroller.current.scrollTop = top;
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    // keys pressed on a header button keep their own meaning
    if (event.target !== event.currentTarget || event.altKey) return;
    const page = Math.max(1, Math.floor(viewport / rowHeight) - 1);
    const row = rowAt(current);
    switch (event.key) {
      case "ArrowDown":
        moveTo(current + 1);
        break;
      case "ArrowUp":
        moveTo(current - 1);
        break;
      case "PageDown":
        moveTo(current + page);
        break;
      case "PageUp":
        moveTo(current - page);
        break;
      case "Home":
        moveTo(0);
        break;
      case "End":
        moveTo(total - 1);
        break;
      case " ":
        if (row && !allSelected) onToggle(row);
        break;
      case "Enter":
        if (row) onOpen(row, current);
        break;
      default:
        return;
    }
    event.preventDefault();
  };

  const onClick = (event: MouseEvent<HTMLDivElement>) => {
    const target = event.target instanceof Element ? event.target.closest("[data-index]") : null;
    if (!(target instanceof HTMLElement)) return;
    const index = Number(target.dataset.index);
    setActive(index);
    scroller.current?.focus({ preventScroll: true });
    const row = rowAt(index);
    if (row) onOpen(row, index);
  };

  const rows: ReactNode[] = [];
  for (let index = first; index <= last; index += 1) {
    const row = rowAt(index);
    const checked = row !== undefined && (allSelected || isSelected(row));
    rows.push(
      <div
        key={index}
        id={`${id}-row-${index}`}
        data-index={index}
        role="row"
        aria-rowindex={index + 2}
        aria-selected={row ? checked : undefined}
        aria-busy={row ? undefined : true}
        className={cn(
          "absolute left-0 flex cursor-default border-b bg-background text-xs hover:bg-accent/60",
          index === current && "bg-accent hover:bg-accent",
        )}
        style={{ top: offset + (index - start) * rowHeight, height: rowHeight, width: totalWidth }}
      >
        <div
          role="gridcell"
          aria-colindex={1}
          className="sticky left-0 z-[1] flex shrink-0 items-center justify-center bg-inherit"
          style={{ width: SELECT_WIDTH }}
        >
          <input
            type="checkbox"
            tabIndex={-1}
            checked={checked}
            disabled={!row || allSelected}
            aria-label={t("explorer.selectRow", {
              name: row?.display_name ?? t("explorer.loadingRow"),
            })}
            // the grid keeps the focus, so the arrows still move after a click here
            onMouseDown={(event) => event.preventDefault()}
            onClick={(event) => event.stopPropagation()}
            onChange={() => {
              if (row) onToggle(row);
            }}
          />
        </div>
        {columns.map((column, position) => (
          <div
            key={column.field}
            role="gridcell"
            aria-colindex={position + 2}
            className={cn(
              "flex shrink-0 items-center overflow-hidden px-[var(--cell-padding-x)]",
              column.pinned && "sticky z-[1] bg-inherit",
            )}
            style={{ width: widths[position], left: column.pinned ? lefts[position] : undefined }}
          >
            {row ? (
              <Cell row={row} field={column.field} />
            ) : (
              position === 0 && (
                <span className="text-muted-foreground">{t("explorer.loadingRow")}</span>
              )
            )}
          </div>
        ))}
      </div>,
    );
  }

  return (
    <div
      ref={scroller}
      role="grid"
      aria-label={label}
      aria-rowcount={total + 1}
      aria-colcount={columns.length + 1}
      aria-multiselectable="true"
      aria-activedescendant={
        total > 0 && current >= first && current <= last ? `${id}-row-${current}` : undefined
      }
      tabIndex={0}
      onKeyDown={onKeyDown}
      onClick={onClick}
      onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
      className="absolute inset-0 overflow-auto outline-none focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-inset"
    >
      <div role="rowgroup" className="sticky top-0 z-[2]" style={{ width: totalWidth }}>
        <div
          role="row"
          aria-rowindex={1}
          className="flex border-b bg-background text-[11px] font-medium text-muted-foreground"
          style={{ height: rowHeight, width: totalWidth }}
        >
          <div
            role="columnheader"
            aria-colindex={1}
            className="sticky left-0 z-[1] flex shrink-0 items-center justify-center bg-background"
            style={{ width: SELECT_WIDTH }}
          >
            <span className="sr-only">{t("common.selectRow")}</span>
          </div>
          {columns.map((column, position) => {
            const index = sort.findIndex((key) => key.field === column.field);
            const key = sort[index];
            const name = t(FIELD_KEYS[column.field]);
            return (
              <div
                key={column.field}
                role="columnheader"
                aria-colindex={position + 2}
                aria-sort={
                  index === 0 && key
                    ? key.direction === "asc"
                      ? "ascending"
                      : "descending"
                    : undefined
                }
                className={cn(
                  "flex shrink-0 items-center px-[var(--cell-padding-x)]",
                  column.pinned && "sticky z-[1] bg-background",
                )}
                style={{
                  width: widths[position],
                  left: column.pinned ? lefts[position] : undefined,
                }}
              >
                {sortable(column.field) ? (
                  <button
                    type="button"
                    title={t("explorer.sortHint")}
                    onClick={(event) => onSort(column.field, event.shiftKey)}
                    className="flex w-full min-w-0 items-center gap-1 text-left"
                  >
                    <span className="truncate">{name}</span>
                    {key ? (
                      <>
                        {key.direction === "asc" ? (
                          <ArrowUp aria-hidden="true" className="size-3 shrink-0" />
                        ) : (
                          <ArrowDown aria-hidden="true" className="size-3 shrink-0" />
                        )}
                        {sort.length > 1 && (
                          <span aria-hidden="true" className="font-mono text-[9px]">
                            {index + 1}
                          </span>
                        )}
                        <span className="sr-only">
                          {t(
                            key.direction === "asc" ? "explorer.sortedAsc" : "explorer.sortedDesc",
                            {
                              n: index + 1,
                            },
                          )}
                        </span>
                      </>
                    ) : (
                      <ChevronsUpDown aria-hidden="true" className="size-3 shrink-0 opacity-40" />
                    )}
                  </button>
                ) : (
                  <span className="truncate">{name}</span>
                )}
              </div>
            );
          })}
        </div>
      </div>
      <div role="rowgroup" className="relative" style={{ height, width: totalWidth }}>
        {rows}
      </div>
    </div>
  );
}
