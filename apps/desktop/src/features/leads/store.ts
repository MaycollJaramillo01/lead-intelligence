import { create } from "zustand";

interface GridPosition {
  /** The query the position belongs to. */
  key: string;
  scrollTop: number;
  active: number;
}

/**
 * What the Lead Explorer keeps while the analyst opens a business and comes back: the leads selected
 * by id (a selection survives paging, sorting and new filters), whether every lead of a query is
 * selected, and where the grid of that query was. Never persisted: a selection is work in progress,
 * not a preference.
 */
interface ExplorerSession {
  selected: ReadonlySet<string>;
  /** The match key of the query whose every lead is selected, or null. */
  allMatching: string | null;
  position: GridPosition;
  toggle: (id: string) => void;
  /** Selects every lead of the query with this match key, or drops that selection (null). */
  selectAll: (key: string | null) => void;
  clear: () => void;
  keep: (position: GridPosition) => void;
}

export const explorerSessionDefaults = {
  selected: new Set<string>(),
  allMatching: null,
  position: { key: "", scrollTop: 0, active: 0 },
} satisfies Pick<ExplorerSession, "selected" | "allMatching" | "position">;

export const useExplorerSession = create<ExplorerSession>()((set) => ({
  ...explorerSessionDefaults,
  toggle: (id) =>
    set((state) => {
      const selected = new Set(state.selected);
      if (!selected.delete(id)) selected.add(id);
      return { selected, allMatching: null };
    }),
  selectAll: (key) => set({ allMatching: key }),
  clear: () => set({ selected: new Set(), allMatching: null }),
  keep: (position) => set({ position }),
}));
