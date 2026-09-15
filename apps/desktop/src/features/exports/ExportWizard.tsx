import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  ExplorerField,
  ExplorerQuery,
  ExportColumn,
  ExportDestination,
  ExportDraft,
  ExportFormat,
  ExportItem,
  ExportPreviewResult,
  JsonValue,
} from "@lead/shared-types";
import { toast } from "sonner";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { FIELD_KEYS, codeText } from "@/features/leads/labels";
import { useT } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { useLoaded } from "@/lib/useLoaded";

const POLL_MS = 1000;
const SELECT = "h-7 w-full rounded-md border bg-transparent px-2 text-xs";
const FINISHED: ReadonlySet<string> = new Set(["succeeded", "failed", "cancelled"]);

/** What an export reads: the leads an explorer query matches now, or a snapshot as it was. */
export type ExportSource =
  | { kind: "query"; query: ExplorerQuery; viewId: string | null; columns: readonly string[] }
  | { kind: "snapshot"; snapshotId: string };

interface Form {
  presetKey: string | null;
  format: ExportFormat;
  columns: ExportColumn[];
  bom: boolean;
}

interface ExportWizardProps {
  source: ExportSource;
  /** The file name the save dialog offers, without its extension. */
  suggestedName: string;
  onOpenChange: (open: boolean) => void;
}

const cellText = (value: JsonValue) =>
  value === null ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value);

/**
 * Writes an export to a file (phase 13): a preset, or columns chosen with their headers; a preview of
 * the rows exactly as written; the destination from the desktop core's own save dialog (the page never
 * names a path); then a streamed write with its progress, its SHA-256 and a manifest next to it.
 * Leads on the do-not-contact list stay out unless included on purpose, with a reason.
 */
export function ExportWizard({ source, suggestedName, onOpenChange }: ExportWizardProps) {
  const t = useT();
  const loadCatalog = useCallback(
    () => desktop.getExportCatalog({ include_superseded: false }),
    [],
  );
  const catalog = useLoaded(loadCatalog, "get_export_catalog");
  const [edited, setEdited] = useState<Form | null>(null);
  const [includeSuppressed, setIncludeSuppressed] = useState(false);
  const [suppressedReason, setSuppressedReason] = useState("");
  const [preview, setPreview] = useState<ExportPreviewResult | null>(null);
  const [destination, setDestination] = useState<ExportDestination | null>(null);
  const [item, setItem] = useState<ExportItem | null>(null);
  const [presetName, setPresetName] = useState("");
  const [busy, setBusy] = useState(false);

  const kind = source.kind;
  const queryColumns = source.kind === "query" ? source.columns : null;
  const offered = useMemo(
    () => (catalog.data?.columns ?? []).filter((column) => column.source_kind === kind),
    [catalog.data, kind],
  );
  const appended = useMemo(
    () => (catalog.data?.appended ?? []).filter((column) => column.source_kind === kind),
    [catalog.data, kind],
  );
  const presets = useMemo(
    () => (catalog.data?.presets ?? []).filter((view) => view.definition.source_kind === kind),
    [catalog.data, kind],
  );
  const header = useCallback(
    (key: string) =>
      kind === "query" && key in FIELD_KEYS ? t(FIELD_KEYS[key as ExplorerField]) : key,
    [kind, t],
  );

  // until something is edited: the explorer's visible columns the catalogue offers, else a preset
  const defaults = useMemo((): Form => {
    const keys = (queryColumns ?? []).filter((key) => offered.some((column) => column.key === key));
    if (keys.length > 0) {
      const columns = keys.map((key) => ({ key, header: header(key) }));
      return { presetKey: null, format: "csv", columns, bom: true };
    }
    const first = presets[0];
    if (!first) return { presetKey: null, format: "csv", columns: [], bom: true };
    const { format, columns, bom } = first.definition;
    return { presetKey: first.preset.key, format, columns, bom };
  }, [queryColumns, offered, presets, header]);
  const form = edited ?? defaults;
  const preset = presets.find((view) => view.preset.key === form.presetKey) ?? null;

  const columns = form.columns.map((column) => ({ key: column.key, header: column.header.trim() }));
  const draft: ExportDraft = {
    source_kind: kind,
    query: source.kind === "query" ? source.query : null,
    view_id: source.kind === "query" ? source.viewId : null,
    snapshot_id: source.kind === "snapshot" ? source.snapshotId : null,
    format: form.format,
    columns,
    bom: form.format === "csv" && form.bom,
    include_suppressed: includeSuppressed,
    suppressed_reason: includeSuppressed ? suppressedReason.trim() : null,
    preset_key: preset?.preset.key ?? null,
    preset_version: preset?.preset.version ?? null,
  };
  const headers = columns.map((column) => column.header.toLowerCase());
  const problem =
    columns.length === 0
      ? "noColumns"
      : headers.includes("")
        ? "blankHeader"
        : new Set(headers).size !== headers.length
          ? "duplicateHeader"
          : includeSuppressed && !suppressedReason.trim()
            ? "reasonNeeded"
            : null;

  // any change of what is written asks for a new preview; another format, for a new destination
  const edit = (next: Partial<Form>) => {
    setEdited({ ...form, presetKey: null, ...next });
    setPreview(null);
    if (next.format !== undefined && next.format !== form.format) setDestination(null);
  };
  const choosePreset = (key: string) => {
    const view = presets.find((candidate) => candidate.preset.key === key);
    if (!view) {
      edit({});
      return;
    }
    const { format, columns: chosen, bom } = view.definition;
    edit({ presetKey: view.preset.key, format, columns: chosen, bom });
  };
  const toggle = (key: string, on: boolean) =>
    edit({
      columns: on
        ? [...form.columns, { key, header: header(key) }]
        : form.columns.filter((column) => column.key !== key),
    });
  const rename = (key: string, value: string) =>
    edit({
      columns: form.columns.map((column) =>
        column.key === key ? { ...column, header: value } : column,
      ),
    });

  const runPreview = async () => {
    setBusy(true);
    try {
      setPreview(await desktop.previewExport(draft));
    } catch (cause) {
      toast.error(t("exportWizard.previewFailed"), {
        description: toIpcError(cause, "preview_export").message,
      });
    } finally {
      setBusy(false);
    }
  };

  const choose = async () => {
    try {
      const chosen = await desktop.chooseExportDestination(form.format, suggestedName);
      if (chosen) setDestination(chosen);
    } catch (cause) {
      toast.error(t("exportWizard.destinationFailed"), {
        description: toIpcError(cause, "choose_export_destination").message,
      });
    }
  };

  const start = async () => {
    if (!destination) return;
    setBusy(true);
    try {
      setItem(await desktop.startExport(draft, destination.grant_id));
    } catch (cause) {
      toast.error(t("exportWizard.startFailed"), {
        description: toIpcError(cause, "start_export").message,
      });
    } finally {
      // a grant is used once, whatever the answer
      setDestination(null);
      setBusy(false);
    }
  };

  const cancel = async (exportId: string) => {
    setBusy(true);
    try {
      setItem(await desktop.cancelExport({ export_id: exportId }));
    } catch (cause) {
      toast.error(t("exportWizard.cancelFailed"), {
        description: toIpcError(cause, "cancel_export").message,
      });
    } finally {
      setBusy(false);
    }
  };

  const savePreset = async () => {
    setBusy(true);
    try {
      await desktop.saveExportPreset({
        key: preset && !preset.preset.built_in ? preset.preset.key : null,
        clone_from: preset?.preset.built_in ? preset.preset.key : null,
        name: presetName.trim(),
        description: null,
        definition: { source_kind: kind, format: form.format, columns, bom: draft.bom },
      });
      toast.success(t("exportWizard.presetSaved"));
      setPresetName("");
      await catalog.reload();
    } catch (cause) {
      toast.error(t("exportWizard.presetFailed"), {
        description: toIpcError(cause, "save_export_preset").message,
      });
    } finally {
      setBusy(false);
    }
  };

  const exportId = item?.export.id ?? null;
  const projectId = item?.export.project_id ?? null;
  const writing = item !== null && !FINISHED.has(item.export.status);
  useEffect(() => {
    if (!writing || exportId === null || projectId === null) return;
    const timer = setInterval(() => {
      void desktop.listExports({ project_id: projectId, limit: 25, cursor: null }).then(
        (page) => {
          const found = page.items.find((entry) => entry.export.id === exportId);
          if (!found) return;
          setItem(found);
          const { status, row_count, file_name, error_message, error_code } = found.export;
          if (status === "succeeded") {
            toast.success(t("exportWizard.done", { rows: row_count ?? 0, file: file_name ?? "" }));
          } else if (status === "failed") {
            toast.error(t("exportWizard.failed"), {
              description: error_message ?? error_code ?? "",
            });
          }
        },
        () => undefined,
      );
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [writing, exportId, projectId, t]);

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-auto sm:max-w-5xl">
        <DialogHeader>
          <DialogTitle>{t("exportWizard.title")}</DialogTitle>
          <DialogDescription>
            {t(kind === "query" ? "exportWizard.fromQuery" : "exportWizard.fromSnapshot")}
          </DialogDescription>
        </DialogHeader>
        {catalog.error ? (
          <StateBlock
            variant={catalog.error.code === "desktop_unavailable" ? "offline" : "error"}
            title={t("exportWizard.catalogFailed")}
            body={catalog.error.message}
          />
        ) : catalog.data === null ? (
          <StateBlock variant="loading" title={t("common.loading")} />
        ) : item ? (
          <div className="space-y-2 text-xs" role="status">
            <p className="font-medium">
              {`${t(`exports.status.${item.export.status}`)} · ${item.export.file_name ?? ""}`}
            </p>
            <progress
              aria-label={t("exportWizard.progress")}
              max={item.rows_total ?? undefined}
              value={item.rows_total === null ? undefined : item.rows_written}
              className="h-1.5 w-full"
            />
            <p>
              {t("exportWizard.rows", {
                written: item.export.row_count ?? item.rows_written,
                total: item.rows_total ?? "?",
              })}
            </p>
            {item.export.file_sha256 && (
              <p className="font-mono break-all">{`SHA-256 ${item.export.file_sha256}`}</p>
            )}
            {item.export.status === "succeeded" && (
              <p className="text-muted-foreground">{t("exportWizard.manifest")}</p>
            )}
            {item.export.error_message && (
              <InlineNotice variant="error">{item.export.error_message}</InlineNotice>
            )}
            {writing && (
              <Button
                size="sm"
                variant="outline"
                className="h-7"
                disabled={busy}
                onClick={() => void cancel(item.export.id)}
              >
                {t("exportWizard.cancel")}
              </Button>
            )}
          </div>
        ) : (
          <div className="grid gap-4 text-xs md:grid-cols-2">
            <div className="space-y-2">
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-0.5">
                  <Label htmlFor="export-preset" className="text-xs">
                    {t("exportWizard.preset")}
                  </Label>
                  <select
                    id="export-preset"
                    className={SELECT}
                    value={form.presetKey ?? ""}
                    onChange={(event) => choosePreset(event.target.value)}
                  >
                    <option value="">{t("exportWizard.custom")}</option>
                    {presets.map((view) => (
                      <option key={view.preset.key} value={view.preset.key}>
                        {`${view.preset.name} v${view.preset.version}${view.preset.built_in ? ` (${t("exportWizard.builtIn")})` : ""}`}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="space-y-0.5">
                  <Label htmlFor="export-format" className="text-xs">
                    {t("exportWizard.format")}
                  </Label>
                  <select
                    id="export-format"
                    className={SELECT}
                    value={form.format}
                    onChange={(event) => edit({ format: event.target.value as ExportFormat })}
                  >
                    {catalog.data.formats.map((format) => (
                      <option key={format} value={format}>
                        {t(`exportWizard.format.${format}`)}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              {form.format === "csv" && (
                <label className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={form.bom}
                    onChange={(event) => edit({ bom: event.target.checked })}
                  />
                  {t("exportWizard.bom")}
                </label>
              )}
              <fieldset className="space-y-1">
                <legend className="font-medium">{t("exportWizard.columns")}</legend>
                <ul className="max-h-72 space-y-0.5 overflow-auto rounded-md border p-1">
                  {offered.map((column) => {
                    const chosen = form.columns.find((entry) => entry.key === column.key);
                    return (
                      <li key={column.key} className="flex items-center gap-1.5">
                        <label
                          className="flex min-w-0 flex-1 items-center gap-1.5"
                          title={column.description}
                        >
                          <input
                            type="checkbox"
                            checked={chosen !== undefined}
                            onChange={(event) => toggle(column.key, event.target.checked)}
                          />
                          <span className="truncate font-mono">{column.key}</span>
                        </label>
                        {chosen && (
                          <Input
                            aria-label={t("exportWizard.header", { column: column.key })}
                            value={chosen.header}
                            maxLength={64}
                            onChange={(event) => rename(column.key, event.target.value)}
                            className="h-6 w-40 text-xs"
                          />
                        )}
                      </li>
                    );
                  })}
                </ul>
                <p className="text-[11px] text-muted-foreground">
                  {t("exportWizard.appended", {
                    columns: appended.map((column) => column.key).join(", "),
                  })}
                </p>
              </fieldset>
              <div className="flex items-end gap-1">
                <div className="flex-1 space-y-0.5">
                  <Label htmlFor="export-preset-name" className="text-[11px]">
                    {t("exportWizard.presetName")}
                  </Label>
                  <Input
                    id="export-preset-name"
                    value={presetName}
                    maxLength={120}
                    onChange={(event) => setPresetName(event.target.value)}
                    className="h-7 text-xs"
                  />
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7"
                  disabled={
                    busy || !presetName.trim() || (problem !== null && problem !== "reasonNeeded")
                  }
                  onClick={() => void savePreset()}
                >
                  {t("exportWizard.savePreset")}
                </Button>
              </div>
            </div>
            <div className="space-y-2">
              <label className="flex items-center gap-1.5">
                <input
                  type="checkbox"
                  checked={includeSuppressed}
                  onChange={(event) => {
                    setIncludeSuppressed(event.target.checked);
                    setPreview(null);
                  }}
                />
                {t("exportWizard.includeSuppressed")}
              </label>
              {includeSuppressed && (
                <div className="space-y-0.5">
                  <Label htmlFor="export-suppressed-reason" className="text-xs">
                    {t("exportWizard.suppressedReason")}
                  </Label>
                  <Textarea
                    id="export-suppressed-reason"
                    value={suppressedReason}
                    maxLength={2000}
                    onChange={(event) => {
                      setSuppressedReason(event.target.value);
                      setPreview(null);
                    }}
                    className="min-h-12 text-xs"
                  />
                </div>
              )}
              {problem && (
                <InlineNotice variant="blocked">{t(`exportWizard.${problem}`)}</InlineNotice>
              )}
              <Button
                variant="outline"
                size="sm"
                className="h-7"
                disabled={busy || problem !== null}
                onClick={() => void runPreview()}
              >
                {t("exportWizard.preview")}
              </Button>
              {preview && (
                <div className="space-y-1" role="status">
                  <p className="font-medium">
                    {t("exportWizard.counts", {
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
                    {t("exportWizard.sample", { n: preview.sample.length })}
                  </p>
                  <div className="max-h-56 overflow-auto rounded-md border">
                    <table className="w-full text-[11px]">
                      <thead>
                        <tr>
                          {preview.headers.map((name) => (
                            <th
                              key={name}
                              scope="col"
                              className="px-1.5 py-1 text-left font-medium whitespace-nowrap text-muted-foreground"
                            >
                              {name}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {preview.sample.map((row, index) => (
                          <tr key={`${index}-${cellText(row[0] ?? null)}`} className="border-t">
                            {row.map((cell, position) => (
                              <td
                                key={preview.headers[position] ?? position}
                                className="max-w-48 truncate px-1.5 py-0.5"
                              >
                                {cellText(cell)}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
              <div className="space-y-1 border-t pt-2">
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7"
                  disabled={busy || preview === null}
                  onClick={() => void choose()}
                >
                  {t("exportWizard.choose")}
                </Button>
                {destination && (
                  <p className="break-all">
                    {t("exportWizard.destination", {
                      file: destination.file_name,
                      folder: destination.folder,
                    })}
                  </p>
                )}
                <Button
                  size="sm"
                  className="h-7"
                  disabled={busy || preview === null || destination === null || problem !== null}
                  onClick={() => void start()}
                >
                  {t("exportWizard.start")}
                </Button>
              </div>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
