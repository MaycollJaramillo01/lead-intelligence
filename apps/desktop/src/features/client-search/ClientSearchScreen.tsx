import { useCallback, useEffect, useMemo, useState } from "react";
import { Download, Pause, Play, Search, Square } from "lucide-react";
import { toast } from "sonner";
import type { ExplorerQuery, ExplorerRow, PipelineRun } from "@lead/shared-types";
import { PageScroll, PageShell } from "@/components/layout/page-chrome";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { BUSINESS_TYPES, US_STATES } from "@/features/campaigns/businessTypes";
import { PROBLEM_KEYS } from "@/features/scoring/labels";
import { useT } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { cn } from "@/lib/utils";
import {
  buildCampaignRequest,
  buildIcpRequest,
  buildPipelineRequest,
  clientExportDraft,
  clientListQuery,
  contactOf,
  latinoFirst,
  offerOf,
  STAGE_MESSAGE,
  visibleProgress,
  type ClientSearchDraft,
  type OfferChoice,
} from "./model";

const OFFERS: OfferChoice[] = ["website", "conversion", "booking", "google", "measurement"];
const POLL_MS = 2_000;
const WRITTEN: ReadonlySet<string> = new Set(["succeeded", "failed", "cancelled"]);

interface ClientList {
  jobId: string;
  /** The clients of the search, as the file downloads them: every match, not only `rows`. */
  query: ExplorerQuery;
  /** Every client of the search; `rows` holds the first of them. */
  total: number;
  /** Every lead of the search, whatever its tier. */
  found: number;
  rows: { row: ExplorerRow; contact: string | null }[];
}

export function ClientSearchScreen() {
  const t = useT();
  const [draft, setDraft] = useState<ClientSearchDraft>({
    offers: [],
    businessTypeId: "",
    latinoOnly: false,
    region: "",
    locality: "",
    radiusKm: null,
    wanted: 25,
  });
  const [runs, setRuns] = useState<PipelineRun[]>([]);
  const [busy, setBusy] = useState(false);
  const [clients, setClients] = useState<ClientList | null>(null);
  const [aiOff, setAiOff] = useState(false);
  const active =
    runs.find((run) => ["queued", "running", "paused"].includes(run.status)) ?? runs[0];
  const finished = active?.status === "succeeded" ? active : null;

  const load = useCallback(async () => {
    try {
      const page = await desktop.listPipelines({ project_id: null, limit: 50, cursor: null });
      setRuns(page.items.filter((run) => run.campaign_name?.startsWith("Buscar clientes ·")));
    } catch {
      /* diagnostics already explains an unavailable worker */
    }
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);
  useEffect(() => {
    if (!runs.some((run) => ["queued", "running"].includes(run.status))) return;
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [load, runs]);
  useEffect(() => {
    void desktop
      .getAiStatus({ include_llm_fit: false })
      .then((status) => setAiOff(status.profiles.every((profile) => !profile.model)))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!finished) return;
    let cancelled = false;
    void (async () => {
      try {
        const campaigns = await desktop.listCampaigns({
          project_id: finished.project_id,
          limit: 500,
          cursor: null,
        });
        const campaign = campaigns.items.find((item) => item.id === finished.campaign_id);
        if (!campaign) return;
        const query = clientListQuery(finished.score_run_id, campaign);
        const [page, found] = await Promise.all([
          desktop.exploreLeads(query),
          desktop.exploreLeads(clientListQuery(finished.score_run_id, campaign, true)),
        ]);
        const rows = await Promise.all(
          page.rows.map(async (row) => {
            const detail = await desktop
              .getBusiness({ business_id: row.business_id })
              .catch(() => null);
            return { row, contact: detail ? contactOf(detail.contacts.map((c) => c.value)) : null };
          }),
        );
        if (cancelled) return;
        setClients({
          jobId: finished.job_id,
          query,
          total: page.total ?? rows.length,
          found: found.total ?? 0,
          rows,
        });
      } catch (cause) {
        if (!cancelled) toast.error(toIpcError(cause, "client list").message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [finished]);

  const valid =
    draft.offers.length > 0 && draft.businessTypeId && draft.region && draft.locality.trim();
  const offerNames = useMemo(
    () =>
      Object.fromEntries(OFFERS.map((key) => [key, t(`clientSearch.offer.${key}`)])) as Record<
        OfferChoice,
        string
      >,
    [t],
  );

  const start = async () => {
    if (!valid) return;
    setBusy(true);
    try {
      const campaignDraft = buildCampaignRequest(draft);
      const campaigns = await desktop.listCampaigns({ project_id: null, limit: 100, cursor: null });
      const priorCampaign = campaigns.items.find((item) => item.name === campaignDraft.name);
      const savedCampaign = await desktop.saveCampaign({
        ...campaignDraft,
        campaign_id: priorCampaign?.id ?? null,
      });

      const catalog = await desktop.getScoringCatalog({
        project_id: savedCampaign.campaign.project_id,
      });
      const priorProfile = catalog.icp_profiles.find(
        (item) => item.profile.name === "Mi oferta" && !item.profile.archived_at,
      );
      const profile = await desktop.saveIcpProfile(
        buildIcpRequest(draft, catalog.icp_template, offerNames, priorProfile?.profile.id ?? null),
      );
      const version = profile.versions[0];
      if (!version) throw new Error("The saved offer has no version");
      await desktop.changeScoring({
        target: "icp_profile",
        id: profile.profile.id,
        action: "activate",
        version_id: version.id,
        reason: "Configurado desde Buscar clientes",
      });
      await desktop.startPipeline(
        buildPipelineRequest(
          savedCampaign.campaign.project_id,
          savedCampaign.campaign.id,
          crypto.randomUUID(),
        ),
      );
      toast.success(t("clientSearch.started"));
      await load();
    } catch (cause) {
      toast.error(t("clientSearch.startFailed"), {
        description: toIpcError(cause, "client search").message,
      });
    } finally {
      setBusy(false);
    }
  };

  const control = async (action: "pause" | "resume" | "cancel") => {
    if (!active) return;
    try {
      await desktop.controlPipeline({ job_id: active.job_id, action });
      await load();
    } catch (cause) {
      toast.error(toIpcError(cause).message);
    }
  };

  const list = finished && clients?.jobId === finished.job_id ? clients : null;
  const latino = latinoFirst(active?.campaign_name ?? null);

  /** Writes the whole list to the file the save dialog names; the page never sees a path. */
  const download = async () => {
    if (!list) return;
    setBusy(true);
    try {
      const file = clientExportDraft(list.query, (key) => t(`clientSearch.column.${key}`));
      const destination = await desktop.chooseExportDestination(
        "csv",
        (active?.campaign_name ?? t("clientSearch.title")).replaceAll(" · ", " - "),
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
            <h1 className="text-2xl font-semibold">{t("clientSearch.title")}</h1>
            <p className="mt-1 text-muted-foreground">{t("clientSearch.promise")}</p>
          </header>

          <section className="rounded-xl border bg-card p-5" aria-labelledby="offer-heading">
            <h2 id="offer-heading" className="font-semibold">
              1. {t("clientSearch.what")}
            </h2>
            <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {OFFERS.map((offer) => {
                const selected = draft.offers.includes(offer);
                return (
                  <button
                    key={offer}
                    type="button"
                    aria-pressed={selected}
                    onClick={() =>
                      setDraft((state) => ({
                        ...state,
                        offers: selected
                          ? state.offers.filter((item) => item !== offer)
                          : [...state.offers, offer],
                      }))
                    }
                    className={cn(
                      "rounded-lg border p-3 text-left text-sm outline-none transition focus-visible:ring-2 focus-visible:ring-ring",
                      selected && "border-primary bg-primary/10",
                    )}
                  >
                    {t(`clientSearch.offer.${offer}`)}
                  </button>
                );
              })}
            </div>
          </section>

          <section className="grid gap-5 rounded-xl border bg-card p-5 md:grid-cols-2">
            <div>
              <Label htmlFor="business-type" className="font-semibold">
                2. {t("clientSearch.business")}
              </Label>
              <select
                id="business-type"
                value={draft.businessTypeId}
                onChange={(event) => setDraft({ ...draft, businessTypeId: event.target.value })}
                className="mt-3 h-9 w-full rounded-md border bg-background px-3 text-sm"
              >
                <option value="">{t("clientSearch.chooseBusiness")}</option>
                {BUSINESS_TYPES.map((type) => (
                  <option key={type.id} value={type.id}>
                    {t(type.label)}
                  </option>
                ))}
              </select>
              <div className="mt-4 flex items-start gap-2 text-sm">
                <Checkbox
                  id="latino-only"
                  checked={draft.latinoOnly}
                  onCheckedChange={(checked) =>
                    setDraft({ ...draft, latinoOnly: checked === true })
                  }
                />
                <Label htmlFor="latino-only" className="font-normal">
                  <strong>{t("clientSearch.latino")}</strong>
                  <span className="mt-1 block text-xs text-muted-foreground">
                    {t("clientSearch.latinoHelp")}
                  </span>
                </Label>
              </div>
            </div>
            <div>
              <h2 className="font-semibold">3. {t("clientSearch.where")}</h2>
              <div className="mt-3 grid grid-cols-2 gap-2">
                <Label htmlFor="region" className="sr-only">
                  {t("clientSearch.state")}
                </Label>
                <select
                  id="region"
                  value={draft.region}
                  onChange={(event) => setDraft({ ...draft, region: event.target.value })}
                  className="h-9 rounded-md border bg-background px-3 text-sm"
                >
                  <option value="">{t("clientSearch.state")}</option>
                  {US_STATES.map(([code, name]) => (
                    <option key={code} value={code}>
                      {name}
                    </option>
                  ))}
                </select>
                <Label htmlFor="locality" className="sr-only">
                  {t("clientSearch.city")}
                </Label>
                <Input
                  id="locality"
                  placeholder={t("clientSearch.city")}
                  value={draft.locality}
                  onChange={(event) => setDraft({ ...draft, locality: event.target.value })}
                />
                <Label htmlFor="radius" className="sr-only">
                  {t("clientSearch.radius")}
                </Label>
                <Input
                  id="radius"
                  className="col-span-2"
                  type="number"
                  min={1}
                  max={500}
                  placeholder={t("clientSearch.radius")}
                  value={draft.radiusKm ?? ""}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      radiusKm: event.target.value ? Number(event.target.value) : null,
                    })
                  }
                />
              </div>
            </div>
          </section>

          <section className="rounded-xl border bg-card p-5">
            <h2 className="font-semibold">4. {t("clientSearch.howMany")}</h2>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {([25, 50, 100] as const).map((wanted) => (
                <Button
                  key={wanted}
                  type="button"
                  variant={draft.wanted === wanted ? "default" : "outline"}
                  onClick={() => setDraft({ ...draft, wanted })}
                >
                  {wanted}
                </Button>
              ))}
              <span className="ml-2 text-xs text-muted-foreground">
                {t(`clientSearch.estimate.${draft.wanted}`)}
              </span>
              <Button className="ml-auto" disabled={!valid || busy} onClick={() => void start()}>
                <Search aria-hidden="true" />
                {busy ? t("state.processing") : t("clientSearch.button")}
              </Button>
            </div>
          </section>

          {active && (
            <section className="rounded-xl border bg-card p-5" aria-live="polite">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="font-semibold">
                    {active.status === "succeeded"
                      ? t("clientSearch.results")
                      : t("clientSearch.yourSearch")}
                  </h2>
                  <p className="text-xs text-muted-foreground">{active.campaign_name}</p>
                </div>
                <div className="flex gap-2">
                  {active.actions.includes("pause") && (
                    <Button variant="outline" onClick={() => void control("pause")}>
                      <Pause />
                      {t("clientSearch.pause")}
                    </Button>
                  )}
                  {active.actions.includes("resume") && (
                    <Button onClick={() => void control("resume")}>
                      <Play />
                      {t("clientSearch.continue")}
                    </Button>
                  )}
                  {active.actions.includes("cancel") && (
                    <Button variant="outline" onClick={() => void control("cancel")}>
                      <Square />
                      {t("common.cancel")}
                    </Button>
                  )}
                </div>
              </div>
              <ol className="mt-4 space-y-2">
                {visibleProgress(active.stages).map((stage) => {
                  // the AI stage still walks every task when no model is assigned, skipping each
                  const aiSkipped = stage.stage === "local_ai" && (stage.skipped || aiOff);
                  const jobs = stage.attempts.at(-1)?.jobs ?? [];
                  const done = jobs.reduce((sum, job) => sum + job.progress_done, 0);
                  const totals = jobs
                    .map((job) => job.progress_total)
                    .filter((value): value is number => value != null);
                  const total =
                    totals.length === jobs.length
                      ? totals.reduce((sum, value) => sum + value, 0)
                      : null;
                  return (
                    <li
                      key={stage.task_id}
                      className="flex justify-between rounded-md bg-muted/40 px-3 py-2 text-sm"
                    >
                      <span>
                        {aiSkipped
                          ? t("clientSearch.progress.aiSkipped")
                          : t(STAGE_MESSAGE[stage.stage])}
                      </span>
                      {!aiSkipped && (
                        <span className="text-muted-foreground">
                          {done}
                          {total != null ? ` / ${total}` : ""}
                        </span>
                      )}
                    </li>
                  );
                })}
              </ol>
              {finished && (
                <div className="mt-4 space-y-3">
                  {!list ? (
                    <p className="text-sm text-muted-foreground">{t("clientSearch.loading")}</p>
                  ) : (
                    <>
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <p className="font-medium">
                          {t("clientSearch.ready", { count: list.total })}
                        </p>
                        <div className="flex flex-wrap gap-2">
                          {list.total > 0 && (
                            <Button disabled={busy} onClick={() => void download()}>
                              <Download aria-hidden="true" />
                              {t("clientSearch.download")}
                            </Button>
                          )}
                          <Button asChild variant="outline">
                            <a href="#/lead-explorer">{t("clientSearch.openList")}</a>
                          </Button>
                        </div>
                      </div>
                      {list.total === 0 ? (
                        <p className="text-sm text-muted-foreground">
                          {t("clientSearch.none", { found: list.found })}
                        </p>
                      ) : (
                        <ul className="divide-y rounded-md border">
                          {list.rows.map(({ row, contact }) => {
                            const offer = offerOf(row.offer);
                            const details = [
                              row.locality,
                              row.top_problem &&
                                t("clientSearch.why", {
                                  // an Overture place without a URL does not prove there is no site
                                  problem:
                                    row.top_problem === "no_website"
                                      ? t("clientSearch.noWebsiteSeen")
                                      : t(PROBLEM_KEYS[row.top_problem]),
                                }),
                              offer &&
                                t("clientSearch.offerIt", {
                                  offer: t(`clientSearch.offer.${offer}`),
                                }),
                              latino &&
                                row.latino_status !== "unknown" &&
                                t("clientSearch.latinoEvidence"),
                            ].filter(Boolean);
                            return (
                              <li
                                key={row.business_id}
                                className="flex flex-wrap items-start justify-between gap-2 px-3 py-2 text-sm"
                              >
                                <div className="min-w-0">
                                  <p className="font-medium">{row.display_name}</p>
                                  <p className="text-xs text-muted-foreground">
                                    {details.join(" · ")}
                                  </p>
                                </div>
                                {contact && <span className="select-all font-mono">{contact}</span>}
                              </li>
                            );
                          })}
                        </ul>
                      )}
                      {list.total > list.rows.length && (
                        <p className="text-xs text-muted-foreground">
                          {t("clientSearch.more", { shown: list.rows.length, count: list.total })}
                        </p>
                      )}
                    </>
                  )}
                </div>
              )}
            </section>
          )}

          <section className="rounded-xl border bg-card p-5">
            <h2 className="font-semibold">{t("clientSearch.history")}</h2>
            {runs.length === 0 ? (
              <p className="mt-2 text-sm text-muted-foreground">{t("clientSearch.noHistory")}</p>
            ) : (
              <ul className="mt-3 divide-y">
                {runs.map((run) => (
                  <li key={run.job_id} className="flex items-center justify-between py-2 text-sm">
                    <span>{run.campaign_name}</span>
                    <span className="text-muted-foreground">{t(`jobStatus.${run.status}`)}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </PageScroll>
    </PageShell>
  );
}
