import { useEffect } from "react";
import { reportUiError } from "@/lib/errors";
import { desktop } from "@/lib/ipc";
import { useDiagnosticsStore } from "@/features/diagnostics/store";

/**
 * Keeps live worker status flowing into the app: one initial system check plus the
 * `worker://status` event stream. A worker that comes back up triggers a fresh check.
 * Mounted once by the shell so every screen shares the same remote state.
 */
export function useDiagnosticsLifecycle(): void {
  useEffect(() => {
    void useDiagnosticsStore.getState().refresh();

    let disposed = false;
    let unlisten: (() => void) | undefined;
    desktop
      .onWorkerStatus((status) => {
        const store = useDiagnosticsStore.getState();
        const wasRunning = store.worker?.state === "running";
        store.applyWorkerStatus(status);
        if (status.state === "running" && !wasRunning) void store.refresh();
      })
      .then(
        (stop) => {
          if (disposed) stop();
          else unlisten = stop;
        },
        (cause: unknown) => reportUiError("diagnostics.events", cause),
      );

    return () => {
      disposed = true;
      unlisten?.();
    };
  }, []);
}
