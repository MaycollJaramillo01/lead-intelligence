import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  BulkRequest,
  BulkResult,
  ExplorerCatalog,
  ExplorerGroup,
  ExplorerPage,
  ExplorerQuery,
  ExplorerRow,
  SavedViewList,
  ViewDefinition,
} from "@lead/shared-types";
import catalogFixture from "@lead/shared-types/fixtures/explorer_catalog.json";
import pageFixture from "@lead/shared-types/fixtures/explorer_page.json";
import viewsFixture from "@lead/shared-types/fixtures/saved_view_list.json";
import { PageActionsProvider } from "@/components/layout/page-chrome";
import { LeadExplorerScreen } from "@/features/leads/LeadExplorerScreen";
import { explorerSessionDefaults, useExplorerSession } from "@/features/leads/store";
import type { desktop } from "@/lib/ipc";
import { PREFERENCES_STORAGE_KEY, preferenceDefaults, usePreferences } from "@/stores/preferences";
import { useUiStore } from "@/stores/ui";

const mocks = vi.hoisted(() => ({
  getExplorerCatalog: vi.fn<typeof desktop.getExplorerCatalog>(),
  exploreLeads: vi.fn<typeof desktop.exploreLeads>(),
  listSavedViews: vi.fn<typeof desktop.listSavedViews>(),
  bulkLeads: vi.fn<typeof desktop.bulkLeads>(),
  getBusiness: vi.fn<typeof desktop.getBusiness>(),
}));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

/** The catalog, a page of leads and saved views, as the real worker answers over synthetic data. */
const catalog = catalogFixture as ExplorerCatalog;
const page = pageFixture as ExplorerPage;
const views = viewsFixture as SavedViewList;
const [template] = page.rows as [ExplorerRow];
const TOTAL = 1_000_000;

/** The lead at `index` of a million-lead run, in rank order or reversed; built only when read. */
function lead(index: number, reversed = false): ExplorerRow {
  const rank = reversed ? TOTAL - index : index + 1;
  return {
    ...template,
    business_id: `01900000-0000-7000-8000-${String(rank).padStart(12, "0")}`,
    rank,
    display_name: `Synthetic lead ${rank}`,
  };
}

/** One window of a run of `total` leads: a descending sort reverses them. */
function windowFor(query: ExplorerQuery, total = TOTAL): ExplorerPage {
  const reversed = query.sort.some((key) => key.direction === "desc");
  const size = Math.max(0, Math.min(query.limit, total - query.offset));
  return {
    ...page,
    offset: query.offset,
    rows: Array.from({ length: size }, (_, index) => lead(query.offset + index, reversed)),
    total: query.with_total ? total : null,
  };
}

function nth(items: HTMLElement[], index: number): HTMLElement {
  const item = items[index];
  if (!item) throw new Error(`no element at ${index}`);
  return item;
}

function renderScreen(path = "/lead-explorer") {
  const host = document.createElement("div");
  document.body.append(host);
  render(
    <MemoryRouter initialEntries={[path]}>
      <PageActionsProvider value={host}>
        <LeadExplorerScreen />
      </PageActionsProvider>
    </MemoryRouter>,
  );
  return host;
}

describe("LeadExplorerScreen", () => {
  beforeEach(() => {
    usePreferences.setState({ ...preferenceDefaults, language: "en" });
    useUiStore.setState({ inspectorOpen: false, inspectedId: null });
    useExplorerSession.setState(explorerSessionDefaults);
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.getExplorerCatalog.mockResolvedValue(catalog);
    mocks.exploreLeads.mockImplementation(async (query) => windowFor(query));
    // the inspector opens on Enter; what it reads is covered by its own tests
    mocks.getBusiness.mockReturnValue(new Promise(() => {}));
  });

  it("reads a million leads window by window and moves through them from the keyboard", async () => {
    renderScreen();
    const grid = await screen.findByRole("grid", { name: "Ranked leads" });
    expect(await screen.findByText("Synthetic lead 1")).toBeTruthy();
    expect(grid.getAttribute("aria-rowcount")).toBe(String(TOTAL + 1));
    expect(grid.getAttribute("aria-multiselectable")).toBe("true");
    expect(screen.getByText(`${TOTAL} leads`)).toBeTruthy();
    expect(mocks.exploreLeads).toHaveBeenCalledWith({
      score_run_id: null,
      filters: { kind: "group", join: "and", negate: false, children: [] },
      search: null,
      sort: [],
      offset: 0,
      limit: 100,
      with_total: true,
    });
    // only the rows in view exist: never the million
    expect(within(grid).getAllByRole("row").length).toBeLessThan(60);

    fireEvent.keyDown(grid, { key: "End" });
    await waitFor(() =>
      expect(mocks.exploreLeads).toHaveBeenCalledWith(
        expect.objectContaining({ offset: 999_900, with_total: false }),
      ),
    );
    const last = (await screen.findByText(`Synthetic lead ${TOTAL}`)).closest('[role="row"]');
    expect(last?.getAttribute("aria-rowindex")).toBe(String(TOTAL + 1));
    expect(grid.getAttribute("aria-activedescendant")).toBe(last?.id);

    fireEvent.keyDown(grid, { key: "Home" });
    expect(await screen.findByText("Synthetic lead 1")).toBeTruthy();
    fireEvent.keyDown(grid, { key: "ArrowDown" });
    fireEvent.keyDown(grid, { key: "Enter" });
    expect(useUiStore.getState().inspectedId).toBe(lead(1).business_id);
    // every window stays in the reads it needed: the first, the last, nothing between
    expect(new Set(mocks.exploreLeads.mock.calls.map(([query]) => query.offset))).toEqual(
      new Set([0, 999_900]),
    );
  });

  it("keeps a selection by id across windows and a new sort", async () => {
    const user = userEvent.setup();
    renderScreen();
    const grid = await screen.findByRole("grid", { name: "Ranked leads" });
    await user.click(await screen.findByRole("checkbox", { name: "Select Synthetic lead 2" }));
    fireEvent.keyDown(grid, { key: "End" });
    await screen.findByText(`Synthetic lead ${TOTAL}`);
    fireEvent.keyDown(grid, { key: " " });
    expect(screen.getByText("2 selected")).toBeTruthy();

    // descending order reads the leads reversed; the selection follows their ids
    await user.click(within(grid).getByRole("button", { name: /^Lead score/ }));
    await user.click(
      within(screen.getByRole("grid", { name: "Ranked leads" })).getByRole("button", {
        name: /^Lead score/,
      }),
    );
    await waitFor(() =>
      expect(mocks.exploreLeads).toHaveBeenLastCalledWith(
        expect.objectContaining({
          sort: [{ field: "total_score", direction: "desc" }],
          offset: 0,
        }),
      ),
    );
    const top = (await screen.findByText(`Synthetic lead ${TOTAL}`)).closest('[role="row"]');
    if (!(top instanceof HTMLElement)) throw new Error("the first row is shown");
    expect(top.getAttribute("aria-selected")).toBe("true");
    expect((within(top).getByRole("checkbox") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText("2 selected")).toBeTruthy();
    expect(useExplorerSession.getState().selected).toEqual(
      new Set([lead(1).business_id, lead(TOTAL - 1).business_id]),
    );
  });

  it("combines all-of and any-of groups, negated or not, into one query", async () => {
    const user = userEvent.setup();
    renderScreen();
    await screen.findByText("Synthetic lead 1");
    await user.click(screen.getByRole("button", { name: "Filters" }));
    const dialog = await screen.findByRole("dialog", { name: "Filters" });

    await user.click(within(dialog).getByRole("button", { name: "Add condition" }));
    fireEvent.change(within(dialog).getByRole("combobox", { name: "Field" }), {
      target: { value: "tier" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Add group" }));
    const addToGroup = () =>
      user.click(nth(within(dialog).getAllByRole("button", { name: "Add condition" }), 1));
    await addToGroup();
    await addToGroup();
    fireEvent.change(nth(within(dialog).getAllByRole("combobox", { name: "Field" }), 1), {
      target: { value: "suppressed" },
    });
    fireEvent.change(nth(within(dialog).getAllByRole("combobox", { name: "Field" }), 2), {
      target: { value: "total_score" },
    });
    fireEvent.change(nth(within(dialog).getAllByRole("combobox", { name: "Operator" }), 2), {
      target: { value: "gte" },
    });
    fireEvent.change(within(dialog).getByRole("spinbutton", { name: "Value" }), {
      target: { value: "70" },
    });
    await user.click(nth(within(dialog).getAllByRole("checkbox", { name: "Not" }), 1));
    // a condition still missing its value stays out of the query, and says so
    await user.click(nth(within(dialog).getAllByRole("button", { name: "Add condition" }), 0));
    expect(
      within(dialog).getByText("Incomplete conditions are left out of the query"),
    ).toBeTruthy();
    await user.click(within(dialog).getByRole("button", { name: "Apply" }));

    const tier = catalog.fields.find((info) => info.field === "tier")?.values[0];
    const expected: ExplorerGroup = {
      kind: "group",
      join: "and",
      negate: false,
      children: [
        { kind: "condition", field: "tier", operator: "eq", value: tier ?? null },
        {
          kind: "group",
          join: "or",
          negate: true,
          children: [
            { kind: "condition", field: "suppressed", operator: "is_true", value: null },
            { kind: "condition", field: "total_score", operator: "gte", value: 70 },
          ],
        },
      ],
    };
    await waitFor(() =>
      expect(mocks.exploreLeads).toHaveBeenLastCalledWith(
        expect.objectContaining({ filters: expected, offset: 0, with_total: true }),
      ),
    );
    expect(screen.getByRole("button", { name: "Filters (3)" })).toBeTruthy();
  });

  it("searches once the analyst stops typing", async () => {
    renderScreen();
    const search = await screen.findByRole("searchbox", { name: "Search leads" });
    await screen.findByText("Synthetic lead 1");
    for (const text of ["b", "ba", "bakery "])
      fireEvent.change(search, { target: { value: text } });
    await waitFor(() =>
      expect(mocks.exploreLeads).toHaveBeenLastCalledWith(
        expect.objectContaining({ search: "bakery", offset: 0 }),
      ),
    );
    // debounced: one query for the search, not one per key
    expect(mocks.exploreLeads.mock.calls.filter(([query]) => query.with_total)).toHaveLength(2);
  });

  it("never lets a slow answer to an earlier query replace a newer one", async () => {
    let answerFirst = () => {};
    mocks.exploreLeads
      .mockImplementationOnce(
        (query) =>
          new Promise((resolve) => {
            answerFirst = () => resolve(windowFor(query));
          }),
      )
      .mockImplementation(async (query) => {
        const window = windowFor(query);
        return {
          ...window,
          rows: window.rows.map((row) => ({ ...row, display_name: `${row.display_name} bakery` })),
        };
      });
    renderScreen();
    fireEvent.change(await screen.findByRole("searchbox", { name: "Search leads" }), {
      target: { value: "bakery" },
    });
    expect(await screen.findByText("Synthetic lead 1 bakery")).toBeTruthy();
    await act(async () => {
      answerFirst();
      await Promise.resolve();
    });
    expect(screen.queryByText("Synthetic lead 1")).toBeNull();
    expect(screen.getByText("Synthetic lead 1 bakery")).toBeTruthy();
  });

  it("marks do-not-contact, stale, partial, conflicting and test leads in words", async () => {
    mocks.exploreLeads.mockImplementation(async (query) => ({
      ...windowFor(query, 1),
      rows: [{ ...lead(0), suppressed: true, stale: true, partial: true, conflicts: 2 }],
    }));
    renderScreen();
    const row = (await screen.findByText("Synthetic lead 1")).closest('[role="row"]');
    if (!(row instanceof HTMLElement)) throw new Error("the lead is shown");
    for (const text of ["Do not contact", "Stale", "Partial", "2 conflicts", "Test data"]) {
      expect(within(row).getByText(text)).toBeTruthy();
    }
  });

  it("is honest when no run has scored leads and when the filters match nothing", async () => {
    mocks.exploreLeads.mockResolvedValue({ ...page, score_run: null, rows: [], total: 0 });
    renderScreen();
    expect(await screen.findByText("No scored leads yet")).toBeTruthy();
    cleanup();

    mocks.exploreLeads.mockResolvedValue({ ...page, rows: [], total: 0 });
    renderScreen();
    expect(await screen.findByText("No lead matches these filters")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Clear filters" })).toBeTruthy();
  });

  it("runs a bulk action over every matching lead only with the count it previewed", async () => {
    const user = userEvent.setup();
    mocks.exploreLeads.mockImplementation(async (query) => windowFor(query, 3));
    mocks.bulkLeads.mockImplementation(async (request: BulkRequest): Promise<BulkResult> => ({
      mode: request.mode,
      action: request.action,
      targeted: 3,
      changed: 2,
      skipped: 1,
      skips: [
        { business_id: lead(0).business_id, display_name: "Synthetic lead 1", reason: "unchanged" },
      ],
      batch_id: request.mode === "apply" ? "01900000-0000-7000-8000-000000009999" : null,
    }));
    const host = renderScreen();
    await user.click(await screen.findByRole("checkbox", { name: "Select Synthetic lead 2" }));
    await user.click(screen.getByRole("button", { name: "Select all 3 matching leads" }));
    expect(screen.getByText("All 3 matching leads are selected")).toBeTruthy();

    await user.click(within(host).getByRole("button", { name: "Bulk action" }));
    const dialog = await screen.findByRole("dialog", { name: "Bulk action" });
    expect(within(dialog).getByText("Every lead matching the current filters (3)")).toBeTruthy();
    expect(within(dialog).getByText(/never a bulk action/)).toBeTruthy();
    await user.click(within(dialog).getByLabelText("Tag"));
    await user.paste("shortlist");
    await user.click(within(dialog).getByRole("button", { name: "Preview" }));
    await waitFor(() =>
      expect(mocks.bulkLeads).toHaveBeenCalledWith(
        expect.objectContaining({
          mode: "preview",
          action: "add_tag",
          business_ids: [],
          expected: null,
          tag: "shortlist",
          reason: null,
        }),
      ),
    );
    expect(await within(dialog).findByText("Synthetic lead 1 · Already so")).toBeTruthy();
    await user.click(within(dialog).getByLabelText("Reason"));
    await user.paste("Synthetic shortlist");
    await user.click(within(dialog).getByRole("button", { name: "Apply" }));
    await waitFor(() =>
      expect(mocks.bulkLeads).toHaveBeenLastCalledWith(
        expect.objectContaining({ mode: "apply", expected: 3, reason: "Synthetic shortlist" }),
      ),
    );
    // the leads are read again after the change
    await waitFor(() =>
      expect(
        mocks.exploreLeads.mock.calls.filter(([query]) => query.with_total).length,
      ).toBeGreaterThan(1),
    );
  });

  it("applies a saved view, says it was upgraded, and brings the layout back after a restart", async () => {
    const user = userEvent.setup();
    mocks.listSavedViews.mockResolvedValue(views);
    const legacy = views.items.find((item) => item.upgraded_from !== null);
    if (!legacy) throw new Error("the fixture holds a view saved with an older schema");
    const host = renderScreen();
    await screen.findByText("Synthetic lead 1");
    await user.click(within(host).getByRole("button", { name: "Views" }));
    const dialog = await screen.findByRole("dialog", { name: "Saved views" });
    expect(
      await within(dialog).findByText(`Upgraded from schema ${legacy.upgraded_from}`),
    ).toBeTruthy();
    const item = within(dialog).getByText(legacy.view.name).closest("li");
    if (!(item instanceof HTMLElement)) throw new Error("the view is listed");
    await user.click(within(item).getByRole("button", { name: "Apply" }));
    const applied = expect.objectContaining({
      filters: legacy.definition.filters,
      sort: legacy.definition.sort,
      offset: 0,
    });
    await waitFor(() => expect(mocks.exploreLeads).toHaveBeenLastCalledWith(applied));
    expect(screen.getByText(`View: ${legacy.view.name}`)).toBeTruthy();
    expect(localStorage.getItem(PREFERENCES_STORAGE_KEY)).toContain(legacy.view.id);

    // a restart: the explorer mounts again and queries the layout it left
    cleanup();
    mocks.exploreLeads.mockClear();
    renderScreen();
    await waitFor(() => expect(mocks.exploreLeads).toHaveBeenCalledWith(applied));
  });

  it("falls back to the default view when the stored layout has an older schema", async () => {
    usePreferences.setState({
      explorer: {
        definition: {
          ...catalog.default_view,
          schema_version: 1,
          sort: [{ field: "rank", direction: "desc" }],
        } as unknown as ViewDefinition,
        viewId: null,
      },
    });
    renderScreen();
    await waitFor(() =>
      expect(mocks.exploreLeads).toHaveBeenCalledWith(expect.objectContaining({ sort: [] })),
    );
  });

  it("opens the run a link names, and goes back to the newest run on request", async () => {
    const user = userEvent.setup();
    const runId = "01900000-0000-7000-8000-000000001398";
    renderScreen(`/lead-explorer?run=${runId}`);
    await waitFor(() =>
      expect(mocks.exploreLeads).toHaveBeenCalledWith(
        expect.objectContaining({ score_run_id: runId }),
      ),
    );
    await user.click(await screen.findByRole("button", { name: "Show the newest run" }));
    await waitFor(() =>
      expect(mocks.exploreLeads).toHaveBeenLastCalledWith(
        expect.objectContaining({ score_run_id: null, offset: 0 }),
      ),
    );
  });
});
