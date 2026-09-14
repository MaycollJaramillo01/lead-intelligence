import { useCallback, useEffect, useRef, useState } from "react";
import type { TimelineEntry, TimelineKind } from "@lead/shared-types";
import { Link } from "react-router";
import { StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { EvidenceChip } from "@/features/leads/EvidenceChip";
import { TIMELINE_KINDS, TIMELINE_KIND_KEYS } from "@/features/leads/labels";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop, toIpcError, type IpcError } from "@/lib/ipc";
import { jobPath, scoreRunPath, sourcePath } from "@/lib/links";

const PAGE_SIZE = 50;

interface Page {
  /** The kinds the entries were read for. */
  key: string;
  entries: TimelineEntry[];
  next: string | null;
}

/**
 * A lead's timeline (phase 12): what was acquired, changed, analysed, reviewed, researched and
 * recorded, newest first, page by page. Each entry links to the job, run, source or row behind it.
 */
export function TimelinePanel({ businessId }: { businessId: string }) {
  const t = useT();
  const language = useLanguage();
  const [kinds, setKinds] = useState<TimelineKind[]>([]);
  const [page, setPage] = useState<Page | null>(null);
  const [error, setError] = useState<IpcError | null>(null);
  const [loading, setLoading] = useState(false);
  const latest = useRef(0);
  const key = kinds.join(",");

  const read = useCallback(
    (cursor: string | null) => {
      const id = ++latest.current;
      desktop.getLeadTimeline({ business_id: businessId, kinds, limit: PAGE_SIZE, cursor }).then(
        (result) => {
          if (id !== latest.current) return;
          setPage((current) => ({
            key,
            entries:
              cursor !== null && current?.key === key
                ? [...current.entries, ...result.entries]
                : result.entries,
            next: result.next_cursor,
          }));
          setError(null);
          setLoading(false);
        },
        (cause: unknown) => {
          if (id !== latest.current) return;
          setError(toIpcError(cause, "get_lead_timeline"));
          setLoading(false);
        },
      );
    },
    [businessId, kinds, key],
  );

  useEffect(() => {
    read(null);
  }, [read]);

  const toggleKind = (kind: TimelineKind) =>
    setKinds((current) =>
      current.includes(kind) ? current.filter((value) => value !== kind) : [...current, kind],
    );
  const current = page?.key === key ? page : null;

  return (
    <section aria-label={t("timeline.title")} className="space-y-2 text-xs">
      <div role="group" aria-label={t("timeline.title")} className="flex flex-wrap gap-1">
        <Button
          variant={kinds.length === 0 ? "default" : "outline"}
          size="sm"
          className="h-6"
          aria-pressed={kinds.length === 0}
          onClick={() => setKinds([])}
        >
          {t("timeline.all")}
        </Button>
        {TIMELINE_KINDS.map((kind) => (
          <Button
            key={kind}
            variant={kinds.includes(kind) ? "default" : "outline"}
            size="sm"
            className="h-6"
            aria-pressed={kinds.includes(kind)}
            onClick={() => toggleKind(kind)}
          >
            {t(TIMELINE_KIND_KEYS[kind])}
          </Button>
        ))}
      </div>
      {error ? (
        <StateBlock
          variant={error.code === "desktop_unavailable" ? "offline" : "error"}
          title={t("timeline.loadError")}
          body={error.message}
          action={
            <Button variant="outline" size="sm" onClick={() => read(null)}>
              {t("common.retry")}
            </Button>
          }
        />
      ) : current === null ? (
        <StateBlock variant="loading" title={t("state.loading")} />
      ) : current.entries.length === 0 ? (
        <p className="text-muted-foreground">{t("timeline.empty")}</p>
      ) : (
        <ol className="space-y-1.5 border-l pl-3">
          {current.entries.map((entry) => (
            <li key={entry.key} className="space-y-0.5">
              <p className="flex flex-wrap items-baseline gap-x-2">
                <span className="font-mono text-[10px] text-muted-foreground tabular-nums">
                  {formatDateTime(entry.at, language)}
                </span>
                <span className="rounded border px-1 text-[10px] text-muted-foreground">
                  {t(TIMELINE_KIND_KEYS[entry.kind])}
                </span>
                <span className="font-medium break-words">{entry.title}</span>
              </p>
              {entry.detail && (
                <p className="text-[11px] break-words text-muted-foreground">{entry.detail}</p>
              )}
              <div className="flex flex-wrap items-center gap-2 text-[11px]">
                {entry.actor && <span className="text-muted-foreground">{entry.actor}</span>}
                {entry.ref && <EvidenceChip businessId={businessId} evidence={entry.ref} />}
                {entry.job_id && (
                  <Link to={jobPath(entry.job_id)} className="text-primary hover:underline">
                    {t("timeline.openJob")}
                  </Link>
                )}
                {entry.score_run_id && (
                  <Link
                    to={scoreRunPath(entry.score_run_id)}
                    className="text-primary hover:underline"
                  >
                    {t("timeline.openRun")}
                  </Link>
                )}
                {entry.source_key && (
                  <Link to={sourcePath(entry.source_key)} className="text-primary hover:underline">
                    {t("common.source")}
                  </Link>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
      {current?.next && (
        <Button
          variant="outline"
          size="sm"
          className="h-7"
          disabled={loading}
          onClick={() => {
            setLoading(true);
            read(current.next);
          }}
        >
          {t("common.loadMore")}
        </Button>
      )}
    </section>
  );
}
