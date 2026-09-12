import { Columns3, Plus, X } from "lucide-react";
import type { Table } from "@tanstack/react-table";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { useT } from "@/lib/i18n";
import {
  createCondition,
  describeFilters,
  FILTER_OPERATORS,
  OPERATOR_LABELS,
  operatorNeedsValue,
  type FilterCondition,
  type FilterField,
  type FilterOperator,
} from "@/components/data/filters";

interface FilterBarProps<TData> {
  table: Table<TData>;
  fields: FilterField[];
  conditions: FilterCondition[];
  onConditionsChange: (conditions: FilterCondition[]) => void;
  /** Rendered on the right of the bar (row counts, screen-specific controls). */
  trailing?: React.ReactNode;
}

/** Editable filter chips plus the readable query summary and the column menu. */
export function FilterBar<TData>({
  table,
  fields,
  conditions,
  onConditionsChange,
  trailing,
}: FilterBarProps<TData>) {
  const t = useT();

  const update = (id: string, patch: Partial<FilterCondition>) =>
    onConditionsChange(
      conditions.map((condition) => (condition.id === id ? { ...condition, ...patch } : condition)),
    );
  const remove = (id: string) =>
    onConditionsChange(conditions.filter((condition) => condition.id !== id));

  const hideableColumns = table.getAllLeafColumns().filter((column) => column.getCanHide());

  return (
    <div className="flex shrink-0 flex-col gap-1.5 border-b px-3 py-2">
      <div className="flex flex-wrap items-center gap-1.5">
        {conditions.map((condition) => (
          <div
            key={condition.id}
            className="flex items-center gap-1 rounded-md border bg-muted/40 py-0.5 pr-0.5 pl-1.5 text-xs"
          >
            <select
              aria-label={t("common.filters")}
              value={condition.field}
              onChange={(event) => update(condition.id, { field: event.target.value })}
              className="bg-transparent font-medium outline-none"
            >
              {fields.map((field) => (
                <option key={field.id} value={field.id}>
                  {field.label}
                </option>
              ))}
            </select>
            <select
              aria-label={OPERATOR_LABELS[condition.operator]}
              value={condition.operator}
              onChange={(event) =>
                update(condition.id, { operator: event.target.value as FilterOperator })
              }
              className="bg-transparent text-muted-foreground outline-none"
            >
              {FILTER_OPERATORS.map((operator) => (
                <option key={operator} value={operator}>
                  {OPERATOR_LABELS[operator]}
                </option>
              ))}
            </select>
            {operatorNeedsValue(condition.operator) && (
              <Input
                aria-label={`${fields.find((field) => field.id === condition.field)?.label ?? condition.field} ${OPERATOR_LABELS[condition.operator]}`}
                value={condition.value}
                onChange={(event) => update(condition.id, { value: event.target.value })}
                className="h-5 w-28 border-0 bg-transparent px-1 py-0 text-xs shadow-none focus-visible:ring-0"
              />
            )}
            <button
              type="button"
              onClick={() => remove(condition.id)}
              aria-label={`${t("common.clear")}: ${fields.find((field) => field.id === condition.field)?.label ?? condition.field}`}
              className="flex size-4 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-accent-foreground"
            >
              <X aria-hidden="true" className="size-3" />
            </button>
          </div>
        ))}

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="h-6 gap-1 px-1.5 text-xs">
              <Plus aria-hidden="true" className="size-3.5" />
              {t("common.addFilter")}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="max-h-72 overflow-auto">
            <DropdownMenuLabel>{t("common.filters")}</DropdownMenuLabel>
            <DropdownMenuSeparator />
            {fields.map((field) => (
              <DropdownMenuItem
                key={field.id}
                onSelect={() => onConditionsChange([...conditions, createCondition(field)])}
              >
                {field.label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        {conditions.length > 0 && (
          <Button
            variant="ghost"
            size="sm"
            className="h-6 px-1.5 text-xs"
            onClick={() => onConditionsChange([])}
          >
            {t("common.clearFilters")}
          </Button>
        )}

        <div className="ml-auto flex items-center gap-2">
          {trailing}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm" className="h-6 gap-1 px-1.5 text-xs">
                <Columns3 aria-hidden="true" className="size-3.5" />
                {t("common.columns")}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="max-h-72 overflow-auto">
              <DropdownMenuLabel>{t("common.columns")}</DropdownMenuLabel>
              <DropdownMenuSeparator />
              {hideableColumns.map((column) => (
                <DropdownMenuCheckboxItem
                  key={column.id}
                  checked={column.getIsVisible()}
                  onCheckedChange={(checked) => column.toggleVisibility(checked === true)}
                  onSelect={(event) => event.preventDefault()}
                >
                  {String(column.columnDef.header ?? column.id)}
                </DropdownMenuCheckboxItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {conditions.length > 0 && (
        <p className="font-mono text-[11px] text-muted-foreground">
          {describeFilters(conditions, fields)}
        </p>
      )}
    </div>
  );
}
