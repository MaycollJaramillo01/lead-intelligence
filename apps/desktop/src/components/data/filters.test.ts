import { describe, expect, it } from "vitest";
import {
  createCondition,
  describeFilters,
  matchesCondition,
  operatorNeedsValue,
  toColumnFilters,
  type FilterCondition,
} from "@/components/data/filters";

const condition = (patch: Partial<FilterCondition>): FilterCondition => ({
  id: "c1",
  field: "vertical",
  operator: "contains",
  value: "",
  ...patch,
});

describe("filter conditions", () => {
  it("matches text case-insensitively", () => {
    expect(matchesCondition("Roofing", condition({ value: "roof" }))).toBe(true);
    expect(matchesCondition("Plumbing", condition({ value: "roof" }))).toBe(false);
    expect(matchesCondition("Roofing", condition({ operator: "equals", value: "roofing" }))).toBe(
      true,
    );
    expect(
      matchesCondition("Roofing", condition({ operator: "not_equals", value: "roofing" })),
    ).toBe(false);
  });

  it("treats null, undefined and blanks as empty", () => {
    const empty = condition({ operator: "is_empty" });
    expect(matchesCondition(null, empty)).toBe(true);
    expect(matchesCondition(undefined, empty)).toBe(true);
    expect(matchesCondition("   ", empty)).toBe(true);
    expect(matchesCondition("value", empty)).toBe(false);
    expect(matchesCondition("value", condition({ operator: "is_not_empty" }))).toBe(true);
  });

  it("accepts everything while the value is still blank", () => {
    expect(matchesCondition("anything", condition({ value: "" }))).toBe(true);
  });

  it("knows which operators need a value", () => {
    expect(operatorNeedsValue("contains")).toBe(true);
    expect(operatorNeedsValue("is_empty")).toBe(false);
  });

  it("groups conditions per column and drops unfinished ones", () => {
    const filters = toColumnFilters([
      condition({ id: "a", value: "roof" }),
      condition({ id: "b", field: "location", value: "" }),
      condition({ id: "c", field: "phone", operator: "is_empty", value: "" }),
    ]);
    expect(filters).toEqual([
      { id: "vertical", value: [expect.objectContaining({ id: "a" })] },
      { id: "phone", value: [expect.objectContaining({ id: "c" })] },
    ]);
  });

  it("describes the query in words", () => {
    const summary = describeFilters(
      [
        condition({ id: "a", value: "roof" }),
        condition({ id: "b", field: "location", operator: "equals", value: "TX" }),
        condition({ id: "c", field: "phone", operator: "is_empty", value: "" }),
      ],
      [
        { id: "vertical", label: "Vertical" },
        { id: "location", label: "Location" },
        { id: "phone", label: "Phone" },
      ],
    );
    expect(summary).toBe('Vertical contains "roof" AND Location is "TX" AND Phone is empty');
  });

  it("creates conditions with a unique id for the clicked field", () => {
    const first = createCondition({ id: "vertical", label: "Vertical" });
    const second = createCondition({ id: "vertical", label: "Vertical" });
    expect(first.field).toBe("vertical");
    expect(first.id).not.toBe(second.id);
  });
});
