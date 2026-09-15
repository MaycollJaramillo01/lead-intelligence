import { useCallback, useState } from "react";
import type { ExplorerField, ExplorerQuery } from "@lead/shared-types";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { FIELD_KEYS, cellText, codeText } from "@/features/leads/labels";
import { useT } from "@/lib/i18n";
import { desktop } from "@/lib/ipc";
import { useLoaded } from "@/lib/useLoaded";

const SAMPLE_SIZE = 20;

interface ExportPreviewDialogProps {
  onOpenChange: (open: boolean) => void;
  query: ExplorerQuery;
  /** The visible columns, in order; memoised by the caller. */
  columns: readonly ExplorerField[];
  /** Opens the export wizard that writes the file (phase 13). */
  onExportToFile?: () => void;
}

/**
 * What an export of the explorer's query would hold (phase 12): the counts, the warnings and a
 * sample, before anything is written. Leads on the do-not-contact list stay out unless the analyst
 * includes them, and the preview says so.
 */
export function ExportPreviewDialog({
  onOpenChange,
  query,
  columns,
  onExportToFile,
}: ExportPreviewDialogProps) {
  const t = useT();
  const [includeSuppressed, setIncludeSuppressed] = useState(false);
  const load = useCallback(
    () =>
      desktop.previewLeadExport({
        query,
        columns: [...columns],
        include_suppressed: includeSuppressed,
        sample_size: SAMPLE_SIZE,
      }),
    [query, columns, includeSuppressed],
  );
  const { data: preview, error } = useLoaded(load, "preview_lead_export");

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>{t("export.title")}</DialogTitle>
          <DialogDescription>{t("export.help")}</DialogDescription>
        </DialogHeader>
        <div className="space-y-2 text-xs">
          <div className="flex items-center justify-between gap-2">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={includeSuppressed}
                onChange={(event) => setIncludeSuppressed(event.target.checked)}
              />
              {t("export.includeSuppressed")}
            </label>
            {onExportToFile && (
              <Button size="sm" className="h-7" onClick={onExportToFile}>
                {t("export.toFile")}
              </Button>
            )}
          </div>
          {error ? (
            <StateBlock
              variant={error.code === "desktop_unavailable" ? "offline" : "error"}
              title={t("export.failed")}
              body={error.message}
            />
          ) : preview === null ? (
            <StateBlock variant="loading" title={t("state.loading")} />
          ) : (
            <>
              <p className="font-medium" role="status">
                {t("export.counts", {
                  matching: preview.matching,
                  excluded: preview.excluded_suppressed,
                  exported: preview.exported,
                })}
              </p>
              {preview.warnings.map((warning) => (
                <InlineNotice
                  key={warning}
                  variant={warning === "suppressed_included" ? "blocked" : "partial"}
                >
                  {codeText(t, "export.warning", warning)}
                </InlineNotice>
              ))}
              <p className="text-[11px] text-muted-foreground">
                {t("export.sample", { n: preview.sample.length })}
              </p>
              <div className="overflow-auto rounded-md border">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr>
                      {preview.columns.map((field) => (
                        <th
                          key={field}
                          scope="col"
                          className="px-1.5 py-1 text-left font-medium whitespace-nowrap text-muted-foreground"
                        >
                          {t(FIELD_KEYS[field])}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {preview.sample.map((row) => (
                      <tr key={row.business_id} className="border-t">
                        {preview.columns.map((field) => (
                          <td key={field} className="max-w-56 truncate px-1.5 py-0.5">
                            {cellText(t, row, field) ?? "—"}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
