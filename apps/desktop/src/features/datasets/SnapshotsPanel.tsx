import { useCallback, useState } from "react";
import type {
  BulkMode,
  DatasetCleanupResult,
  DatasetDiff,
  DatasetListItem,
  DatasetSnapshotDetail,
} from "@lead/shared-types";
import { toast } from "sonner";
import { DateTime, TestDataBadge } from "@/components/data/cells";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ExportWizard } from "@/features/exports/ExportWizard";
import { codeText } from "@/features/leads/labels";
import { useT } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { useLoaded } from "@/lib/useLoaded";
import { cn } from "@/lib/utils";

const LISTED = 50;
const DIFF_ROWS = 100;

const size = (bytes: number) =>
  bytes >= 1024 * 1024
    ? `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
    : `${Math.ceil(bytes / 1024)} KiB`;
const band = (index: number) => `${index * 10}–${index === 9 ? 100 : index * 10 + 9}`;

interface SnapshotsPanelProps {
  dataset: DatasetListItem;
  /** The snapshot a link points at. */
  linkedSnapshot: string | null;
  onChanged: () => void;
}

/**
 * The versions of a dataset (phase 13): each snapshot with its measured quality report, its lineage
 * and its exports; two versions compared row by row; old versions retained, or deleted with a reason.
 */
export function SnapshotsPanel({ dataset, linkedSnapshot, onChanged }: SnapshotsPanelProps) {
  const t = useT();
  const datasetId = dataset.dataset.id;
  const loadList = useCallback(
    () => desktop.listDatasetSnapshots({ dataset_id: datasetId, limit: LISTED, cursor: null }),
    [datasetId],
  );
  const list = useLoaded(loadList, "list_dataset_snapshots");
  const items = list.data?.items ?? [];
  const [picked, setPicked] = useState<string | null>(linkedSnapshot);
  const selected = items.find((item) => item.snapshot.id === picked) ?? items[0] ?? null;
  const selectedId = selected?.snapshot.id ?? null;
  const previous = selected
    ? (items.find((item) => item.snapshot.version < selected.snapshot.version) ?? null)
    : null;
  const loadDetail = useCallback(
    (): Promise<DatasetSnapshotDetail | null> =>
      selectedId === null
        ? Promise.resolve(null)
        : desktop.getDatasetSnapshot({ snapshot_id: selectedId }),
    [selectedId],
  );
  const detail = useLoaded(loadDetail, "get_dataset_snapshot");
  const shown = detail.data && detail.data.item.snapshot.id === selectedId ? detail.data : null;
  const [diff, setDiff] = useState<DatasetDiff | null>(null);
  const [keepLatest, setKeepLatest] = useState(3);
  const [reason, setReason] = useState("");
  const [retention, setRetention] = useState<DatasetCleanupResult | null>(null);
  const [exporting, setExporting] = useState(false);
  const [busy, setBusy] = useState(false);
  const keepValid = Number.isInteger(keepLatest) && keepLatest >= 1 && keepLatest <= 100;

  const capture = async () => {
    setBusy(true);
    try {
      const created = await desktop.createDatasetSnapshot({
        project_id: dataset.dataset.project_id,
        dataset_name: dataset.dataset.name,
        description: null,
        score_run_id: null,
      });
      toast.success(
        t("snapshot.created", {
          version: created.item.snapshot.version,
          rows: created.item.snapshot.row_count,
        }),
      );
      setPicked(created.item.snapshot.id);
      setDiff(null);
      setRetention(null);
      await list.reload();
      onChanged();
    } catch (cause) {
      toast.error(t("snapshot.createFailed"), {
        description: toIpcError(cause, "create_dataset_snapshot").message,
      });
    } finally {
      setBusy(false);
    }
  };

  const compare = async () => {
    if (!selected || !previous) return;
    setBusy(true);
    try {
      setDiff(
        await desktop.diffDatasetSnapshots({
          before_snapshot_id: previous.snapshot.id,
          after_snapshot_id: selected.snapshot.id,
          limit: DIFF_ROWS,
        }),
      );
    } catch (cause) {
      toast.error(t("snapshot.compareFailed"), {
        description: toIpcError(cause, "diff_dataset_snapshots").message,
      });
    } finally {
      setBusy(false);
    }
  };

  const retain = async (mode: BulkMode) => {
    setBusy(true);
    try {
      const result = await desktop.cleanupDataset({
        dataset_id: datasetId,
        keep_latest: keepLatest,
        mode,
        reason: mode === "apply" ? reason.trim() : null,
      });
      setRetention(result);
      if (mode === "apply") {
        toast.success(t("snapshot.retentionApplied", { n: result.deleted.length }));
        setReason("");
        setPicked(null);
        await list.reload();
        onChanged();
      }
    } catch (cause) {
      toast.error(t("snapshot.retentionFailed"), {
        description: toIpcError(cause, "cleanup_dataset").message,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-3 p-3 text-xs lg:grid-cols-[18rem_1fr]">
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <h2 className="flex min-w-0 items-center gap-1.5 text-sm font-medium">
            <span className="truncate">{t("snapshot.title", { name: dataset.dataset.name })}</span>
            {dataset.is_synthetic && <TestDataBadge />}
          </h2>
          <Button size="sm" className="h-7" disabled={busy} onClick={() => void capture()}>
            {t("snapshot.new")}
          </Button>
        </div>
        {list.error ? (
          <StateBlock variant="error" title={t("snapshot.errorTitle")} body={list.error.message} />
        ) : list.data === null ? (
          <StateBlock variant="loading" title={t("common.loading")} />
        ) : items.length === 0 ? (
          <StateBlock variant="empty" title={t("snapshot.empty")} />
        ) : (
          <ul className="space-y-1">
            {items.map((item) => (
              <li key={item.snapshot.id}>
                <button
                  type="button"
                  aria-pressed={item.snapshot.id === selectedId}
                  onClick={() => {
                    setPicked(item.snapshot.id);
                    setDiff(null);
                  }}
                  className={cn(
                    "w-full rounded-md border px-2 py-1 text-left hover:bg-accent/60",
                    item.snapshot.id === selectedId && "border-primary bg-accent",
                  )}
                >
                  <span className="font-medium">
                    {t("snapshot.version", { version: item.snapshot.version })}
                  </span>
                  <span className="block text-[11px] text-muted-foreground">
                    {t("snapshot.listLine", {
                      rows: item.snapshot.row_count,
                      schema: item.snapshot.schema_version,
                    })}{" "}
                    · <DateTime value={item.snapshot.created_at} />
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
        <section className="space-y-1 rounded-md border p-2" aria-label={t("snapshot.retention")}>
          <h3 className="font-medium">{t("snapshot.retention")}</h3>
          <Label htmlFor="retention-keep" className="text-[11px]">
            {t("snapshot.keepLatest")}
          </Label>
          <Input
            id="retention-keep"
            type="number"
            min={1}
            max={100}
            value={keepLatest}
            onChange={(event) => {
              setKeepLatest(Number(event.target.value));
              setRetention(null);
            }}
            className="h-7 text-xs"
          />
          <Label htmlFor="retention-reason" className="text-[11px]">
            {t("snapshot.reason")}
          </Label>
          <Textarea
            id="retention-reason"
            value={reason}
            maxLength={2000}
            onChange={(event) => setReason(event.target.value)}
            className="min-h-10 text-xs"
          />
          {retention && (
            <div role="status" className="space-y-0.5">
              <p className="font-medium">
                {t("snapshot.retentionLine", {
                  deleted: retention.deleted.length,
                  kept: retention.kept.length,
                  bytes: size(retention.freed_bytes),
                })}
              </p>
              <ul className="text-[11px] text-muted-foreground">
                {retention.kept.map((entry) => (
                  <li key={entry.snapshot_id}>
                    {`${t("snapshot.version", { version: entry.version })} · ${codeText(t, "snapshot.kept", entry.kept_because ?? "")}`}
                  </li>
                ))}
                {retention.deleted.map((entry) => (
                  <li key={entry.snapshot_id} className="text-danger">
                    {`${t("snapshot.version", { version: entry.version })} · ${size(entry.bytes)}`}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="flex justify-end gap-1">
            <Button
              variant="outline"
              size="sm"
              className="h-7"
              disabled={busy || !keepValid}
              onClick={() => void retain("preview")}
            >
              {t("snapshot.previewRetention")}
            </Button>
            <Button
              size="sm"
              className="h-7"
              disabled={
                busy ||
                !keepValid ||
                retention?.mode !== "preview" ||
                retention.deleted.length === 0 ||
                !reason.trim()
              }
              onClick={() => void retain("apply")}
            >
              {t("snapshot.applyRetention")}
            </Button>
          </div>
        </section>
      </div>
      <div className="min-w-0 space-y-3">
        {detail.error && !shown ? (
          <StateBlock
            variant="error"
            title={t("snapshot.detailFailed")}
            body={detail.error.message}
          />
        ) : selected === null ? null : shown === null ? (
          <StateBlock variant="loading" title={t("common.loading")} />
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-sm font-medium">
                {t("snapshot.version", { version: shown.item.snapshot.version })}
              </h3>
              <span className="text-muted-foreground">
                {t("snapshot.cutoff")}{" "}
                <DateTime value={shown.item.snapshot.cutoff_at ?? shown.item.snapshot.created_at} />
              </span>
              <Button
                variant="outline"
                size="sm"
                className="h-7"
                disabled={busy || previous === null}
                onClick={() => void compare()}
              >
                {previous
                  ? t("snapshot.compare", { version: previous.snapshot.version })
                  : t("snapshot.noPrevious")}
              </Button>
              <Button size="sm" className="h-7" onClick={() => setExporting(true)}>
                {t("snapshot.export")}
              </Button>
            </div>
            {diff && diff.after.snapshot.id === shown.item.snapshot.id && <DiffView diff={diff} />}
            <QualityReport detail={shown} />
            <section className="space-y-1" aria-label={t("snapshot.lineage")}>
              <h4 className="font-medium">{t("snapshot.lineage")}</h4>
              {shown.lineage.length === 0 ? (
                <p className="text-muted-foreground">{t("snapshot.noLineage")}</p>
              ) : (
                <ul className="space-y-0.5 text-[11px]">
                  {shown.lineage.map((entry) => (
                    <li key={`${entry.kind}-${entry.id}`}>
                      {`${t(`snapshot.lineage.${entry.kind}`)} · ${entry.label}`}
                      {entry.at && (
                        <>
                          {" · "}
                          <DateTime value={entry.at} />
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <section className="space-y-1" aria-label={t("snapshot.exports")}>
              <h4 className="font-medium">{t("snapshot.exports")}</h4>
              {shown.exports.length === 0 ? (
                <p className="text-muted-foreground">{t("snapshot.noExports")}</p>
              ) : (
                <ul className="space-y-0.5 text-[11px]">
                  {shown.exports.map((record) => (
                    <li key={record.id}>
                      {`${record.file_name ?? "—"} · ${t(`exports.status.${record.status}`)} · ${record.row_count ?? "—"} · `}
                      <DateTime value={record.requested_at} />
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </div>
      {exporting && selected && (
        <ExportWizard
          source={{ kind: "snapshot", snapshotId: selected.snapshot.id }}
          suggestedName={`${dataset.dataset.name} v${selected.snapshot.version}`}
          onOpenChange={(open) => {
            if (!open) setExporting(false);
          }}
        />
      )}
    </div>
  );
}

function QualityReport({ detail }: { detail: DatasetSnapshotDetail }) {
  const t = useT();
  const { quality } = detail;
  const figures: [string, number][] = [
    [t("snapshot.rows"), quality.rows],
    [t("snapshot.duplicates"), quality.duplicates],
    [t("snapshot.suppressed"), quality.suppressed],
    [t("snapshot.unscored"), quality.unscored],
    [t("snapshot.stale"), quality.stale_rows],
  ];
  return (
    <section className="space-y-2" aria-label={t("snapshot.quality")}>
      <h4 className="font-medium">{t("snapshot.quality")}</h4>
      {quality.errors.map((code) => (
        <InlineNotice key={code} variant="partial">
          {codeText(t, "snapshot.qualityError", code)}
        </InlineNotice>
      ))}
      <dl className="flex flex-wrap gap-x-4 gap-y-1">
        {figures.map(([label, value]) => (
          <div key={label} className="flex gap-1">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="font-mono">{value}</dd>
          </div>
        ))}
        {quality.oldest_seen_at && quality.newest_seen_at && (
          <div className="flex gap-1">
            <dt className="text-muted-foreground">{t("snapshot.seen")}</dt>
            <dd>
              <DateTime value={quality.oldest_seen_at} /> –{" "}
              <DateTime value={quality.newest_seen_at} />
            </dd>
          </div>
        )}
      </dl>
      <div className="grid gap-3 md:grid-cols-3">
        <table className="text-[11px]">
          <caption className="text-left font-medium">{t("snapshot.coverage")}</caption>
          <thead>
            <tr className="text-left text-muted-foreground">
              <th scope="col" className="font-medium">
                {t("snapshot.field")}
              </th>
              <th scope="col" className="font-medium">
                {t("snapshot.known")}
              </th>
            </tr>
          </thead>
          <tbody>
            {quality.coverage.map((entry) => (
              <tr key={entry.field} className="border-t">
                <td className="pr-2 font-mono">{entry.field}</td>
                <td className="font-mono">{`${entry.known} (${Math.round(entry.share * 100)}%)`}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div>
          <p className="font-medium">{t("snapshot.tiers")}</p>
          <ul className="text-[11px]">
            {quality.tiers.map((tier) => (
              <li key={tier.value}>{`${tier.value} · ${tier.count}`}</li>
            ))}
          </ul>
        </div>
        <div>
          <p className="font-medium">{t("snapshot.scoreBands")}</p>
          <ul className="font-mono text-[11px]">
            {quality.score_bins.map((count, index) => (
              <li key={band(index)}>{`${band(index)} · ${count}`}</li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}

function DiffView({ diff }: { diff: DatasetDiff }) {
  const t = useT();
  return (
    <section
      role="status"
      aria-label={t("snapshot.diff")}
      className="space-y-1 rounded-md border p-2"
    >
      <p className="font-medium">
        {t("snapshot.diffLine", {
          added: diff.added_count,
          removed: diff.removed_count,
          changed: diff.changed_count,
          unchanged: diff.unchanged_count,
        })}
      </p>
      {diff.truncated && (
        <InlineNotice variant="partial">{t("snapshot.diffTruncated")}</InlineNotice>
      )}
      <ul className="max-h-48 space-y-0.5 overflow-auto text-[11px]">
        {diff.added.map((row) => (
          <li key={`added-${row.business_id}`}>{`${t("snapshot.added")} · ${row.display_name}`}</li>
        ))}
        {diff.removed.map((row) => (
          <li key={`removed-${row.business_id}`} className="text-danger">
            {`${t("snapshot.removed")} · ${row.display_name}`}
          </li>
        ))}
        {diff.changed.map((row) => (
          <li key={`changed-${row.business_id}`}>
            {`${t("snapshot.changed")} · ${row.display_name} · ${row.fields.join(", ")}`}
          </li>
        ))}
      </ul>
    </section>
  );
}
