import { describe, expect, it } from "vitest";
import { NAV_ITEMS, NAV_SECTIONS, findNavItem, findNavItemByPath } from "@/app/navigation";
import { en } from "@/lib/locales/en";
import { es } from "@/lib/locales/es";

/** The 16 modules the product promises, in the order the prompt defines them. */
const EXPECTED_MODULES = [
  "overview",
  "discovery",
  "campaigns",
  "sources",
  "businesses",
  "lead-explorer",
  "research-center",
  "icp-builder",
  "scoring-models",
  "ai-models",
  "jobs",
  "datasets",
  "exports",
  "system",
  "logs",
  "settings",
];

describe("navigation registry", () => {
  it("covers every module exactly once", () => {
    expect(NAV_ITEMS.map((item) => item.id).sort()).toEqual([...EXPECTED_MODULES].sort());
    expect(new Set(NAV_ITEMS.map((item) => item.path)).size).toBe(NAV_ITEMS.length);
  });

  it("places every module in a declared section", () => {
    const sections = new Set(NAV_SECTIONS.map((section) => section.id));
    expect(NAV_ITEMS.filter((item) => !sections.has(item.section))).toEqual([]);
  });

  it("states a delivery phase for every module that is not configured", () => {
    const missingPhase = NAV_ITEMS.filter(
      (item) => item.status === "not_configured" && typeof item.phase !== "number",
    );
    const availableWithPhase = NAV_ITEMS.filter(
      (item) => item.status === "available" && item.phase !== undefined,
    );
    expect(missingPhase.map((item) => item.id)).toEqual([]);
    expect(availableWithPhase.map((item) => item.id)).toEqual([]);
  });

  it("has English and Spanish strings for every module", () => {
    const missing = NAV_ITEMS.filter(
      (item) =>
        !en[item.titleKey] ||
        !en[item.descriptionKey] ||
        !es[item.titleKey] ||
        !es[item.descriptionKey],
    );
    expect(missing.map((item) => item.id)).toEqual([]);
  });

  it("looks modules up by id and by path", () => {
    expect(findNavItem("system")?.path).toBe("/system");
    expect(findNavItemByPath("/lead-explorer")?.id).toBe("lead-explorer");
    expect(findNavItemByPath("/businesses/01900000-0000-7000-8000-000000000001")?.id).toBe(
      "businesses",
    );
    expect(findNavItemByPath("/nope")).toBeUndefined();
    expect(findNavItemByPath("/businessesque")).toBeUndefined();
  });
});
