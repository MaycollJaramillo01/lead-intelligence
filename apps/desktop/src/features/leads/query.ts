import type {
  ExplorerCatalog,
  ExplorerCondition,
  ExplorerField,
  ExplorerFieldInfo,
  ExplorerGroup,
  ExplorerQuery,
  ExplorerSort,
  ViewColumn,
  ViewDefinition,
} from "@lead/shared-types";

/** Rows asked for per window: the grid reads the windows it shows, never every lead. */
export const WINDOW_SIZE = 100;
/** Windows kept in memory for the current query; the least recently used is dropped first. */
export const WINDOW_CACHE = 12;
const MAX_VALUES = 50;
const MAX_TEXT = 200;

export type FilterNode = ExplorerCondition | ExplorerGroup;
/** Child indexes from the root group down to a node; the root is `[]`. */
export type NodePath = readonly number[];
export type FieldMap = ReadonlyMap<ExplorerField, ExplorerFieldInfo>;

const UNARY: ReadonlySet<string> = new Set(["is_null", "is_not_null", "is_true", "is_false"]);
const LISTS: ReadonlySet<string> = new Set(["in", "not_in"]);

export const isUnary = (operator: ExplorerCondition["operator"]) => UNARY.has(operator);
export const takesList = (operator: ExplorerCondition["operator"]) => LISTS.has(operator);

export const emptyGroup = (join: ExplorerGroup["join"] = "and"): ExplorerGroup => ({
  kind: "group",
  join,
  negate: false,
  children: [],
});

export function fieldMap(catalog: ExplorerCatalog | null): Map<ExplorerField, ExplorerFieldInfo> {
  return new Map((catalog?.fields ?? []).map((info) => [info.field, info]));
}

export function nodeAt(group: ExplorerGroup, path: NodePath): FilterNode | undefined {
  let node: FilterNode | undefined = group;
  for (const index of path) {
    node = node?.kind === "group" ? node.children[index] : undefined;
  }
  return node;
}

/** The tree with the node at `path` replaced by what `change` returns (removed when null). */
export function updateAt(
  group: ExplorerGroup,
  path: NodePath,
  change: (node: FilterNode) => FilterNode | null,
): ExplorerGroup {
  const [index, ...rest] = path;
  if (index === undefined) {
    const next = change(group);
    return next?.kind === "group" ? next : emptyGroup(group.join);
  }
  const children = group.children.flatMap((child, position): FilterNode[] => {
    if (position !== index) return [child];
    if (rest.length === 0) {
      const next = change(child);
      return next ? [next] : [];
    }
    return child.kind === "group" ? [updateAt(child, rest, change)] : [child];
  });
  return { ...group, children };
}

export const removeAt = (group: ExplorerGroup, path: NodePath) => updateAt(group, path, () => null);

export const appendAt = (group: ExplorerGroup, path: NodePath, node: FilterNode) =>
  updateAt(group, path, (target) =>
    target.kind === "group" ? { ...target, children: [...target.children, node] } : target,
  );

export function groupDepth(group: ExplorerGroup): number {
  return (
    1 +
    Math.max(0, ...group.children.map((child) => (child.kind === "group" ? groupDepth(child) : 0)))
  );
}

export function conditionCount(group: ExplorerGroup): number {
  return group.children.reduce(
    (sum, child) => sum + (child.kind === "group" ? conditionCount(child) : 1),
    0,
  );
}

export function defaultValue(
  info: ExplorerFieldInfo,
  operator: ExplorerCondition["operator"],
): ExplorerCondition["value"] {
  if (isUnary(operator)) return null;
  if (takesList(operator)) return info.values.slice(0, 1);
  if (info.type === "number") return 0;
  return info.values[0] ?? "";
}

export function defaultCondition(info: ExplorerFieldInfo): ExplorerCondition {
  const operator = info.operators[0] ?? "eq";
  return { kind: "condition", field: info.field, operator, value: defaultValue(info, operator) };
}

/** Whether a condition can be queried: the rules the desktop core and the worker enforce. */
export function conditionReady(
  condition: ExplorerCondition,
  info: ExplorerFieldInfo | undefined,
): boolean {
  if (!info?.operators.includes(condition.operator)) return false;
  const { value } = condition;
  const known = (text: string) =>
    text.trim() !== "" &&
    text.length <= MAX_TEXT &&
    (info.values.length === 0 || info.values.includes(text));
  if (isUnary(condition.operator)) return value === null;
  if (takesList(condition.operator)) {
    return (
      Array.isArray(value) && value.length > 0 && value.length <= MAX_VALUES && value.every(known)
    );
  }
  if (info.type === "number") return typeof value === "number" && Number.isFinite(value);
  return typeof value === "string" && known(value);
}

/** The filters a query carries: incomplete conditions and emptied groups are left out. */
export function readyFilters(group: ExplorerGroup, fields: FieldMap): ExplorerGroup {
  const children = group.children.flatMap((child): FilterNode[] => {
    if (child.kind === "condition") {
      return conditionReady(child, fields.get(child.field)) ? [child] : [];
    }
    const nested = readyFilters(child, fields);
    return nested.children.length > 0 ? [nested] : [];
  });
  return { ...group, children };
}

/**
 * A click on a column header: without Shift it sorts by that column alone (ascending, descending,
 * then back to the run's rank order); with Shift it adds or flips that key among the others, up to
 * `max` keys.
 */
export function toggleSort(
  sort: readonly ExplorerSort[],
  field: ExplorerField,
  additive: boolean,
  max: number,
): ExplorerSort[] {
  const current = sort.find((key) => key.field === field);
  if (!current) {
    if (!additive) return [{ field, direction: "asc" }];
    return sort.length >= max ? [...sort] : [...sort, { field, direction: "asc" }];
  }
  if (current.direction === "asc") {
    const flipped: ExplorerSort = { field, direction: "desc" };
    return additive ? sort.map((key) => (key.field === field ? flipped : key)) : [flipped];
  }
  return additive ? sort.filter((key) => key.field !== field) : [];
}

/** The visible columns, pinned ones first, each group in the view's order. */
export function orderedColumns(columns: readonly ViewColumn[]): ViewColumn[] {
  const visible = columns.filter((column) => column.visible);
  return [...visible.filter((column) => column.pinned), ...visible.filter((c) => !c.pinned)];
}

export function moveColumn(
  columns: readonly ViewColumn[],
  field: ExplorerField,
  delta: -1 | 1,
): ViewColumn[] {
  const index = columns.findIndex((column) => column.field === field);
  const target = index + delta;
  const next = [...columns];
  if (index < 0 || target < 0 || target >= columns.length) return next;
  const [moved] = next.splice(index, 1);
  if (moved) next.splice(target, 0, moved);
  return next;
}

export const toggleColumn = (columns: readonly ViewColumn[], field: ExplorerField) =>
  columns.map((column) =>
    column.field === field ? { ...column, visible: !column.visible } : column,
  );

export const togglePin = (columns: readonly ViewColumn[], field: ExplorerField) =>
  columns.map((column) =>
    column.field === field ? { ...column, pinned: !column.pinned } : column,
  );

/** What the explorer shows, as a saved view holds it. */
export interface ExplorerState {
  scoreRunId: string | null;
  filters: ExplorerGroup;
  search: string;
  sort: ExplorerSort[];
  columns: ViewColumn[];
}

export const stateOf = (definition: ViewDefinition): ExplorerState => ({
  scoreRunId: definition.score_run_id,
  filters: definition.filters,
  search: definition.search ?? "",
  sort: [...definition.sort],
  columns: [...definition.columns],
});

export const definitionOf = (state: ExplorerState): ViewDefinition => ({
  schema_version: 2,
  score_run_id: state.scoreRunId,
  filters: state.filters,
  search: state.search.trim() || null,
  sort: state.sort,
  columns: state.columns,
});

export const sameDefinition = (a: ViewDefinition | null, b: ViewDefinition | null) =>
  JSON.stringify(a) === JSON.stringify(b);

export function queryOf(
  state: ExplorerState,
  fields: FieldMap,
  offset: number,
  withTotal: boolean,
): ExplorerQuery {
  return {
    score_run_id: state.scoreRunId,
    filters: readyFilters(state.filters, fields),
    search: state.search.trim() || null,
    sort: state.sort,
    offset,
    limit: WINDOW_SIZE,
    with_total: withTotal,
  };
}

/** What a query asks, whatever its window: cached windows belong to one key. */
export const queryKey = (query: ExplorerQuery) =>
  JSON.stringify([query.score_run_id, query.filters, query.search, query.sort]);

export const windowOf = (index: number) =>
  Math.floor(Math.max(0, index) / WINDOW_SIZE) * WINDOW_SIZE;

/** The offsets of the windows that hold rows `first` to `last`. */
export function windowsBetween(first: number, last: number): number[] {
  const offsets: number[] = [];
  for (let offset = windowOf(first); offset <= last; offset += WINDOW_SIZE) offsets.push(offset);
  return offsets;
}

/** The leads a query matches, whatever their order or window: what "select all" selects. */
export const matchKey = (query: ExplorerQuery) =>
  JSON.stringify([query.score_run_id, query.filters, query.search]);

/**
 * A stored layout's columns against the catalog's: unknown or repeated fields are dropped and the
 * fields added since are appended as the default view has them, so an older layout names them all.
 */
export function reconcileColumns(
  columns: readonly ViewColumn[],
  defaults: readonly ViewColumn[],
): ViewColumn[] {
  const known = new Set(defaults.map((column) => column.field));
  const seen = new Set<ExplorerField>();
  const kept = columns.filter((column) => {
    if (!known.has(column.field) || seen.has(column.field)) return false;
    seen.add(column.field);
    return true;
  });
  return [...kept, ...defaults.filter((column) => !seen.has(column.field))];
}

/**
 * The tallest scroll area the grid asks for. Engines cap an element's height (Chromium near 33.5
 * million pixels, Firefox near 17.9 million); past this the scroll position maps onto the rows
 * proportionally, so the last of a million rows stays reachable.
 */
export const MAX_SCROLL_HEIGHT = 8_000_000;

export interface RowWindow {
  /** Height of the scroll area. */
  height: number;
  /** The scroll position the rows are placed from, clamped to the area. */
  offset: number;
  /** The row at the top of the viewport, fractional. */
  start: number;
  first: number;
  last: number;
}

/** The rows a grid renders at a scroll position: those in the viewport plus `overscan` each side. */
export function rowWindow(
  total: number,
  rowHeight: number,
  scrollTop: number,
  viewport: number,
  overscan = 0,
): RowWindow {
  const height = Math.min(total * rowHeight, MAX_SCROLL_HEIGHT);
  const room = Math.max(0, height - viewport);
  const offset = Math.min(Math.max(scrollTop, 0), room);
  const rows = Math.max(0, total - viewport / rowHeight);
  const start = room === 0 ? 0 : (offset / room) * rows;
  const top = Math.floor(start);
  return {
    height,
    offset,
    start,
    first: Math.max(0, top - overscan),
    last: Math.min(total - 1, top + Math.ceil(viewport / rowHeight) + overscan),
  };
}

/** The scroll position that brings row `index` into view, moving as little as possible. */
export function scrollTopFor(
  index: number,
  total: number,
  rowHeight: number,
  viewport: number,
  scrollTop: number,
): number {
  const { height, offset, start } = rowWindow(total, rowHeight, scrollTop, viewport);
  const room = Math.max(0, height - viewport);
  const rows = Math.max(0, total - viewport / rowHeight);
  if (room === 0 || rows === 0) return 0;
  const shown = viewport / rowHeight;
  if (index < start) return (Math.max(index, 0) / rows) * room;
  if (index + 1 > start + shown) return (Math.min(index + 1 - shown, rows) / rows) * room;
  return offset;
}

/** Comma-separated text as a list of values, blanks left out. */
export const listValues = (text: string) =>
  text
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
