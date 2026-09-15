import { useCallback, useState } from "react";
import { Link, useSearchParams } from "react-router";
import type { BulkMode, EventCleanupResult, EventLevel } from "@lead/shared-types";
import { toast } from "sonner";
import { PagedFooter } from "@/components/data/PagedFooter";
import { PageScroll, PageShell, Section } from "@/components/layout/page-chrome";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { logsPath } from "@/lib/links";
import { useKeysetPages } from "@/lib/useKeysetPages";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 100;
const LEVELS: readonly EventLevel[] = ["debug", "info", "warning", "error"];
const CLEANABLE: readonly EventLevel[] = ["debug", "info", "warning"];
const ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const FILTERS = ["job", "task", "business", "request"] as const;
type FilterName = (typeof FILTERS)[number];
const TONE: Record<EventLevel, string> = {
  debug: "text-muted-foreground",
  info: "text-info",
  warning: "text-warn",
  error: "text-danger",
};

/**
 * The persisted event log (phase 13): what every job did, a pipeline's engine jobs included, filtered
 * by job, stage, business, request or level. Old events of finished jobs can be cleaned up; errors
 * are always kept, because they explain what failed.
 */
export function LogsScreen() {
  const t = useT();
  const language = useLanguage();
  const [params, setParams] = useSearchParams();
  const read = (name: FilterName) => {
    const value = params.get(name);
    return value !== null && ID.test(value) ? value : null;
  };
  const job = read("job");
  const task = read("task");
  const business = read("business");
  const request = read("request");
  const invalid = FILTERS.some((name) => params.get(name) !== null && read(name) === null);
  const [levels, setLevels] = useState<EventLevel[]>([]);

  const fetchPage = useCallback(
    (cursor: string | null) =>
      desktop.listJobEvents({
        job_id: job,
        task_id: task,
        business_id: business,
        request_id: request,
        levels,
        limit: PAGE_SIZE,
        cursor,
      }),
    [job, task, business, request, levels],
  );
  const pages = useKeysetPages(fetchPage, "list_job_events");

  return (
    <PageShell>
      <PageScroll>
        <Section title={t("logs.title")} description={t("logs.help")}>
          <div className="space-y-2">
            <Filters key={params.toString()} onApply={setParams} />
            <fieldset className="flex flex-wrap items-center gap-3 text-xs">
              <legend className="sr-only">{t("logs.levels")}</legend>
              {LEVELS.map((level) => (
                <label key={level} className="flex items-center gap-1">
                  <input
                    type="checkbox"
                    checked={levels.includes(level)}
                    onChange={(event) =>
                      setLevels((current) =>
                        event.target.checked
                          ? [...current, level]
                          : current.filter((entry) => entry !== level),
                      )
                    }
                  />
                  <span className={TONE[level]}>{t(`logs.level.${level}`)}</span>
                </label>
              ))}
            </fieldset>
            {invalid && <InlineNotice variant="partial">{t("logs.invalidFilter")}</InlineNotice>}
            {pages.error ? (
              <StateBlock
                variant={pages.error.code === "desktop_unavailable" ? "offline" : "error"}
                title={t("logs.errorTitle")}
                body={pages.error.message}
                action={
                  <Button variant="outline" size="sm" onClick={() => void pages.reload()}>
                    {t("common.retry")}
                  </Button>
                }
              />
            ) : pages.loading && pages.items.length === 0 ? (
              <StateBlock variant="loading" title={t("common.loading")} />
            ) : pages.items.length === 0 ? (
              <StateBlock variant="empty" title={t("logs.emptyTitle")} body={t("logs.emptyBody")} />
            ) : (
              <>
                <div className="overflow-auto">
                  <table className="w-full text-[11px]">
                    <thead>
                      <tr className="text-left text-muted-foreground">
                        {(
                          [
                            "logs.column.time",
                            "logs.column.level",
                            "logs.column.job",
                            "logs.column.stage",
                            "logs.column.event",
                            "logs.column.detail",
                          ] as const
                        ).map((key) => (
                          <th key={key} scope="col" className="py-1 pr-2 font-medium">
                            {t(key)}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {pages.items.map(({ event, job_kind, stage }) => {
                        const detail = event.detail_json ? JSON.stringify(event.detail_json) : "";
                        return (
                          <tr key={event.id} className="border-t align-top">
                            <td className="py-0.5 pr-2 whitespace-nowrap">
                              {formatDateTime(event.created_at, language)}
                            </td>
                            <td className={cn("pr-2", TONE[event.level])}>
                              {t(`logs.level.${event.level}`)}
                            </td>
                            <td className="pr-2 whitespace-nowrap">
                              <Link
                                to={logsPath(event.job_id)}
                                title={event.job_id}
                                className="text-primary hover:underline"
                              >
                                {job_kind}
                              </Link>
                            </td>
                            <td className="pr-2">{stage ?? "—"}</td>
                            <td className="pr-2 font-mono">
                              {event.error_code
                                ? `${event.event} (${event.error_code}${event.retryable ? `, ${t("logs.retryable")}` : ""})`
                                : event.event}
                            </td>
                            <td className="max-w-96 truncate font-mono" title={detail}>
                              {detail}
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
              </>
            )}
          </div>
        </Section>
        <Cleanup onApplied={() => void pages.reload()} />
      </PageScroll>
    </PageShell>
  );
}

/** Id filters, written into the address so a link can carry them. */
function Filters({ onApply }: { onApply: (next: URLSearchParams) => void }) {
  const t = useT();
  const [params] = useSearchParams();
  const [drafts, setDrafts] = useState<Record<FilterName, string>>(() => ({
    job: params.get("job") ?? "",
    task: params.get("task") ?? "",
    business: params.get("business") ?? "",
    request: params.get("request") ?? "",
  }));
  const apply = () => {
    const next = new URLSearchParams();
    for (const name of FILTERS) {
      const value = drafts[name].trim().toLowerCase();
      if (value) next.set(name, value);
    }
    onApply(next);
  };
  return (
    <form
      className="flex flex-wrap items-end gap-2 text-xs"
      onSubmit={(event) => {
        event.preventDefault();
        apply();
      }}
    >
      {FILTERS.map((name) => (
        <div key={name} className="space-y-0.5">
          <Label htmlFor={`logs-${name}`} className="text-[11px]">
            {t(`logs.filter.${name}`)}
          </Label>
          <Input
            id={`logs-${name}`}
            value={drafts[name]}
            maxLength={36}
            placeholder="00000000-0000-0000-0000-000000000000"
            onChange={(event) => setDrafts({ ...drafts, [name]: event.target.value })}
            className="h-7 w-72 font-mono text-xs"
          />
        </div>
      ))}
      <Button type="submit" size="sm" variant="outline" className="h-7">
        {t("logs.applyFilters")}
      </Button>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        className="h-7"
        onClick={() => onApply(new URLSearchParams())}
      >
        {t("logs.clearFilters")}
      </Button>
    </form>
  );
}

/** Deletes old events of finished jobs: previewed first, applied with a reason. */
function Cleanup({ onApplied }: { onApplied: () => void }) {
  const t = useT();
  const [days, setDays] = useState(30);
  const [levels, setLevels] = useState<EventLevel[]>(["debug", "info"]);
  const [reason, setReason] = useState("");
  const [result, setResult] = useState<EventCleanupResult | null>(null);
  const [busy, setBusy] = useState(false);
  const valid = Number.isInteger(days) && days >= 1 && days <= 3650 && levels.length > 0;

  const run = async (mode: BulkMode) => {
    setBusy(true);
    try {
      const outcome = await desktop.cleanupJobEvents({
        mode,
        older_than_days: days,
        levels: CLEANABLE.filter((level) => levels.includes(level)),
        reason: mode === "apply" ? reason.trim() : null,
      });
      setResult(outcome);
      if (mode === "apply") {
        toast.success(t("logs.cleaned", { n: outcome.deleted }));
        setReason("");
        onApplied();
      }
    } catch (cause) {
      toast.error(t("logs.cleanupFailed"), {
        description: toIpcError(cause, "cleanup_job_events").message,
      });
    } finally {
      setBusy(false);
    }
  };

  const edit = (apply: () => void) => {
    apply();
    setResult(null);
  };

  return (
    <Section title={t("logs.cleanupTitle")} description={t("logs.cleanupHelp")}>
      <div className="space-y-2 text-xs">
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-0.5">
            <Label htmlFor="cleanup-days" className="text-[11px]">
              {t("logs.olderThan")}
            </Label>
            <Input
              id="cleanup-days"
              type="number"
              min={1}
              max={3650}
              value={days}
              onChange={(event) => edit(() => setDays(Number(event.target.value)))}
              className="h-7 w-24 text-xs"
            />
          </div>
          <fieldset className="flex items-center gap-3">
            <legend className="sr-only">{t("logs.levels")}</legend>
            {CLEANABLE.map((level) => (
              <label key={level} className="flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={levels.includes(level)}
                  onChange={(event) =>
                    edit(() =>
                      setLevels((current) =>
                        event.target.checked
                          ? [...current, level]
                          : current.filter((entry) => entry !== level),
                      ),
                    )
                  }
                />
                {t(`logs.level.${level}`)}
              </label>
            ))}
          </fieldset>
        </div>
        <InlineNotice variant="blocked">{t("logs.errorsKept")}</InlineNotice>
        <div className="space-y-0.5">
          <Label htmlFor="cleanup-reason" className="text-[11px]">
            {t("logs.reason")}
          </Label>
          <Textarea
            id="cleanup-reason"
            value={reason}
            maxLength={2000}
            onChange={(event) => setReason(event.target.value)}
            className="min-h-10 text-xs"
          />
        </div>
        {result && (
          <p role="status" className="font-medium">
            {t(result.mode === "preview" ? "logs.previewLine" : "logs.appliedLine", {
              matched: result.matched,
              deleted: result.deleted,
              kept: result.kept_errors,
            })}
          </p>
        )}
        <div className="flex justify-end gap-1">
          <Button
            variant="outline"
            size="sm"
            className="h-7"
            disabled={busy || !valid}
            onClick={() => void run("preview")}
          >
            {t("logs.preview")}
          </Button>
          <Button
            size="sm"
            className="h-7"
            disabled={
              busy || !valid || result?.mode !== "preview" || result.matched === 0 || !reason.trim()
            }
            onClick={() => void run("apply")}
          >
            {t("logs.apply")}
          </Button>
        </div>
      </div>
    </Section>
  );
}
