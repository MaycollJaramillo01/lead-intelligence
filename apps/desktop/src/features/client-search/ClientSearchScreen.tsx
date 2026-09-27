import { useEffect, useMemo, useState } from "react";
import { Pause, Play, Search, Square } from "lucide-react";
import { useLocation } from "react-router";
import { toast } from "sonner";
import { PageScroll, PageShell } from "@/components/layout/page-chrome";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { BUSINESS_TYPES, US_STATES } from "@/features/campaigns/businessTypes";
import { citiesOf } from "@/features/campaigns/usCities";
import { useT } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { cn } from "@/lib/utils";
import {
  buildCampaignRequest,
  buildIcpRequest,
  buildPipelineRequest,
  searchTitle,
  STAGE_MESSAGE,
  visibleProgress,
  type ClientSearchDraft,
  type OfferChoice,
} from "./model";
import { SearchFailure } from "./SearchFailure";
import { useSearchRuns } from "./useSearches";

const OFFERS: OfferChoice[] = ["website", "conversion", "booking", "google", "measurement"];
const SELECT = "h-9 w-full rounded-md border bg-background px-3 text-sm disabled:opacity-60";

/**
 * Asks for a search in four steps and follows the one that is running (phase 16). The clients it
 * finds live in their own screen, so this one never grows into a list.
 */
export function ClientSearchScreen() {
  const t = useT();
  // "Repetir" in Mis búsquedas opens the form filled with the search it repeats
  const repeated = (useLocation().state as { repeat?: ClientSearchDraft } | null)?.repeat ?? null;
  const [draft, setDraft] = useState<ClientSearchDraft>(() =>
    repeated
      ? // a city typed before the list existed (D-098) is chosen again from the list
        {
          ...repeated,
          locality: citiesOf(repeated.region).includes(repeated.locality) ? repeated.locality : "",
        }
      : {
          offers: [],
          businessTypeId: "",
          latinoOnly: false,
          region: "",
          locality: "",
          radiusKm: null,
          wanted: 25,
        },
  );
  const { active, reload } = useSearchRuns();
  const [busy, setBusy] = useState(false);
  const [aiOff, setAiOff] = useState(false);
  useEffect(() => {
    void desktop
      .getAiStatus({ include_llm_fit: false })
      .then((status) => setAiOff(status.profiles.every((profile) => !profile.model)))
      .catch(() => undefined);
  }, []);

  const cities = useMemo(() => citiesOf(draft.region), [draft.region]);
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
      await reload();
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
      await reload();
    } catch (cause) {
      toast.error(toIpcError(cause).message);
    }
  };

  return (
    <PageShell>
      <PageScroll>
        <div className="mx-auto max-w-5xl space-y-6 p-6">
          <header>
            <h1 className="text-2xl font-semibold">{t("clientSearch.title")}</h1>
            <p className="mt-1 text-muted-foreground">{t("clientSearch.promise")}</p>
            {repeated && (
              <p className="mt-2 text-sm font-medium" role="status">
                {t(
                  repeated.offers.length > 0
                    ? "clientSearch.repeating"
                    : "clientSearch.repeatingNoOffer",
                )}
              </p>
            )}
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
                className={cn(SELECT, "mt-3")}
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
                  // the cities belong to the chosen state: an old one would search somewhere else
                  onChange={(event) =>
                    setDraft({ ...draft, region: event.target.value, locality: "" })
                  }
                  className={SELECT}
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
                <select
                  id="locality"
                  value={draft.locality}
                  disabled={cities.length === 0}
                  onChange={(event) => setDraft({ ...draft, locality: event.target.value })}
                  className={SELECT}
                >
                  <option value="">
                    {draft.region
                      ? t("clientSearch.chooseCity")
                      : t("clientSearch.chooseStateFirst")}
                  </option>
                  {cities.map((city) => (
                    <option key={city} value={city}>
                      {city}
                    </option>
                  ))}
                </select>
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
                  <h2 className="font-semibold">{t("clientSearch.yourSearch")}</h2>
                  <p className="text-xs text-muted-foreground">
                    {searchTitle(t, active.campaign_name)}
                  </p>
                  {active.status === "paused" && (
                    <p className="mt-1 text-sm font-medium">{t("clientSearch.paused")}</p>
                  )}
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
                  const failed = stage.status === "failed";
                  return (
                    <li
                      key={stage.task_id}
                      className={cn(
                        "flex justify-between rounded-md bg-muted/40 px-3 py-2 text-sm",
                        failed && "text-destructive",
                      )}
                    >
                      <span>
                        {aiSkipped
                          ? t("clientSearch.progress.aiSkipped")
                          : t(STAGE_MESSAGE[stage.stage])}
                      </span>
                      {!aiSkipped && (
                        <span className={cn(!failed && "text-muted-foreground")}>
                          {failed
                            ? t("clientSearch.progress.stopped")
                            : stage.skipped === "skipped_by_person"
                              ? t("clientSearch.progress.skipped")
                              : `${done}${total != null ? ` / ${total}` : ""}`}
                        </span>
                      )}
                    </li>
                  );
                })}
              </ol>
              {active.status === "failed" && (
                <div className="mt-4">
                  <SearchFailure run={active} onResolved={reload} />
                </div>
              )}
              {active.status === "succeeded" && (
                <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
                  <p className="font-medium">{t("clientSearch.done")}</p>
                  <Button asChild>
                    <a href={`#/mis-clientes?job=${active.job_id}`}>{t("clientSearch.openList")}</a>
                  </Button>
                </div>
              )}
            </section>
          )}
        </div>
      </PageScroll>
    </PageShell>
  );
}
