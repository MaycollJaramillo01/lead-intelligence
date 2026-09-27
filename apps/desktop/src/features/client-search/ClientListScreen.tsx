import { useState } from "react";
import { Copy, Download } from "lucide-react";
import { Link, useSearchParams } from "react-router";
import { toast } from "sonner";
import type { LeadStage } from "@lead/shared-types";
import { PageScroll, PageShell } from "@/components/layout/page-chrome";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { STAGE_KEYS, TIER_KEYS } from "@/features/scoring/labels";
import { useT, type TranslationKey } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { businessPath } from "@/lib/links";
import { cn } from "@/lib/utils";
import {
  clientColumnLabels,
  clientExportDraft,
  latinoFirst,
  LIST_REASON,
  NO_FILTERS,
  offerOf,
  outcomeRequest,
  searchTitle,
  stagesFor,
  tierKey,
  whyKey,
  whyProblems,
  type ClientAction,
  type ClientFilters,
} from "./model";
import { POLL_MS, useClientList, useSearchRuns, type ClientRowData } from "./useSearches";

const WRITTEN: ReadonlySet<string> = new Set(["succeeded", "failed", "cancelled"]);
/** The stages the list itself records, said as the person who recorded them would. */
const STAGE_WORDS: Partial<Record<LeadStage, TranslationKey>> = {
  attempted: "clientList.stage.attempted",
  connected: "clientList.stage.connected",
  qualified: "clientList.stage.qualified",
  not_interested: "clientList.stage.not_interested",
};
const FILTERS = [
  ["phone", "clientList.filter.phone"],
  ["email", "clientList.filter.email"],
  ["callFirst", "clientList.filter.callFirst"],
] as const satisfies ReadonlyArray<readonly [keyof ClientFilters, TranslationKey]>;
const ACTIONS = [
  ["interested", "clientList.action.interested"],
  ["not_interested", "clientList.action.notInterested"],
] as const satisfies ReadonlyArray<readonly [ClientAction, TranslationKey]>;

/** A number or an address, selectable and copied with one click. */
function Contact({ value }: { value: string }) {
  const t = useT();
  const copy = () =>
    void navigator.clipboard.writeText(value).then(
      () => toast.success(t("clientList.copied")),
      () => undefined, // the text stays selectable
    );
  return (
    <span className="inline-flex items-center gap-1">
      <span className="font-mono select-all">{value}</span>
      <Button
        type="button"
        size="icon"
        variant="ghost"
        className="size-7"
        aria-label={t("clientList.copy", { value })}
        onClick={copy}
      >
        <Copy aria-hidden="true" />
      </Button>
    </span>
  );
}

/** One client: who, how to reach them, why to call, what to offer and what happened so far. */
function ClientRow({
  data: { row, contacts },
  scoreRunId,
  latino,
  otherName,
  onChanged,
}: {
  data: ClientRowData;
  scoreRunId: string;
  latino: boolean;
  /** The person's own service («Otro») in this search. */
  otherName: string | null;
  onChanged: () => void;
}) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const choice = offerOf(row.offer);
  const offer = choice === "other" ? otherName : choice && t(`clientSearch.offer.${choice}`);
  const why = whyProblems(row.top_problem, row.problems);
  const stage = row.lead_stage;
  const notes = [
    stage !== "not_contacted" && t(STAGE_WORDS[stage] ?? STAGE_KEYS[stage]),
    latino && row.latino_status !== "unknown" && t("clientSearch.latinoEvidence"),
  ].filter(Boolean);

  const act = async (work: () => Promise<void>) => {
    setBusy(true);
    try {
      await work();
      onChanged();
    } catch (cause) {
      toast.error(t("clientList.actionFailed"), {
        description: toIpcError(cause, "client list").message,
      });
    } finally {
      setBusy(false);
    }
  };

  /** Walks the funnel stage by stage, as it only allows (an interested client was reached). */
  const record = (action: ClientAction) =>
    act(async () => {
      const channel = action === "contacted" ? (contacts.phone ? "phone" : "email") : null;
      for (const next of stagesFor(action, stage)) {
        await desktop.actOnLead(outcomeRequest(scoreRunId, row.business_id, next, channel));
      }
      toast.success(t("clientList.saved"));
    });

  const doNotContact = () =>
    act(async () => {
      const entry = await desktop.changeSuppression({
        suppression_id: null,
        kind: "business",
        value: row.business_id,
        reason: LIST_REASON,
      });
      const undo = () =>
        desktop
          .changeSuppression({
            suppression_id: entry.id,
            kind: null,
            value: null,
            reason: LIST_REASON,
          })
          .then(onChanged, (cause: unknown) => toast.error(toIpcError(cause).message));
      toast.success(t("clientList.suppressed", { name: row.display_name }), {
        action: { label: t("clientList.undo"), onClick: () => void undo() },
      });
    });

  return (
    <li className="space-y-2 px-3 py-3 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-medium">{row.display_name}</p>
          <p className="text-xs text-muted-foreground">
            {[row.locality, row.region].filter(Boolean).join(", ")}
          </p>
        </div>
        <span
          className={cn(
            "rounded-full border px-2 py-0.5 text-xs font-medium",
            row.tier === "priority" && "border-primary text-primary",
          )}
        >
          {t(tierKey(row.tier) ?? TIER_KEYS[row.tier])}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        {contacts.phone && <Contact value={contacts.phone} />}
        {contacts.email && <Contact value={contacts.email} />}
        {row.domain && (
          <span className="text-muted-foreground">
            {t("clientList.website", { domain: row.domain })}
          </span>
        )}
      </div>
      {why.length > 0 && (
        <p>
          {t("clientSearch.why", { problem: why.map((problem) => t(whyKey(problem))).join("; ") })}
        </p>
      )}
      {offer && <p>{t("clientSearch.offerIt", { offer })}</p>}
      {notes.length > 0 && <p className="text-xs text-muted-foreground">{notes.join(" · ")}</p>}
      <div className="flex flex-wrap gap-2">
        {stagesFor("contacted", stage).length > 0 && (
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => void record("contacted")}
          >
            {t(contacts.phone ? "clientList.action.called" : "clientList.action.wrote")}
          </Button>
        )}
        {ACTIONS.filter(([action]) => stagesFor(action, stage).length > 0).map(([action, key]) => (
          <Button
            key={action}
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => void record(action)}
          >
            {t(key)}
          </Button>
        ))}
        <Button size="sm" variant="outline" disabled={busy} onClick={() => void doNotContact()}>
          {t("clientList.action.doNotContact")}
        </Button>
        <Button asChild size="sm" variant="ghost">
          <Link to={`${businessPath(row.business_id)}?section=offer`}>
            {t("clientList.action.script")}
          </Link>
        </Button>
        <Button asChild size="sm" variant="ghost">
          <Link to={businessPath(row.business_id)}>{t("clientList.action.details")}</Link>
        </Button>
      </div>
    </li>
  );
}

/** The clients one search found: who to call, on what number and what to offer (phase 16). */
export function ClientListScreen() {
  const t = useT();
  const [params] = useSearchParams();
  const { runs } = useSearchRuns();
  const [filters, setFilters] = useState<ClientFilters>(NO_FILTERS);
  const [busy, setBusy] = useState(false);
  const job = params.get("job");
  const finished =
    runs.find((run) => run.status === "succeeded" && (job === null || run.job_id === job)) ?? null;
  const { list, reload } = useClientList(finished, filters);
  const latino = latinoFirst(finished?.campaign_name ?? null);
  const scoreRunId = finished?.score_run_id ?? null;
  const filtered = Object.values(filters).some(Boolean);

  /** Writes what the filters keep to the file the save dialog names; the page never sees a path. */
  const download = async () => {
    if (!list) return;
    setBusy(true);
    try {
      const file = clientExportDraft(
        list.query,
        (key) => t(`clientSearch.column.${key}`),
        clientColumnLabels(t, list.otherName),
      );
      const destination = await desktop.chooseExportDestination(
        "csv",
        (searchTitle(t, finished?.campaign_name ?? null) ?? t("clientSearch.results")).replaceAll(
          " · ",
          " - ",
        ),
      );
      if (!destination) return; // the save dialog was closed
      let item = await desktop.startExport(file, destination.grant_id);
      const exportId = item.export.id;
      while (!WRITTEN.has(item.export.status)) {
        await new Promise((resolve) => window.setTimeout(resolve, POLL_MS));
        const page = await desktop.listExports({
          project_id: item.export.project_id,
          limit: 25,
          cursor: null,
        });
        item = page.items.find((entry) => entry.export.id === exportId) ?? item;
      }
      if (item.export.status !== "succeeded") {
        throw new Error(item.export.error_message ?? item.export.error_code ?? item.export.status);
      }
      toast.success(
        t("clientSearch.downloaded", {
          count: item.export.row_count ?? 0,
          file: item.export.file_name ?? "",
        }),
      );
    } catch (cause) {
      toast.error(t("clientSearch.downloadFailed"), {
        description: toIpcError(cause, "client export").message,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <PageShell>
      <PageScroll>
        <div className="mx-auto max-w-5xl space-y-6 p-6">
          <header>
            <h1 className="text-2xl font-semibold">{t("clientSearch.results")}</h1>
            <p className="mt-1 text-muted-foreground">
              {searchTitle(t, finished?.campaign_name ?? null) ?? t("desc.client_list")}
            </p>
          </header>

          {finished === null ? (
            <section className="rounded-xl border bg-card p-5">
              <p className="text-sm text-muted-foreground">{t("clientList.empty")}</p>
              <Button asChild className="mt-3">
                <Link to="/buscar-clientes">{t("clientSearch.button")}</Link>
              </Button>
            </section>
          ) : (
            <section className="space-y-3 rounded-xl border bg-card p-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="font-medium">
                  {list
                    ? t("clientSearch.ready", { count: list.total })
                    : t("clientSearch.loading")}
                </p>
                {list && list.total > 0 && (
                  <Button disabled={busy} onClick={() => void download()}>
                    <Download aria-hidden="true" />
                    {t("clientSearch.download")}
                  </Button>
                )}
              </div>
              <fieldset className="flex flex-wrap items-center gap-4 text-sm">
                <legend className="sr-only">{t("clientList.filters")}</legend>
                <span aria-hidden="true" className="text-muted-foreground">
                  {t("clientList.filters")}
                </span>
                {FILTERS.map(([key, label]) => (
                  <span key={key} className="flex items-center gap-2">
                    <Checkbox
                      id={`client-filter-${key}`}
                      checked={filters[key]}
                      onCheckedChange={(checked) =>
                        setFilters((current) => ({ ...current, [key]: checked === true }))
                      }
                    />
                    <Label htmlFor={`client-filter-${key}`} className="font-normal">
                      {t(label)}
                    </Label>
                  </span>
                ))}
              </fieldset>
              {list?.total === 0 && (
                <p className="text-sm text-muted-foreground">
                  {filtered
                    ? t("clientList.noneFiltered")
                    : t("clientSearch.none", { found: list.found })}
                </p>
              )}
              {list && list.rows.length > 0 && scoreRunId && (
                <ul className="divide-y rounded-md border">
                  {list.rows.map((data) => (
                    <ClientRow
                      key={data.row.business_id}
                      data={data}
                      scoreRunId={scoreRunId}
                      latino={latino}
                      otherName={list.otherName}
                      onChanged={reload}
                    />
                  ))}
                </ul>
              )}
              {list && list.total > list.rows.length && (
                <p className="text-xs text-muted-foreground">
                  {t("clientSearch.more", { shown: list.rows.length, count: list.total })}
                </p>
              )}
            </section>
          )}
        </div>
      </PageScroll>
    </PageShell>
  );
}
