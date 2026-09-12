import { create } from "zustand";
import { persist } from "zustand/middleware";

export type ThemePreference = "system" | "light" | "dark";
export type Density = "comfortable" | "compact";
export type Language = "en" | "es";

export const SIDEBAR_MIN_WIDTH = 176;
export const SIDEBAR_MAX_WIDTH = 340;
export const PREFERENCES_STORAGE_KEY = "lead-intelligence.preferences";

/** Preferences that survive restarts. Nothing here is business data. */
interface PreferencesState {
  theme: ThemePreference;
  density: Density;
  language: Language;
  sidebarCollapsed: boolean;
  sidebarWidth: number;
  /** Inspector width as a percentage of the content area. */
  inspectorSize: number;
  /** Column visibility per table id, so each table keeps its own layout. */
  columnVisibility: Record<string, Record<string, boolean>>;
  setTheme: (theme: ThemePreference) => void;
  setDensity: (density: Density) => void;
  setLanguage: (language: Language) => void;
  toggleSidebar: () => void;
  setSidebarWidth: (width: number) => void;
  setInspectorSize: (size: number) => void;
  setColumnVisibility: (tableId: string, visibility: Record<string, boolean>) => void;
  reset: () => void;
}

const DEFAULTS = {
  theme: "dark",
  density: "comfortable",
  language: "en",
  sidebarCollapsed: false,
  sidebarWidth: 232,
  inspectorSize: 32,
  columnVisibility: {},
} satisfies Omit<
  PreferencesState,
  | "setTheme"
  | "setDensity"
  | "setLanguage"
  | "toggleSidebar"
  | "setSidebarWidth"
  | "setInspectorSize"
  | "setColumnVisibility"
  | "reset"
>;

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

export const usePreferences = create<PreferencesState>()(
  persist(
    (set) => ({
      ...DEFAULTS,
      setTheme: (theme) => set({ theme }),
      setDensity: (density) => set({ density }),
      setLanguage: (language) => set({ language }),
      toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
      setSidebarWidth: (width) =>
        set({ sidebarWidth: Math.round(clamp(width, SIDEBAR_MIN_WIDTH, SIDEBAR_MAX_WIDTH)) }),
      setInspectorSize: (size) => set({ inspectorSize: clamp(size, 20, 60) }),
      setColumnVisibility: (tableId, visibility) =>
        set((state) => ({
          columnVisibility: { ...state.columnVisibility, [tableId]: visibility },
        })),
      reset: () => set(DEFAULTS),
    }),
    { name: PREFERENCES_STORAGE_KEY, version: 1 },
  ),
);

export const preferenceDefaults = DEFAULTS;
