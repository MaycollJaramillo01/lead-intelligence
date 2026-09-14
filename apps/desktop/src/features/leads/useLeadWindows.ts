import { useCallback, useEffect, useRef, useState } from "react";
import type { ExplorerQuery, ExplorerRow, ScoreRunItem } from "@lead/shared-types";
import { WINDOW_CACHE, windowOf, windowsBetween } from "@/features/leads/query";
import { desktop, toIpcError, type IpcError } from "@/lib/ipc";

export interface LeadWindows {
  /** Leads the query matches; null until its first window answers. */
  total: number | null;
  run: ScoreRunItem | null;
  /** The first window of the current query is in flight. */
  loading: boolean;
  /** The rows shown still answer an earlier query while the current one loads. */
  stale: boolean;
  error: IpcError | null;
  /** Windows after the first that could not be read. */
  failed: number;
  generatedAt: string | null;
  rowAt: (index: number) => ExplorerRow | undefined;
  /** Reads the windows holding rows `first` to `last` that are not loaded yet. */
  ensure: (first: number, last: number) => void;
  reload: () => void;
}

interface Loaded {
  /** The query these windows answer; an earlier one while a newer query loads. */
  query: ExplorerQuery | null;
  total: number | null;
  run: ScoreRunItem | null;
  generatedAt: string | null;
  windows: ReadonlyMap<number, readonly ExplorerRow[]>;
  failed: ReadonlySet<number>;
  error: { query: ExplorerQuery; error: IpcError } | null;
}

const NOTHING: Loaded = {
  query: null,
  total: null,
  run: null,
  generatedAt: null,
  windows: new Map(),
  failed: new Set(),
  error: null,
};

/**
 * The leads of an explorer query, read window by window (phase 12). The first window counts the
 * matches; after it the grid asks only for the windows on screen, and at most `WINDOW_CACHE`
 * windows stay in memory. A newer query or a reload wins: an answer to an earlier one is dropped,
 * so a slow response never replaces a newer one. `base` must be memoised by its caller.
 */
export function useLeadWindows(base: ExplorerQuery | null): LeadWindows {
  const [loaded, setLoaded] = useState<Loaded>(NOTHING);
  const generation = useRef(0);
  const pending = useRef(new Set<number>());

  /** Reads the first window of a query, counting its leads; any earlier read is superseded. */
  const read = useCallback((query: ExplorerQuery) => {
    generation.current += 1;
    const current = generation.current;
    pending.current = new Set();
    desktop.exploreLeads({ ...query, offset: 0, with_total: true }).then(
      (page) => {
        if (current !== generation.current) return;
        setLoaded({
          query,
          total: page.total ?? page.rows.length,
          run: page.score_run,
          generatedAt: page.generated_at,
          windows: new Map([[0, page.rows]]),
          failed: new Set(),
          error: null,
        });
      },
      (cause: unknown) => {
        if (current !== generation.current) return;
        setLoaded((previous) => ({
          ...previous,
          error: { query, error: toIpcError(cause, "explore_leads") },
        }));
      },
    );
  }, []);

  useEffect(() => {
    if (base !== null) read(base);
  }, [base, read]);

  const answered = base !== null && loaded.query === base;
  const total = loaded.total;
  const windows = loaded.windows;
  const failed = loaded.failed;

  const ensure = useCallback(
    (first: number, last: number) => {
      if (!answered || total === null || total === 0) return;
      const current = generation.current;
      const keep = new Set(windowsBetween(first, last));
      for (const offset of windowsBetween(first, Math.min(last, total - 1))) {
        if (windows.has(offset) || failed.has(offset) || pending.current.has(offset)) continue;
        pending.current.add(offset);
        desktop.exploreLeads({ ...base, offset, with_total: false }).then(
          (page) => {
            pending.current.delete(offset);
            if (current !== generation.current) return;
            setLoaded((previous) => {
              if (previous.query !== base) return previous;
              const next = new Map(previous.windows);
              next.set(offset, page.rows);
              // drop the oldest windows first, never the ones on screen
              for (const key of next.keys()) {
                if (next.size <= WINDOW_CACHE) break;
                if (!keep.has(key)) next.delete(key);
              }
              return { ...previous, windows: next };
            });
          },
          () => {
            pending.current.delete(offset);
            if (current !== generation.current) return;
            setLoaded((previous) =>
              previous.query !== base
                ? previous
                : { ...previous, failed: new Set(previous.failed).add(offset) },
            );
          },
        );
      }
    },
    [answered, base, total, windows, failed],
  );

  const rowAt = useCallback(
    (index: number) => {
      const offset = windowOf(index);
      return windows.get(offset)?.[index - offset];
    },
    [windows],
  );

  const reload = useCallback(() => {
    if (base !== null) read(base);
  }, [base, read]);
  const error = base !== null && loaded.error?.query === base ? loaded.error.error : null;

  return {
    total,
    run: loaded.run,
    loading: base !== null && !answered && error === null,
    stale: base !== null && !answered && loaded.query !== null,
    error,
    failed: answered ? failed.size : 0,
    generatedAt: loaded.generatedAt,
    rowAt,
    ensure,
    reload,
  };
}
