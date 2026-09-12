import { beforeEach, describe, expect, it } from "vitest";
import {
  preferenceDefaults,
  PREFERENCES_STORAGE_KEY,
  SIDEBAR_MAX_WIDTH,
  SIDEBAR_MIN_WIDTH,
  usePreferences,
} from "@/stores/preferences";

describe("preferences store", () => {
  beforeEach(() => {
    usePreferences.setState(preferenceDefaults);
    localStorage.clear();
  });

  it("starts dark, comfortable and in English", () => {
    const state = usePreferences.getState();
    expect(state.theme).toBe("dark");
    expect(state.density).toBe("comfortable");
    expect(state.language).toBe("en");
    expect(state.sidebarCollapsed).toBe(false);
  });

  it("clamps the sidebar width to its allowed range", () => {
    const { setSidebarWidth } = usePreferences.getState();
    setSidebarWidth(10);
    expect(usePreferences.getState().sidebarWidth).toBe(SIDEBAR_MIN_WIDTH);
    setSidebarWidth(5000);
    expect(usePreferences.getState().sidebarWidth).toBe(SIDEBAR_MAX_WIDTH);
    setSidebarWidth(240);
    expect(usePreferences.getState().sidebarWidth).toBe(240);
  });

  it("keeps column layouts per table", () => {
    const { setColumnVisibility } = usePreferences.getState();
    setColumnVisibility("businesses", { phone: false });
    setColumnVisibility("lead-explorer", { score: false });
    const { columnVisibility } = usePreferences.getState();
    expect(columnVisibility.businesses).toEqual({ phone: false });
    expect(columnVisibility["lead-explorer"]).toEqual({ score: false });
  });

  it("persists preferences on this machine and restores them", () => {
    usePreferences.getState().setDensity("compact");
    usePreferences.getState().setLanguage("es");
    const stored = localStorage.getItem(PREFERENCES_STORAGE_KEY);
    expect(stored).toBeTruthy();
    const parsed = JSON.parse(stored ?? "{}") as { state: { density: string; language: string } };
    expect(parsed.state.density).toBe("compact");
    expect(parsed.state.language).toBe("es");
  });

  it("resets back to the defaults", () => {
    const state = usePreferences.getState();
    state.setTheme("light");
    state.toggleSidebar();
    state.setColumnVisibility("businesses", { phone: false });
    state.reset();
    const reset = usePreferences.getState();
    expect(reset.theme).toBe("dark");
    expect(reset.sidebarCollapsed).toBe(false);
    expect(reset.columnVisibility).toEqual({});
  });
});
