import type { PipelineRun } from "@lead/shared-types";
import { Link, useNavigate } from "react-router";
import { toast } from "sonner";
import { PageScroll, PageShell } from "@/components/layout/page-chrome";
import { Button } from "@/components/ui/button";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { searchTitle } from "./model";
import { useSearchDetails, useSearchRuns, type SearchDetails } from "./useSearches";

const IN_PROGRESS: ReadonlySet<string> = new Set(["queued", "running"]);

/** Every search of the simple mode: when, what, where, how many clients and how it ended (phase 16). */
export function ClientSearchesScreen() {
  const t = useT();
  const language = useLanguage();
  const navigate = useNavigate();
  const { runs, reload } = useSearchRuns();
  const detailsOf = useSearchDetails(runs);

  const resume = async (run: PipelineRun) => {
    try {
      await desktop.controlPipeline({ job_id: run.job_id, action: "resume" });
      await reload();
      toast.success(t("clientSearches.resumed"));
    } catch (cause) {
      toast.error(toIpcError(cause, "search").message);
    }
  };

  /** What the search sold, in the words of the form that asked for it. */
  const sells = (details: SearchDetails | null) =>
    details?.draft?.offers.map((offer) => t(`clientSearch.offer.${offer}`)).join(", ");

  return (
    <PageShell>
      <PageScroll>
        <div className="mx-auto max-w-5xl space-y-6 p-6">
          <header>
            <h1 className="text-2xl font-semibold">{t("clientSearch.history")}</h1>
            <p className="mt-1 text-muted-foreground">{t("desc.client_searches")}</p>
          </header>
          <section className="rounded-xl border bg-card p-5">
            {runs.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("clientSearch.noHistory")}</p>
            ) : (
              <ul className="divide-y">
                {runs.map((run) => {
                  const details = detailsOf(run);
                  const sold = sells(details);
                  const clients = details?.clients;
                  const meta = [
                    formatDateTime(run.requested_at, language),
                    t(`jobStatus.${run.status}`),
                    clients != null &&
                      (clients === 1
                        ? t("clientSearches.oneClient")
                        : t("clientSearches.clients", { count: clients })),
                  ].filter(Boolean);
                  return (
                    <li
                      key={run.job_id}
                      className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm"
                    >
                      <div className="min-w-0 space-y-0.5">
                        <p className="font-medium">{searchTitle(t, run.campaign_name)}</p>
                        {sold && <p>{t("clientSearches.sells", { offers: sold })}</p>}
                        <p className="text-xs text-muted-foreground">{meta.join(" · ")}</p>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {run.status === "succeeded" && (
                          <Button asChild variant="outline">
                            <Link to={`/mis-clientes?job=${run.job_id}`}>
                              {t("clientSearch.open")}
                            </Link>
                          </Button>
                        )}
                        {run.actions.includes("resume") && (
                          <Button onClick={() => void resume(run)}>
                            {t("clientSearch.continue")}
                          </Button>
                        )}
                        {IN_PROGRESS.has(run.status) && (
                          <Button asChild variant="outline">
                            <Link to="/buscar-clientes">{t("clientSearches.progress")}</Link>
                          </Button>
                        )}
                        {!IN_PROGRESS.has(run.status) &&
                          run.status !== "paused" &&
                          details?.draft && (
                            <Button
                              variant="outline"
                              onClick={() =>
                                void navigate("/buscar-clientes", {
                                  state: { repeat: details.draft },
                                })
                              }
                            >
                              {t("clientSearches.repeat")}
                            </Button>
                          )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </div>
      </PageScroll>
    </PageShell>
  );
}
