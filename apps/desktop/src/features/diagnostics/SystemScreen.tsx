import { Fragment } from "react";
import { RefreshCw, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { PageActions, PageScroll, PageShell } from "@/components/layout/page-chrome";
import { InlineNotice } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { StateBadge } from "@/features/diagnostics/StateBadge";
import { toRows, type DiagnosticRow } from "@/features/diagnostics/model";
import { useDiagnosticsStore } from "@/features/diagnostics/store";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

function Row({ row }: { row: DiagnosticRow }) {
  return (
    <li className="grid grid-cols-[8rem_7rem_minmax(0,1fr)] items-start gap-4 px-4 py-3">
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

export function SystemScreen() {
  const t = useT();
  const language = useLanguage();
  const report = useDiagnosticsStore((state) => state.report);
  const worker = useDiagnosticsStore((state) => state.worker);
  const loading = useDiagnosticsStore((state) => state.loading);
  const restarting = useDiagnosticsStore((state) => state.restarting);
  const error = useDiagnosticsStore((state) => state.error);
  const refresh = useDiagnosticsStore((state) => state.refresh);
  const restartWorker = useDiagnosticsStore((state) => state.restartWorker);

  const onRestart = async () => {
    await restartWorker();
    const failure = useDiagnosticsStore.getState().error;
    if (failure) {
      toast.error(t("toast.actionFailed", { action: t("nav.system") }), {
        description: failure.message,
      });
    } else {
      toast.success(t("toast.workerRestarted"));
    }
  };

  return (
    <PageShell>
      <PageActions>
        <Button
          variant="outline"
          size="sm"
          className="h-7"
          onClick={() => void refresh()}
          disabled={loading}
        >
          <RefreshCw aria-hidden="true" className={cn(loading && "motion-safe:animate-spin")} />
          {loading ? t("state.loading") : t("common.refresh")}
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-7"
          onClick={() => void onRestart()}
          disabled={restarting}
        >
          <RotateCcw aria-hidden="true" />
          {restarting ? t("state.processing") : t("common.restartWorker")}
        </Button>
      </PageActions>

      <PageScroll>
        <p className="text-xs text-muted-foreground">{t("system.subtitle")}</p>

        {error && (
          <InlineNotice variant={error.code === "desktop_unavailable" ? "offline" : "error"}>
            <strong className="font-medium">
              {error.code === "desktop_unavailable" ? t("state.offline") : t("state.error")}
            </strong>{" "}
            {error.message}
          </InlineNotice>
        )}

        {report ? (
          <>
            <p className="text-[11px] text-muted-foreground">
              {/* only call it stale when the latest check actually failed */}
              {t(error ? "state.stale" : "common.lastChecked", {
                time: formatDateTime(report.generated_at, language),
              })}
            </p>
            <ul className="divide-y divide-border rounded-md border bg-card">
              {toRows(report, worker).map((row) => (
                <Row key={row.id} row={row} />
              ))}
            </ul>
          </>
        ) : (
          !error && <output className="text-sm text-muted-foreground">{t("state.loading")}</output>
        )}
      </PageScroll>
    </PageShell>
  );
}
