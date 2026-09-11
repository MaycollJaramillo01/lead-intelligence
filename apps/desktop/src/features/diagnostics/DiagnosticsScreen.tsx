import { Fragment, useEffect } from "react";
import { RefreshCw, RotateCcw } from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { Button } from "@/components/ui/button";
import { reportUiError } from "@/lib/errors";
import { desktop } from "@/lib/ipc";
import { cn } from "@/lib/utils";
import { toRows, type DiagnosticRow } from "./model";
import { StateBadge } from "./StateBadge";
import { useDiagnosticsStore } from "./store";

/** Live worker lifecycle events; a worker that (re)starts triggers a fresh diagnostics run. */
function useWorkerStatusEvents() {
  useEffect(() => {
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

function Row({ row }: { row: DiagnosticRow }) {
  return (
    <li className="grid grid-cols-[9rem_7rem_minmax(0,1fr)] items-start gap-4 px-4 py-3">
      <span className="text-sm font-medium">{row.label}</span>
      <StateBadge state={row.state} />
      <div className="min-w-0 space-y-1.5">
        <p className="text-sm">{row.summary}</p>
        {row.details.length > 0 && (
          <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-xs">
            {row.details.map(([label, value]) => (
              <Fragment key={label}>
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="font-mono break-all">{value}</dd>
              </Fragment>
            ))}
          </dl>
        )}
        {row.log.length > 0 && (
          <pre className="max-h-40 overflow-auto rounded-md bg-muted p-2 font-mono text-xs whitespace-pre-wrap">
            {row.log.join("\n")}
          </pre>
        )}
      </div>
    </li>
  );
}

export function DiagnosticsScreen() {
  const { report, worker, loading, restarting, error, refresh, restartWorker } =
    useDiagnosticsStore(
      useShallow((s) => ({
        report: s.report,
        worker: s.worker,
        loading: s.loading,
        restarting: s.restarting,
        error: s.error,
        refresh: s.refresh,
        restartWorker: s.restartWorker,
      })),
    );
  useWorkerStatusEvents();
  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <main className="mx-auto flex max-w-5xl flex-col gap-4 p-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold">System diagnostics</h1>
          <p className="text-sm text-muted-foreground">
            Temporary foundation screen. Every state is a live check; nothing here is simulated.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}>
            <RefreshCw aria-hidden="true" className={cn(loading && "motion-safe:animate-spin")} />
            {loading ? "Checking…" : "Refresh"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void restartWorker()}
            disabled={restarting}
          >
            <RotateCcw aria-hidden="true" />
            {restarting ? "Restarting…" : "Restart worker"}
          </Button>
        </div>
      </header>

      {error && (
        <div role="alert" className="rounded-md border border-danger/40 px-4 py-3 text-sm">
          <p className="font-medium">
            {error.code === "desktop_unavailable" ? "Desktop core not available" : "Request failed"}
          </p>
          <p className="text-muted-foreground">{error.message}</p>
        </div>
      )}

      {report ? (
        <section aria-label="Subsystem status" className="space-y-2">
          <p className="text-xs text-muted-foreground">
            Last checked {new Date(report.generated_at).toLocaleString()}
          </p>
          <ul className="divide-y divide-border rounded-md border bg-card">
            {toRows(report, worker).map((row) => (
              <Row key={row.id} row={row} />
            ))}
          </ul>
        </section>
      ) : (
        !error && <output className="text-sm text-muted-foreground">Checking the system…</output>
      )}
    </main>
  );
}
