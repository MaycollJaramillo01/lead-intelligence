import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SourceCatalog } from "@lead/shared-types";
import catalogFixture from "@lead/shared-types/fixtures/source_catalog.json";
import { SourcesScreen } from "@/features/sources/SourcesScreen";
import { IpcError, type desktop } from "@/lib/ipc";

const mocks = vi.hoisted(() => ({ listSources: vi.fn<typeof desktop.listSources>() }));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

const catalog = catalogFixture as SourceCatalog;

function renderSources(path = "/sources") {
  render(
    <MemoryRouter initialEntries={[path]}>
      <SourcesScreen />
    </MemoryRouter>,
  );
}

describe("SourcesScreen", () => {
  beforeEach(() => {
    mocks.listSources.mockReset();
    mocks.listSources.mockResolvedValue(catalog);
  });

  it("shows every source with its terms, limits and what it can filter on", async () => {
    renderSources();
    expect(await screen.findByText("USAspending.gov award recipients")).toBeTruthy();
    expect(screen.getByText("Local file import (CSV, TSV, JSON, JSONL)")).toBeTruthy();
    expect(screen.getAllByText("30/min").length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: "Terms of use" }).length).toBeGreaterThan(0);
    expect(screen.getAllByText("NAICS").length).toBeGreaterThan(0);
  });

  it("marks the source a link points at, and says when no source has that key", async () => {
    const [source] = catalog.sources;
    if (!source) throw new Error("the fixture holds a source");
    renderSources(`/sources?source=${source.metadata.key}`);
    const name = await screen.findByText(source.metadata.name);
    expect(name.closest("article")?.getAttribute("aria-current")).toBe("true");
  });

  it("says when no source has the key a link names", async () => {
    renderSources("/sources?source=retired.source");
    expect(await screen.findByText("No source has the key retired.source")).toBeTruthy();
  });

  it("says a source that needs credentials is not ready, instead of hiding it", async () => {
    renderSources();
    expect(
      await screen.findByText("Business directory API (template, not configured)"),
    ).toBeTruthy();
    expect(screen.getByText("Needs configuration")).toBeTruthy();
    expect(screen.getByText(/API key/)).toBeTruthy();
  });

  it("states plainly that it never works around a source's restrictions", async () => {
    renderSources();
    const intro = await screen.findByText(/never solves CAPTCHAs/);
    expect(intro.textContent).toContain("stays disabled");
  });

  it("reports why the catalogue could not be read", async () => {
    mocks.listSources.mockRejectedValue(new IpcError("desktop_unavailable", "Core not reachable"));
    renderSources();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("The source catalogue could not be read");
    expect(alert.textContent).toContain("Core not reachable");
  });
});
