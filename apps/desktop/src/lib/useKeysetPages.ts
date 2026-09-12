import { useCallback, useEffect, useRef, useState } from "react";
import { toIpcError, type IpcError } from "@/lib/ipc";

/** Shape shared by every keyset-paginated answer of the desktop core. */
export interface KeysetPage<T> {
  items: T[];
  next_cursor: string | null;
  total: number;
}

interface PagesState<T> {
  items: T[];
  total: number;
  nextCursor: string | null;
  loading: boolean;
  error: IpcError | null;
}

/**
 * Loads a list page by page. `fetchPage` must be memoised: a new function (e.g. a new search)
 * starts again from the first page, keeping the previous rows on screen until it answers.
 * Answers to superseded requests are dropped.
 */
export function useKeysetPages<T>(
  fetchPage: (cursor: string | null) => Promise<KeysetPage<T>>,
  command: string,
) {
  const [state, setState] = useState<PagesState<T>>({
    items: [],
    total: 0,
    nextCursor: null,
    loading: true,
    error: null,
  });
  const latest = useRef(0);

  const settle = useCallback(
    (cursor: string | null, answer: Promise<KeysetPage<T>>) => {
      const id = ++latest.current;
      return answer.then(
        (page) => {
          if (id !== latest.current) return;
          setState((current) => ({
            items: cursor === null ? page.items : [...current.items, ...page.items],
            total: page.total,
            nextCursor: page.next_cursor,
            loading: false,
            error: null,
          }));
        },
        (cause: unknown) => {
          if (id !== latest.current) return;
          setState((current) => ({
            ...current,
            loading: false,
            error: toIpcError(cause, command),
          }));
        },
      );
    },
    [command],
  );

  useEffect(() => {
    void settle(null, fetchPage(null));
  }, [settle, fetchPage]);

  const { nextCursor } = state;
  const reload = useCallback(() => {
    setState((current) => ({ ...current, loading: true }));
    return settle(null, fetchPage(null));
  }, [settle, fetchPage]);
  const loadMore = useCallback(() => {
    if (nextCursor === null) return Promise.resolve();
    setState((current) => ({ ...current, loading: true }));
    return settle(nextCursor, fetchPage(nextCursor));
  }, [settle, fetchPage, nextCursor]);

  return { ...state, reload, loadMore };
}
