import { PageScroll, PageShell } from "@/components/layout/page-chrome";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";
import { useSearchRuns } from "./useSearches";

/** Every search of the simple mode and what happened to it (phase 16). */
export function ClientSearchesScreen() {
  const t = useT();
  const { runs } = useSearchRuns();

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
                {runs.map((run) => (
                  <li
                    key={run.job_id}
                    className="flex flex-wrap items-center justify-between gap-3 py-2 text-sm"
                  >
                    <span className="min-w-0">{run.campaign_name}</span>
                    <span className="flex items-center gap-3">
                      <span className="text-muted-foreground">{t(`jobStatus.${run.status}`)}</span>
                      {run.status === "succeeded" && (
                        <Button asChild variant="outline">
                          <a href={`#/mis-clientes?job=${run.job_id}`}>{t("clientSearch.open")}</a>
                        </Button>
                      )}
                    </span>
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
