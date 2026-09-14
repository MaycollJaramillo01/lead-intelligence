import { describe, expect, it } from "vitest";
import type { ExplorerQuery, ViewColumn } from "@lead/shared-types";
import {
  MAX_SCROLL_HEIGHT,
  emptyGroup,
  matchKey,
  reconcileColumns,
  rowWindow,
  scrollTopFor,
} from "@/features/leads/query";

describe("grid rows", () => {
  it("maps pixels to rows one to one while the rows fit in the scroll area", () => {
    const view = rowWindow(1000, 34, 3400, 680);
    expect(view.height).toBe(34_000);
    expect(view.start).toBeCloseTo(100);
    expect([view.first, view.last]).toEqual([100, 120]);
    // a row below the viewport scrolls to its bottom edge; a row in view does not scroll
    expect(scrollTopFor(500, 1000, 34, 680, 3400)).toBeCloseTo(500 * 34 + 34 - 680);
    expect(scrollTopFor(110, 1000, 34, 680, 3400)).toBe(3400);
  });

  it("keeps the last of a million rows reachable within the engine's height limit", () => {
    const total = 1_000_000;
    const bottom = scrollTopFor(total - 1, total, 34, 680, 0);
    const view = rowWindow(total, 34, bottom, 680, 8);
    expect(view.height).toBe(MAX_SCROLL_HEIGHT);
    expect(view.last).toBe(total - 1);
    expect(view.last - view.first).toBeLessThan(40);
    // the last row sits on the viewport's bottom edge
    expect((total - 1 - view.start) * 34).toBeCloseTo(680 - 34);
    expect(rowWindow(total, 34, scrollTopFor(0, total, 34, 680, bottom), 680).first).toBe(0);
  });

  it("clamps a scroll position past the rows", () => {
    const view = rowWindow(10, 34, 99_999, 680);
    expect(view.offset).toBe(0);
    expect([view.first, view.last]).toEqual([0, 9]);
    expect(rowWindow(0, 34, 0, 680).last).toBe(-1);
  });
});

describe("explorer layouts", () => {
  it("drops unknown and repeated columns of a stored layout and appends the new ones", () => {
    const defaults: ViewColumn[] = [
      { field: "rank", visible: true, pinned: true, width: null },
      { field: "name", visible: true, pinned: true, width: null },
      { field: "tier", visible: true, pinned: false, width: null },
    ];
    const stored = [
      { field: "tier", visible: false, pinned: false, width: 90 },
      { field: "tier", visible: true, pinned: false, width: null },
      { field: "retired_field", visible: true, pinned: false, width: null },
    ] as unknown as ViewColumn[];
    expect(reconcileColumns(stored, defaults)).toEqual([
      { field: "tier", visible: false, pinned: false, width: 90 },
      { field: "rank", visible: true, pinned: true, width: null },
      { field: "name", visible: true, pinned: true, width: null },
    ]);
  });

  it("selects every matching lead by what matches, whatever the order or window", () => {
    const query: ExplorerQuery = {
      score_run_id: null,
      filters: emptyGroup(),
      search: "bakery",
      sort: [],
      offset: 0,
      limit: 100,
      with_total: true,
    };
    const sorted = { ...query, sort: [{ field: "rank" as const, direction: "desc" as const }] };
    expect(matchKey({ ...sorted, offset: 500, with_total: false })).toBe(matchKey(query));
    expect(matchKey({ ...query, search: "plumbing" })).not.toBe(matchKey(query));
  });
});
