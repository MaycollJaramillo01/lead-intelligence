import type { ColumnDef, FilterFn } from "@tanstack/react-table";

export type FilterOperator = "contains" | "equals" | "not_equals" | "is_empty" | "is_not_empty";

export const FILTER_OPERATORS: readonly FilterOperator[] = [
  "contains",
  "equals",
  "not_equals",
  "is_empty",
  "is_not_empty",
];

export const OPERATOR_LABELS: Record<FilterOperator, string> = {
  contains: "contains",
  equals: "is",
  not_equals: "is not",
  is_empty: "is empty",
  is_not_empty: "is not empty",
};

export function operatorNeedsValue(operator: FilterOperator): boolean {
  return operator !== "is_empty" && operator !== "is_not_empty";
}

/** A field the user can filter on, taken from the table columns. */
export interface FilterField {
  id: string;
  label: string;
}

export interface FilterCondition {
  id: string;
  field: string;
  operator: FilterOperator;
  value: string;
}

/** Filter fields use the column id, which is what the table filters on. */
export function filterFieldsFrom<TData>(columns: ColumnDef<TData>[]): FilterField[] {
  return columns.flatMap((column) => {
    const id = column.id ?? ("accessorKey" in column ? String(column.accessorKey) : undefined);
    const label = typeof column.header === "string" ? column.header : (id ?? "");
    return id ? [{ id, label }] : [];
  });
}

export function createCondition(field: FilterField): FilterCondition {
  return {
    id: `${field.id}-${Math.random().toString(36).slice(2, 8)}`,
    field: field.id,
    operator: "contains",
    value: "",
  };
}

/** Pure matcher, shared by the table filter and the tests. */
export function matchesCondition(cellValue: unknown, condition: FilterCondition): boolean {
  const text = cellValue === null || cellValue === undefined ? "" : String(cellValue).trim();
  const needle = condition.value.trim();
  switch (condition.operator) {
    case "is_empty":
      return text === "";
    case "is_not_empty":
      return text !== "";
    case "equals":
      return text.toLowerCase() === needle.toLowerCase();
    case "not_equals":
      return text.toLowerCase() !== needle.toLowerCase();
    case "contains":
      return needle === "" || text.toLowerCase().includes(needle.toLowerCase());
  }
}

/** Every condition on one column must hold. */
export function matchesConditions(cellValue: unknown, conditions: FilterCondition[]): boolean {
  return conditions.every((condition) => matchesCondition(cellValue, condition));
}

/** TanStack filter function: the filter value is the list of conditions for that column. */
export function conditionsFilterFn<TData>(): FilterFn<TData> {
  return (row, columnId, filterValue) => {
    const conditions = filterValue as FilterCondition[];
    if (!Array.isArray(conditions) || conditions.length === 0) return true;
    return matchesConditions(row.getValue(columnId), conditions);
  };
}

/** Groups conditions the way TanStack expects them: one entry per column. */
export function toColumnFilters(conditions: FilterCondition[]) {
  const byField = new Map<string, FilterCondition[]>();
  for (const condition of conditions) {
    if (operatorNeedsValue(condition.operator) && condition.value.trim() === "") continue;
    byField.set(condition.field, [...(byField.get(condition.field) ?? []), condition]);
  }
  return [...byField.entries()].map(([id, value]) => ({ id, value }));
}

/** Readable one-line summary of the active query, e.g. "Vertical contains roofing AND State is TX". */
export function describeFilters(conditions: FilterCondition[], fields: FilterField[]): string {
  const parts = conditions.map((condition) => {
    const label = fields.find((field) => field.id === condition.field)?.label ?? condition.field;
    const operator = OPERATOR_LABELS[condition.operator];
    return operatorNeedsValue(condition.operator)
      ? `${label} ${operator} "${condition.value.trim()}"`
      : `${label} ${operator}`;
  });
  return parts.join(" AND ");
}
