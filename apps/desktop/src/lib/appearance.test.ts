import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { installAppearanceSync, resolveTheme } from "@/lib/appearance";
import { preferenceDefaults, usePreferences } from "@/stores/preferences";

describe("appearance sync", () => {
  let stop: (() => void) | undefined;

  beforeEach(() => {
    usePreferences.setState({ ...preferenceDefaults, language: "en" });
    document.documentElement.className = "";
  });

  afterEach(() => {
    stop?.();
    stop = undefined;
  });

  it("resolves the system preference", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
    expect(resolveTheme("light", true)).toBe("light");
  });

  it("mirrors theme, density and language onto the document", () => {
    stop = installAppearanceSync();
    expect(document.documentElement.classList.contains("dark")).toBe(true);

    usePreferences.getState().setTheme("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    usePreferences.getState().setDensity("compact");
    expect(document.documentElement.dataset.density).toBe("compact");

    usePreferences.getState().setLanguage("es");
    expect(document.documentElement.lang).toBe("es");
  });

  it("stops mirroring once disposed", () => {
    stop = installAppearanceSync();
    stop();
    stop = undefined;
    usePreferences.getState().setDensity("compact");
    expect(document.documentElement.dataset.density).toBe("comfortable");
  });
});
