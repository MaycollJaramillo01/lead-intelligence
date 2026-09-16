import { useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DataTable } from "@/components/data/DataTable";
import type { FilterCondition } from "@/components/data/filters";
import { preferenceDefaults, usePreferences } from "@/stores/preferences";

interface Row {
  id: string;
  name: string;
  city: string;
}

/** Synthetic rows: test data only, never shown by the product. */
const ROWS: Row[] = [
  { id: "1", name: "Zeta Test Row", city: "Austin" },
  { id: "2", name: "Alpha Test Row", city: "Dallas" },
  { id: "3", name: "Mid Test Row", city: "Austin" },
];

const COLUMNS: ColumnDef<Row>[] = [
  { accessorKey: "name", header: "Business", cell: ({ getValue }) => String(getValue()) },
  { accessorKey: "city", header: "City", cell: ({ getValue }) => String(getValue()) },
];

const FIELDS = [
  { id: "name", label: "Business" },
  { id: "city", label: "City" },
];

function Harness({ data = ROWS }: { data?: Row[] }) {
  const [conditions, setConditions] = useState<FilterCondition[]>([]);
  return (
    <DataTable
      tableId="test-table"
      columns={COLUMNS}
      data={data}
      getRowId={(row) => row.id}
      filterFields={FIELDS}
      conditions={conditions}
      onConditionsChange={setConditions}
      emptyState={<p>Nothing to show</p>}
    />
  );
}

function bodyRowNames(): string[] {
  const rows = screen.getAllByRole("row").slice(1); // skip the header row
  return rows.flatMap((row) => {
    const cells = within(row).queryAllByRole("cell");
    const name = cells[1]?.textContent ?? "";
    return name ? [name] : [];
  });
}

describe("DataTable", () => {
  beforeEach(() => {
    usePreferences.setState({ ...preferenceDefaults, language: "en" });
  });

  it("renders rows and sorts by a column header", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    expect(bodyRowNames()).toEqual(["Zeta Test Row", "Alpha Test Row", "Mid Test Row"]);

    await user.click(screen.getByRole("button", { name: /business/i }));
    expect(bodyRowNames()).toEqual(["Alpha Test Row", "Mid Test Row", "Zeta Test Row"]);
    expect(screen.getAllByRole("columnheader")[1]).toHaveProperty("ariaSort", "ascending");
  });

  it("counts selected rows", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    expect(screen.getByText("3 rows")).toBeTruthy();
    await user.click(screen.getAllByRole("checkbox", { name: /select row/i })[0] as HTMLElement);
    expect(screen.getByText("1 selected")).toBeTruthy();
  });

  it("filters rows through an editable chip and shows the query summary", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: /add filter/i }));
    await user.click(screen.getByRole("menuitem", { name: "City" }));
    await user.type(screen.getByRole("textbox", { name: /city contains/i }), "dallas");

    expect(bodyRowNames()).toEqual(["Alpha Test Row"]);
    expect(screen.getByText('City contains "dallas"')).toBeTruthy();
  });

  it("hides a column and remembers it for this table", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: /columns/i }));
    await user.click(screen.getByRole("menuitemcheckbox", { name: "City" }));

    expect(screen.queryAllByRole("columnheader", { name: /city/i })).toHaveLength(0);
    expect(usePreferences.getState().columnVisibility["test-table"]).toEqual({ city: false });
  });

  it("shows the empty state instead of inventing rows", () => {
    render(<Harness data={[]} />);
    expect(screen.getByText("Nothing to show")).toBeTruthy();
    expect(screen.getByText("0 rows")).toBeTruthy();
  });
});

describe("DataTable virtualization", () => {
  const many: Row[] = Array.from({ length: 5000 }, (_row, index) => ({
    id: String(index),
    name: `Row ${index}`,
    city: index % 2 === 0 ? "Austin" : "Dallas",
  }));

  const originalOffsetHeight = Object.getOwnPropertyDescriptor(
    HTMLElement.prototype,
    "offsetHeight",
  );

  beforeEach(() => {
    usePreferences.setState({ ...preferenceDefaults, language: "en" });
    Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
      configurable: true,
      get: () => 600,
    });
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      width: 900,
      height: 600,
      top: 0,
      left: 0,
      bottom: 600,
      right: 900,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect);
  });

  afterEach(() => {
    if (originalOffsetHeight) {
      Object.defineProperty(HTMLElement.prototype, "offsetHeight", originalOffsetHeight);
    }
    vi.restoreAllMocks();
  });

  it("keeps the DOM small and the scroll height honest for 5000 rows", () => {
    render(<Harness data={many} />);
    const renderedRows = screen.getAllByRole("row").length;
    expect(renderedRows).toBeGreaterThan(1);
    expect(renderedRows).toBeLessThan(80);

    const spacer = document.querySelector("tr[aria-hidden] > td");
    expect(spacer).toBeTruthy();
    const height = Number.parseFloat((spacer as HTMLElement).style.height);
    expect(height).toBeGreaterThan(10_000);
  });
});
