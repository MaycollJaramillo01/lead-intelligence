import { useMemo, useState } from "react";
import type {
  ExplorerCatalog,
  ExplorerCondition,
  ExplorerField,
  ExplorerFieldInfo,
  ExplorerGroup,
  ExplorerOperator,
} from "@lead/shared-types";
import { X } from "lucide-react";
import { InlineNotice } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { FIELD_KEYS, OPERATOR_KEYS, valueText } from "@/features/leads/labels";
import {
  appendAt,
  conditionCount,
  conditionReady,
  defaultCondition,
  defaultValue,
  emptyGroup,
  fieldMap,
  isUnary,
  listValues,
  readyFilters,
  removeAt,
  takesList,
  updateAt,
  type FieldMap,
  type NodePath,
} from "@/features/leads/query";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const SELECT = "h-7 rounded-md border bg-transparent px-2 text-xs";

type Edit = (apply: (root: ExplorerGroup) => ExplorerGroup) => void;

const arity = (operator: ExplorerOperator) =>
  isUnary(operator) ? "none" : takesList(operator) ? "list" : "one";

function ListInput({
  values,
  onValues,
  label,
  hint,
}: {
  values: readonly string[];
  onValues: (values: string[]) => void;
  label: string;
  hint: string;
}) {
  // the text keeps what is being typed (a trailing comma too) while it still says the same values
  const [text, setText] = useState(values.join(", "));
  const shown =
    JSON.stringify(listValues(text)) === JSON.stringify(values) ? text : values.join(", ");
  return (
    <Input
      aria-label={label}
      title={hint}
      placeholder={hint}
      value={shown}
      maxLength={2000}
      onChange={(event) => {
        setText(event.target.value);
        onValues(listValues(event.target.value));
      }}
      className="h-7 w-52 text-xs"
    />
  );
}

function ValueEditor({
  condition,
  info,
  onValue,
}: {
  condition: ExplorerCondition;
  info: ExplorerFieldInfo;
  onValue: (value: ExplorerCondition["value"]) => void;
}) {
  const t = useT();
  const label = t("filter.value");
  const { operator, value } = condition;
  if (isUnary(operator)) return null;
  if (takesList(operator)) {
    const values = Array.isArray(value) ? value : [];
    if (info.values.length === 0) {
      return (
        <ListInput values={values} onValues={onValue} label={label} hint={t("filter.valuesHelp")} />
      );
    }
    return (
      <select
        multiple
        aria-label={label}
        value={values}
        onChange={(event) =>
          onValue(Array.from(event.target.selectedOptions, (option) => option.value))
        }
        className="min-h-16 rounded-md border bg-transparent px-1 text-xs"
      >
        {info.values.map((code) => (
          <option key={code} value={code}>
            {valueText(t, info.field, code)}
          </option>
        ))}
      </select>
    );
  }
  if (info.type === "number") {
    return (
      <Input
        type="number"
        aria-label={label}
        value={typeof value === "number" ? value : ""}
        onChange={(event) => onValue(event.target.value === "" ? null : Number(event.target.value))}
        className="h-7 w-28 text-xs"
      />
    );
  }
  if (info.values.length > 0) {
    return (
      <select
        aria-label={label}
        value={typeof value === "string" ? value : ""}
        onChange={(event) => onValue(event.target.value)}
        className={SELECT}
      >
        {info.values.map((code) => (
          <option key={code} value={code}>
            {valueText(t, info.field, code)}
          </option>
        ))}
      </select>
    );
  }
  return (
    <Input
      aria-label={label}
      value={typeof value === "string" ? value : ""}
      maxLength={200}
      onChange={(event) => onValue(event.target.value)}
      className="h-7 w-44 text-xs"
    />
  );
}

function ConditionEditor({
  condition,
  path,
  catalog,
  fields,
  edit,
}: {
  condition: ExplorerCondition;
  path: NodePath;
  catalog: ExplorerCatalog;
  fields: FieldMap;
  edit: Edit;
}) {
  const t = useT();
  const info = fields.get(condition.field);
  const replace = (next: ExplorerCondition) => edit((root) => updateAt(root, path, () => next));
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-1 rounded-md border p-1",
        !conditionReady(condition, info) && "border-dashed border-warn/60",
      )}
    >
      <select
        aria-label={t("filter.field")}
        value={condition.field}
        onChange={(event) => {
          const next = fields.get(event.target.value as ExplorerField);
          if (next) replace(defaultCondition(next));
        }}
        className={SELECT}
      >
        {catalog.fields.map((field) => (
          <option key={field.field} value={field.field}>
            {t(FIELD_KEYS[field.field])}
          </option>
        ))}
      </select>
      <select
        aria-label={t("filter.operator")}
        value={condition.operator}
        onChange={(event) => {
          const operator = event.target.value as ExplorerOperator;
          const same = arity(operator) === arity(condition.operator);
          replace({
            ...condition,
            operator,
            value: same || !info ? condition.value : defaultValue(info, operator),
          });
        }}
        className={SELECT}
      >
        {(info?.operators ?? [condition.operator]).map((operator) => (
          <option key={operator} value={operator}>
            {t(OPERATOR_KEYS[operator])}
          </option>
        ))}
      </select>
      {info && (
        <ValueEditor
          key={`${condition.field}:${arity(condition.operator)}`}
          condition={condition}
          info={info}
          onValue={(value) => replace({ ...condition, value })}
        />
      )}
      <Button
        variant="ghost"
        size="icon"
        className="ml-auto size-6"
        aria-label={t("filter.remove")}
        onClick={() => edit((root) => removeAt(root, path))}
      >
        <X aria-hidden="true" className="size-3.5" />
      </Button>
    </div>
  );
}

function GroupEditor({
  group,
  path,
  depth,
  catalog,
  fields,
  total,
  edit,
}: {
  group: ExplorerGroup;
  path: NodePath;
  depth: number;
  catalog: ExplorerCatalog;
  fields: FieldMap;
  total: number;
  edit: Edit;
}) {
  const t = useT();
  const [first] = catalog.fields;
  const full = group.children.length >= catalog.max_children;
  const change = (patch: Partial<Pick<ExplorerGroup, "join" | "negate">>) =>
    edit((root) =>
      updateAt(root, path, (node) => (node.kind === "group" ? { ...node, ...patch } : node)),
    );
  return (
    <div
      role="group"
      aria-label={t(group.join === "and" ? "filter.all" : "filter.any")}
      className={cn("space-y-1.5 rounded-md border p-2", depth > 1 && "bg-muted/30")}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <select
          aria-label={t("filter.join")}
          value={group.join}
          onChange={(event) => change({ join: event.target.value === "or" ? "or" : "and" })}
          className={SELECT}
        >
          <option value="and">{t("filter.all")}</option>
          <option value="or">{t("filter.any")}</option>
        </select>
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            checked={group.negate}
            onChange={(event) => change({ negate: event.target.checked })}
          />
          {t("filter.negate")}
        </label>
        <Button
          variant="outline"
          size="sm"
          className="h-6"
          disabled={!first || full || total >= catalog.max_conditions}
          onClick={() => {
            if (first) edit((root) => appendAt(root, path, defaultCondition(first)));
          }}
        >
          {t("filter.addCondition")}
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-6"
          disabled={full || depth >= catalog.max_depth}
          onClick={() =>
            edit((root) => appendAt(root, path, emptyGroup(group.join === "and" ? "or" : "and")))
          }
        >
          {t("filter.addGroup")}
        </Button>
        {path.length > 0 && (
          <Button
            variant="ghost"
            size="sm"
            className="h-6"
            onClick={() => edit((root) => removeAt(root, path))}
          >
            {t("filter.remove")}
          </Button>
        )}
      </div>
      {group.children.length > 0 && (
        <ul className="space-y-1">
          {group.children.map((child, index) => (
            <li key={index}>
              {child.kind === "group" ? (
                <GroupEditor
                  group={child}
                  path={[...path, index]}
                  depth={depth + 1}
                  catalog={catalog}
                  fields={fields}
                  total={total}
                  edit={edit}
                />
              ) : (
                <ConditionEditor
                  condition={child}
                  path={[...path, index]}
                  catalog={catalog}
                  fields={fields}
                  edit={edit}
                />
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface FilterBuilderProps {
  onOpenChange: (open: boolean) => void;
  catalog: ExplorerCatalog;
  filters: ExplorerGroup;
  onApply: (filters: ExplorerGroup) => void;
}

/**
 * The explorer's filters (phase 12): conditions in groups joined by all or any, a group optionally
 * negated, within the catalog's depth and size limits. Edits stay here until applied; a condition
 * still incomplete is kept but left out of the query, and says so.
 */
export function FilterBuilder({ onOpenChange, catalog, filters, onApply }: FilterBuilderProps) {
  const t = useT();
  const [draft, setDraft] = useState(filters);
  const fields = useMemo(() => fieldMap(catalog), [catalog]);
  const total = conditionCount(draft);
  const incomplete = conditionCount(readyFilters(draft, fields)) < total;
  const edit: Edit = (apply) => setDraft((current) => apply(current));

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{t("filter.title")}</DialogTitle>
          <DialogDescription>{t("filter.help")}</DialogDescription>
        </DialogHeader>
        <div className="space-y-2 text-xs">
          <GroupEditor
            group={draft}
            path={[]}
            depth={1}
            catalog={catalog}
            fields={fields}
            total={total}
            edit={edit}
          />
          {total === 0 && <p className="text-muted-foreground">{t("filter.empty")}</p>}
          <p className="text-[11px] text-muted-foreground">
            {t("filter.limits", { depth: catalog.max_depth, conditions: catalog.max_conditions })}
          </p>
          {incomplete && <InlineNotice variant="partial">{t("filter.incomplete")}</InlineNotice>}
          <div className="flex justify-between gap-1">
            <Button
              variant="ghost"
              size="sm"
              className="h-7"
              onClick={() => setDraft(emptyGroup())}
            >
              {t("filter.clear")}
            </Button>
            <Button
              size="sm"
              className="h-7"
              onClick={() => {
                onApply(draft);
                onOpenChange(false);
              }}
            >
              {t("common.apply")}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
