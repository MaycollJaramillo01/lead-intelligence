import { create } from "zustand";

/** Ephemeral UI state: never persisted, never sent anywhere. */
interface UiState {
  commandPaletteOpen: boolean;
  shortcutsHelpOpen: boolean;
  inspectorOpen: boolean;
  /** Row id selected in the current screen's table, or null. */
  inspectedId: string | null;
  openCommandPalette: () => void;
  closeCommandPalette: () => void;
  toggleCommandPalette: () => void;
  setShortcutsHelpOpen: (open: boolean) => void;
  toggleShortcutsHelp: () => void;
  toggleInspector: () => void;
  setInspectorOpen: (open: boolean) => void;
  inspect: (id: string | null) => void;
}

export const useUiStore = create<UiState>()((set) => ({
  commandPaletteOpen: false,
  shortcutsHelpOpen: false,
  inspectorOpen: true,
  inspectedId: null,
  openCommandPalette: () => set({ commandPaletteOpen: true }),
  closeCommandPalette: () => set({ commandPaletteOpen: false }),
  toggleCommandPalette: () => set((state) => ({ commandPaletteOpen: !state.commandPaletteOpen })),
  setShortcutsHelpOpen: (open) => set({ shortcutsHelpOpen: open }),
  toggleShortcutsHelp: () => set((state) => ({ shortcutsHelpOpen: !state.shortcutsHelpOpen })),
  toggleInspector: () => set((state) => ({ inspectorOpen: !state.inspectorOpen })),
  setInspectorOpen: (open) => set({ inspectorOpen: open }),
  inspect: (id) => set({ inspectedId: id, inspectorOpen: id !== null }),
}));
