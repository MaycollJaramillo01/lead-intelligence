import { describe, expect, it } from "vitest";
import { formatDateTime, translate } from "@/lib/i18n";
import { en } from "@/lib/locales/en";
import { es } from "@/lib/locales/es";

describe("translate", () => {
  it("returns the string for the active language", () => {
    expect(translate("en", "nav.settings")).toBe("Settings");
    expect(translate("es", "nav.settings")).toBe("Ajustes");
  });

  it("falls back to English when a Spanish string is missing", () => {
    expect(translate("es", "app.name")).toBe(en["app.name"]);
  });

  it("interpolates variables and leaves unknown placeholders alone", () => {
    expect(translate("en", "common.availableInPhase", { phase: 11 })).toBe("Planned for phase 11");
    expect(translate("es", "common.selectedCount", { count: 3 })).toBe("3 seleccionadas");
    expect(translate("en", "common.availableInPhase", {})).toContain("{phase}");
  });

  it("keeps the Spanish dictionary free of unknown keys", () => {
    for (const key of Object.keys(es)) expect(en).toHaveProperty(key);
  });

  it("formats timestamps for the active language", () => {
    const iso = "2026-01-02T03:04:05Z";
    expect(formatDateTime(iso, "en")).not.toBe(formatDateTime(iso, "es"));
  });
});
