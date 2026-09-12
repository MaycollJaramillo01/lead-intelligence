import { useCallback, useEffect, useRef, useState } from "react";
import { toIpcError, type IpcError } from "@/lib/ipc";

interface LoadedState<T> {
  data: T | null;
  error: IpcError | null;
}

/**
 * Loads one answer from the desktop core and keeps it, with the error that explains a failure.
 * `load` must be memoised: a new function reloads. The answer to a superseded call is dropped, and
 * the previous data stays on screen until a new one arrives.
 */
export function useLoaded<T>(load: () => Promise<T>, command: string) {
  const [state, setState] = useState<LoadedState<T>>({ data: null, error: null });
  const latest = useRef(0);

  const reload = useCallback(() => {
    const id = ++latest.current;
    return load().then(
      (data) => {
        if (id === latest.current) setState({ data, error: null });
      },
      (cause: unknown) => {
        if (id === latest.current) {
          setState((current) => ({ ...current, error: toIpcError(cause, command) }));
        }
      },
    );
  }, [load, command]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { ...state, reload };
}
