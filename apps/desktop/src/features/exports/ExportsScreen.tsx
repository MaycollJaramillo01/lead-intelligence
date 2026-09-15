import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import type { ExportItem } from "@lead/shared-types";
import { toast } from "sonner";
import { DateTime, TestDataBadge } from "@/components/data/cells";
import { PagedFooter } from "@/components/data/PagedFooter";
import { PageScroll, PageShell, Section } from "@/components/layout/page-chrome";
import { StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { snapshotPath } from "@/lib/links";
import { useKeysetPages } from "@/lib/useKeysetPages";
import { useLoaded } from "@/lib/useLoaded";

const PAGE_SIZE = 50;
const POLL_MS = 2000;

const writing = (item: ExportItem) =>
  item.export.status === "pending" || item.export.status === "running";

/**
 * Export history and presets (phase 13). Exports start where their rows are: the Lead Explorer for a
 * query, a dataset snapshot for a frozen version.
 */
export function ExportsScreen() {
  const t = useT();
  const fetchPage = useCallback(
    (cursor: string | null) => desktop.listExports({ project_id: null, limit: PAGE_SIZE, cursor }),
    [],
  );
  const pages = useKeysetPages(fetchPage, "list_exports");
  const loadCatalog = useCallback(() => desktop.getExportCatalog({ include_superseded: true }), []);
  const catalog = useLoaded(loadCatalog, "get_export_catalog");
  const [busy, setBusy] = useState(false);

  const { reload } = pages;
  const active = pages.items.some(writing);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => void reload(), POLL_MS);
    return () => clearInterval(timer);
  }, [active, reload]);

  const cancel = async (item: ExportItem) => {
    setBusy(true);
    try {
      await desktop.cancelExport({ export_id: item.export.id });
      await reload();
    } catch (cause) {
      toast.error(t("exportWizard.cancelFailed"), {
        description: toIpcError(cause, "cancel_export").message,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <PageShell>
      <PageScroll>
        <Section title={t("exports.historyTitle")} description={t("exports.historyHelp")}>
          {pages.error ? (
            <StateBlock
              variant={pages.error.code === "desktop_unavailable" ? "offline" : "error"}
              title={t("exports.errorTitle")}
              body={pages.error.message}
              action={
                <Button variant="outline" size="sm" onClick={() => void reload()}>
                  {t("common.retry")}
                </Button>
              }
            />
          ) : pages.loading && pages.items.length === 0 ? (
            <StateBlock variant="loading" title={t("common.loading")} />
          ) : pages.items.length === 0 ? (
            <StateBlock
              variant="empty"
              title={t("exports.emptyTitle")}
              body={t("exports.emptyBody")}
            />
          ) : (
            <div className="space-y-2">
              <div className="overflow-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-muted-foreground">
                      {(
                        [
                          "exports.column.requested",
                          "exports.column.source",
                          "exports.column.file",
                          "exports.column.status",
                          "exports.column.rows",
                          "exports.column.sha",
                          "exports.column.preset",
                        ] as const
                      ).map((key) => (
                        <th
                          key={key}
                          scope="col"
                          className="py-1 pr-3 font-medium whitespace-nowrap"
                        >
                          {t(key)}
                        </th>
                      ))}
                      <th scope="col">
                        <span className="sr-only">{t("exports.column.actions")}</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {pages.items.map((item) => {
                      const record = item.export;
                      return (
                        <tr key={record.id} className="border-t align-top">
                          <td className="py-1 pr-3 whitespace-nowrap">
                            <DateTime value={record.requested_at} />
                          </td>
                          <td className="pr-3 whitespace-nowrap">
                            {record.dataset_snapshot_id ? (
                              <Link
                                to={snapshotPath(record.dataset_snapshot_id)}
                                className="text-primary hover:underline"
                              >
                                {t("exports.fromSnapshot")}
                              </Link>
                            ) : (
                              t("exports.fromQuery")
                            )}
                            {item.is_synthetic && (
                              <span className="ml-1.5">
                                <TestDataBadge />
                              </span>
                            )}
                          </td>
                          <td className="pr-3">
                            {`${record.file_name ?? "—"} · ${t(`exportWizard.format.${record.format}`)}`}
                          </td>
                          <td className="pr-3">
                            <span>{t(`exports.status.${record.status}`)}</span>
                            {record.error_message && (
                              <span className="block text-[11px] text-danger">
                                {record.error_message}
                              </span>
                            )}
                          </td>
                          <td className="pr-3 font-mono whitespace-nowrap">
                            {record.row_count ?? `${item.rows_written}/${item.rows_total ?? "?"}`}
                          </td>
                          <td className="pr-3 font-mono" title={record.file_sha256 ?? undefined}>
                            {record.file_sha256 ? record.file_sha256.slice(0, 12) : "—"}
                          </td>
                          <td className="pr-3 whitespace-nowrap">
                            {record.preset_key
                              ? `${record.preset_key} v${record.preset_version}`
                              : "—"}
                          </td>
                          <td>
                            {writing(item) && (
                              <Button
                                size="sm"
                                variant="outline"
                                className="h-6 text-[11px]"
                                disabled={busy}
                                onClick={() => void cancel(item)}
                              >
                                {t("exportWizard.cancel")}
                              </Button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <PagedFooter
                loaded={pages.items.length}
                total={pages.total}
                hasMore={pages.nextCursor !== null}
                loading={pages.loading}
                onLoadMore={() => void pages.loadMore()}
              />
            </div>
          )}
        </Section>
        <Section title={t("exports.presetsTitle")} description={t("exports.presetsHelp")}>
          {catalog.error ? (
            <StateBlock
              variant="error"
              title={t("exportWizard.catalogFailed")}
              body={catalog.error.message}
            />
          ) : catalog.data === null ? (
            <StateBlock variant="loading" title={t("common.loading")} />
          ) : (
            <ul className="space-y-2 text-xs">
              {catalog.data.presets.map((view) => (
                <li key={view.preset.id} className="rounded-md border p-2">
                  <p className="font-medium">
                    {`${view.preset.name} · ${view.preset.key} v${view.preset.version}`}
                    {view.preset.built_in && (
                      <span className="ml-1.5 text-muted-foreground">
                        {t("exportWizard.builtIn")}
                      </span>
                    )}
                    {view.preset.superseded_at && (
                      <span className="ml-1.5 text-muted-foreground">
                        {t("exports.superseded")}
                      </span>
                    )}
                  </p>
                  <p className="text-[11px] text-muted-foreground">
                    {t("exports.presetLine", {
                      source: t(`exports.sourceKind.${view.definition.source_kind}`),
                      format: t(`exportWizard.format.${view.definition.format}`),
                      columns: view.definition.columns.map((column) => column.header).join(", "),
                    })}
                  </p>
                  {view.preset.description && <p>{view.preset.description}</p>}
                </li>
              ))}
            </ul>
          )}
        </Section>
      </PageScroll>
    </PageShell>
  );
}
