import { useCallback, useEffect, useRef, useState } from "react";
import type { LatinoReviewFilter, LatinoStatus, SourceKind } from "@lead/shared-types";
import { Play } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import type { LatinoFilter } from "@/features/latino/filters";
import {
  LATINO_STATUSES,
  LATINO_STATUS_KEYS,
  REVIEW_FILTERS,
  REVIEW_FILTER_KEYS,
  SOURCE_KINDS,
  SOURCE_KIND_KEYS,
} from "@/features/latino/labels";
import { useT } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { useLoaded } from "@/lib/useLoaded";

const RUN_POLL_MS = 1000;

interface LatinoFiltersProps {
  value: LatinoFilter;
  onChange: (value: LatinoFilter) => void;
  /** Called when an evaluation stops moving, so the list is read again. */
  onEvaluated: () => void;
}

/** Latino evidence filters for Businesses, next to the evaluation that produces what they filter. */
export function LatinoFilters({ value, onChange, onEvaluated }: LatinoFiltersProps) {
  const t = useT();
  const [starting, setStarting] = useState(false);
  const fetchRuns = useCallback(
    () => desktop.listLatinoRuns({ project_id: null, limit: 5, cursor: null }),
    [],
  );
  const runs = useLoaded(fetchRuns, "list_latino_runs");
  const latest = runs.data?.items[0] ?? null;
  const active = (runs.data?.items ?? []).some(
    (run) => run.status === "queued" || run.status === "running",
  );
  const reloadRuns = runs.reload;
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => void reloadRuns(), RUN_POLL_MS);
    return () => clearInterval(timer);
  }, [active, reloadRuns]);
  const wasActive = useRef(active);
  useEffect(() => {
    if (wasActive.current && !active) onEvaluated();
    wasActive.current = active;
  }, [active, onEvaluated]);

  const start = async () => {
    setStarting(true);
    try {
      const started = await desktop.startLatino({ project_id: null });
      toast.success(t("latino.started", { n: started.items.length }));
      await reloadRuns();
    } catch (cause) {
      toast.error(t("latino.startFailed"), {
        description: toIpcError(cause, "start_latino").message,
      });
    } finally {
      setStarting(false);
    }
  };

  const set = <K extends keyof LatinoFilter>(key: K, next: LatinoFilter[K]) =>
    onChange({ ...value, [key]: next });
  const select = "h-7 rounded-md border bg-transparent px-2 text-xs";
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-2 border-b px-2 py-1.5 text-xs">
      <label className="flex items-center gap-1">
        {t("latino.filterStatus")}
        <select
          value={value.status}
          onChange={(event) => set("status", event.target.value as LatinoStatus | "")}
          className={select}
        >
          <option value="">{t("latino.any")}</option>
          {LATINO_STATUSES.map((status) => (
            <option key={status} value={status}>
              {t(LATINO_STATUS_KEYS[status])}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-1">
        {t("latino.filterConfidence")}
        <input
          type="number"
          min={0}
          max={100}
          step={5}
          value={value.minConfidence}
          onChange={(event) => set("minConfidence", event.target.value)}
          className={`${select} w-16`}
        />
      </label>
      <label className="flex items-center gap-1">
        {t("latino.filterSource")}
        <select
          value={value.sourceKind}
          onChange={(event) => set("sourceKind", event.target.value as SourceKind | "")}
          className={select}
        >
          <option value="">{t("latino.any")}</option>
          {SOURCE_KINDS.map((kind) => (
            <option key={kind} value={kind}>
              {t(SOURCE_KIND_KEYS[kind])}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-1">
        {t("latino.filterReview")}
        <select
          value={value.review}
          onChange={(event) => set("review", event.target.value as LatinoReviewFilter | "")}
          className={select}
        >
          <option value="">{t("latino.any")}</option>
          {REVIEW_FILTERS.map((review) => (
            <option key={review} value={review}>
              {t(REVIEW_FILTER_KEYS[review])}
            </option>
          ))}
        </select>
      </label>
      {latest && (
        <span className="text-[11px] text-muted-foreground">
          {active
            ? t("latino.running", {
                stage: latest.stage,
                done: latest.progress_done,
                total: latest.progress_total ?? "?",
              })
            : t("latino.lastRun", {
                businesses: latest.stats.businesses,
                ownership:
                  latest.stats.verified + latest.stats.high_confidence + latest.stats.probable,
                affinity: latest.stats.affinity,
              })}
        </span>
      )}
      <Button
        variant="outline"
        size="sm"
        className="ml-auto h-7"
        disabled={starting || active}
        onClick={() => void start()}
      >
        <Play aria-hidden="true" />
        {t("latino.run")}
      </Button>
      <p className="basis-full text-[11px] text-muted-foreground">{t("latino.explain")}</p>
    </div>
  );
}
