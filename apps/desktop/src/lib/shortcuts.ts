import { useEffect } from "react";
import type { TranslationKey } from "@/lib/i18n";

export type ShortcutGroup = "global" | "navigation" | "view";

export interface ShortcutDefinition {
  id: string;
  /** Normalised combo: "ctrl", "alt", "shift" in that order, then a lowercase key. */
  combo: string;
  group: ShortcutGroup;
  descriptionKey: TranslationKey;
  /** Target module id, for navigation shortcuts. */
  target?: string;
  /** Very few shortcuts may fire while a field has focus (the palette). */
  allowInInput?: boolean;
}

export const SHORTCUTS: readonly ShortcutDefinition[] = [
  {
    id: "command-palette",
    combo: "ctrl+k",
    group: "global",
    descriptionKey: "shortcuts.commandPalette",
    allowInInput: true,
  },
  { id: "shortcuts-help", combo: "?", group: "global", descriptionKey: "shortcuts.shortcutsHelp" },
  { id: "settings", combo: "ctrl+,", group: "global", descriptionKey: "shortcuts.settings" },
  {
    id: "refresh-diagnostics",
    combo: "alt+r",
    group: "global",
    descriptionKey: "shortcuts.refresh",
  },

  {
    id: "toggle-sidebar",
    combo: "ctrl+b",
    group: "view",
    descriptionKey: "shortcuts.toggleSidebar",
  },
  {
    id: "toggle-inspector",
    combo: "ctrl+i",
    group: "view",
    descriptionKey: "shortcuts.toggleInspector",
  },
  { id: "toggle-theme", combo: "alt+t", group: "view", descriptionKey: "shortcuts.toggleTheme" },
  {
    id: "toggle-density",
    combo: "alt+d",
    group: "view",
    descriptionKey: "shortcuts.toggleDensity",
  },

  {
    id: "goto-overview",
    combo: "alt+1",
    group: "navigation",
    descriptionKey: "shortcuts.goto",
    target: "overview",
  },
  {
    id: "goto-businesses",
    combo: "alt+2",
    group: "navigation",
    descriptionKey: "shortcuts.goto",
    target: "businesses",
  },
  {
    id: "goto-lead-explorer",
    combo: "alt+3",
    group: "navigation",
    descriptionKey: "shortcuts.goto",
    target: "lead-explorer",
  },
  {
    id: "goto-jobs",
    combo: "alt+4",
    group: "navigation",
    descriptionKey: "shortcuts.goto",
    target: "jobs",
  },
  {
    id: "goto-system",
    combo: "alt+5",
    group: "navigation",
    descriptionKey: "shortcuts.goto",
    target: "system",
  },
  {
    id: "goto-settings",
    combo: "alt+6",
    group: "navigation",
    descriptionKey: "shortcuts.goto",
    target: "settings",
  },
];

/** Combos claimed by more than one shortcut. Must stay empty (covered by a test). */
export function findConflicts(shortcuts: readonly ShortcutDefinition[] = SHORTCUTS): string[] {
  const seen = new Set<string>();
  const conflicts = new Set<string>();
  for (const shortcut of shortcuts) {
    if (seen.has(shortcut.combo)) conflicts.add(shortcut.combo);
    seen.add(shortcut.combo);
  }
  return [...conflicts];
}

type KeyboardEventLike = Pick<KeyboardEvent, "key" | "ctrlKey" | "altKey" | "shiftKey" | "metaKey">;

/** Builds the normalised combo for a keyboard event. Shift is ignored for symbol keys. */
export function comboFromEvent(event: KeyboardEventLike): string {
  const key = event.key.toLowerCase();
  const isSymbol = key.length === 1 && !/[a-z0-9]/.test(key);
  const parts: string[] = [];
  if (event.ctrlKey || event.metaKey) parts.push("ctrl");
  if (event.altKey) parts.push("alt");
  if (event.shiftKey && !isSymbol) parts.push("shift");
  parts.push(key);
  return parts.join("+");
}

/** Splits a combo into display chunks: "ctrl+k" becomes ["Ctrl", "K"]. */
export function formatCombo(combo: string): string[] {
  return combo.split("+").map((part) => {
    if (part === "ctrl") return "Ctrl";
    if (part === "alt") return "Alt";
    if (part === "shift") return "Shift";
    return part.length === 1 ? part.toUpperCase() : part;
  });
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    ["input", "textarea", "select"].includes(target.tagName.toLowerCase())
  );
}

export type ShortcutHandlers = Partial<Record<string, () => void>>;

/** Binds the registry to handlers. Shortcuts without a handler simply do nothing. */
export function useShortcuts(handlers: ShortcutHandlers): void {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const combo = comboFromEvent(event);
      const shortcut = SHORTCUTS.find((candidate) => candidate.combo === combo);
      if (!shortcut) return;
      if (isTypingTarget(event.target) && !shortcut.allowInInput) return;
      const handler = handlers[shortcut.id];
      if (!handler) return;
      event.preventDefault();
      handler();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [handlers]);
}
