import { describe, expect, it } from "vitest";
import type {
  ExplorerCatalog,
  ExplorerField,
  ExplorerGroup,
  ExplorerSort,
} from "@lead/shared-types";
import catalogFixture from "@lead/shared-types/fixtures/explorer_catalog.json";
import {
  WINDOW_SIZE,
  appendAt,
  conditionCount,
  defaultCondition,
  emptyGroup,
  fieldMap,
  groupDepth,
  listValues,
  moveColumn,
  nodeAt,
  orderedColumns,
  queryOf,
  readyFilters,
  removeAt,
  stateOf,
  toggleColumn,
  togglePin,
  toggleSort,
  updateAt,
  windowsBetween,
} from "@/features/leads/query";

/** The catalogue the real worker describes. */
const catalog = catalogFixture as ExplorerCatalog;
const fields = fieldMap(catalog);

function info(field: ExplorerField) {
  const found = fields.get(field);
  if (!found) throw new Error(`the catalogue describes ${field}`);
  return found;
}

describe("filter trees", () => {
  it("adds, edits and removes conditions and nested groups by path", () => {
    let tree = appendAt(emptyGroup(), [], defaultCondition(info("tier")));
    tree = appendAt(tree, [], emptyGroup("or"));
    tree = appendAt(tree, [1], defaultCondition(info("total_score")));
    expect(groupDepth(tree)).toBe(2);
    expect(conditionCount(tree)).toBe(2);

    tree = updateAt(tree, [1, 0], (node) =>
      node.kind === "condition" ? { ...node, operator: "gt", value: 60 } : node,
    );
    expect(nodeAt(tree, [1, 0])).toEqual({
      kind: "condition",
      field: "total_score",
      operator: "gt",
      value: 60,
    });
    tree = removeAt(tree, [0]);
    expect(conditionCount(tree)).toBe(1);
    expect(nodeAt(tree, [0, 0])?.kind).toBe("condition");
  });

  it("queries only complete conditions and leaves emptied groups out", () => {
    const tree: ExplorerGroup = {
      kind: "group",
      join: "and",
      negate: false,
      children: [
        { kind: "condition", field: "name", operator: "contains", value: "  " },
        {
          kind: "group",
          join: "or",
          negate: true,
          children: [{ kind: "condition", field: "tier", operator: "in", value: [] }],
        },
        { kind: "condition", field: "tier", operator: "eq", value: "priority" },
        { kind: "condition", field: "suppressed", operator: "is_true", value: null },
        { kind: "condition", field: "tier", operator: "eq", value: "gold" },
        { kind: "condition", field: "total_score", operator: "contains", value: 3 },
      ],
    };
    expect(readyFilters(tree, fields).children).toEqual([tree.children[2], tree.children[3]]);
    expect(listValues(" priority, ,review ")).toEqual(["priority", "review"]);
  });
});

describe("sort keys", () => {
  it("cycles one key, or adds keys with Shift up to the limit", () => {
    let sort = toggleSort([], "total_score", false, 4);
    expect(sort).toEqual([{ field: "total_score", direction: "asc" }]);
    sort = toggleSort(sort, "total_score", false, 4);
    expect(sort).toEqual([{ field: "total_score", direction: "desc" }]);
    sort = toggleSort(sort, "name", true, 4);
    expect(sort.map((key) => key.field)).toEqual(["total_score", "name"]);
    expect(toggleSort(sort, "total_score", false, 4)).toEqual([]);

    const fieldsToAdd: ExplorerField[] = ["rank", "tier", "name", "region"];
    const full = fieldsToAdd.reduce<ExplorerSort[]>(
      (keys, field) => toggleSort(keys, field, true, 4),
      [],
    );
    expect(toggleSort(full, "locality", true, 4)).toEqual(full);
  });
});

describe("columns and windows", () => {
  it("shows pinned columns first and keeps order, visibility and pins", () => {
    const columns = catalog.default_view.columns;
    const shown = orderedColumns(columns);
    expect(shown.every((column) => column.visible)).toBe(true);
    expect(shown.slice(0, 2).map((column) => column.field)).toEqual(["rank", "name"]);
    const tier = columns.findIndex((column) => column.field === "tier");
    expect(moveColumn(columns, "tier", -1).findIndex((column) => column.field === "tier")).toBe(
      tier - 1,
    );
    expect(toggleColumn(columns, "tier").find((c) => c.field === "tier")?.visible).toBe(false);
    expect(togglePin(columns, "tier").find((c) => c.field === "tier")?.pinned).toBe(true);
  });

  it("asks for the windows a viewport shows, never for every lead", () => {
    expect(windowsBetween(0, 20)).toEqual([0]);
    expect(windowsBetween(95, 230)).toEqual([0, 100, 200]);
    const query = queryOf(stateOf(catalog.default_view), fields, 999_900, false);
    expect(query).toMatchObject({ offset: 999_900, limit: WINDOW_SIZE, with_total: false });
  });
});
