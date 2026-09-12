import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { findNavItem } from "@/app/navigation";
import {
  comboFromEvent,
  findConflicts,
  formatCombo,
  SHORTCUTS,
  useShortcuts,
} from "@/lib/shortcuts";

function press(key: string, modifiers: Partial<KeyboardEventInit> = {}, target?: HTMLElement) {
  const event = new KeyboardEvent("keydown", {
    key,
    bubbles: true,
    cancelable: true,
    ...modifiers,
  });
  (target ?? window).dispatchEvent(event);
  return event;
}

describe("shortcut registry", () => {
  it("never assigns the same combo twice", () => {
    expect(findConflicts()).toEqual([]);
  });

  it("detects a conflict when one is introduced", () => {
    const conflicts = findConflicts([
      ...SHORTCUTS,
      { id: "duplicate", combo: "ctrl+k", group: "global", descriptionKey: "shortcuts.refresh" },
    ]);
    expect(conflicts).toEqual(["ctrl+k"]);
  });

  it("points every navigation shortcut at a real module", () => {
    const brokenTargets = SHORTCUTS.filter(
      (shortcut) => shortcut.group === "navigation" && !findNavItem(shortcut.target ?? ""),
    );
    expect(brokenTargets.map((shortcut) => shortcut.id)).toEqual([]);
  });

  it("normalises events into combos, ignoring shift for symbol keys", () => {
    expect(
      comboFromEvent({ key: "k", ctrlKey: true, altKey: false, shiftKey: false, metaKey: false }),
    ).toBe("ctrl+k");
    expect(
      comboFromEvent({ key: "?", ctrlKey: false, altKey: false, shiftKey: true, metaKey: false }),
    ).toBe("?");
    expect(
      comboFromEvent({ key: "T", ctrlKey: false, altKey: true, shiftKey: true, metaKey: false }),
    ).toBe("alt+shift+t");
  });

  it("formats combos for display", () => {
    expect(formatCombo("ctrl+k")).toEqual(["Ctrl", "K"]);
    expect(formatCombo("alt+1")).toEqual(["Alt", "1"]);
  });
});

describe("useShortcuts", () => {
  it("runs the handler for a registered combo", () => {
    const run = vi.fn<() => void>();
    renderHook(() => useShortcuts({ "toggle-sidebar": run }));
    const event = press("b", { ctrlKey: true });
    expect(run).toHaveBeenCalledOnce();
    expect(event.defaultPrevented).toBe(true);
  });

  it("ignores shortcuts while typing, except those marked otherwise", () => {
    const palette = vi.fn<() => void>();
    const sidebar = vi.fn<() => void>();
    renderHook(() => useShortcuts({ "command-palette": palette, "toggle-sidebar": sidebar }));
    const input = document.createElement("input");
    document.body.append(input);
    input.focus();

    press("b", { ctrlKey: true }, input);
    expect(sidebar).not.toHaveBeenCalled();

    press("k", { ctrlKey: true }, input);
    expect(palette).toHaveBeenCalledOnce();
    input.remove();
  });

  it("does nothing for unregistered combos", () => {
    const run = vi.fn<() => void>();
    renderHook(() => useShortcuts({ "toggle-sidebar": run }));
    const event = press("q", { ctrlKey: true });
    expect(run).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });
});
