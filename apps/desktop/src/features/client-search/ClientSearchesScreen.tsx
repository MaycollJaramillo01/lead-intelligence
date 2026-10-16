import type { PipelineRun } from "@lead/shared-types";
import { ArrowLeft, Folder } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { toast } from "sonner";
import { PageScroll, PageShell } from "@/components/layout/page-chrome";
import { Button } from "@/components/ui/button";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { SEARCH_FOLDERS, searchTitle, soldNames } from "./model";
import { SearchFailure } from "./SearchFailure";
import { useSearchDetails, useSearchRuns, type SearchDetails } from "./useSearches";

const IN_PROGRESS: ReadonlySet<string> = new Set(["queued", "running"]);

/** Every search of the simple mode: when, what, where, how many clients and how it ended (phase 16). */
export function ClientSearchesScreen() {
  const t = useT();
  const language = useLanguage();
  const navigate = useNavigate();
  const { runs, reload } = useSearchRuns();
  const detailsOf = useSearchDetails(runs);
  const [openFolder, setOpenFolder] = useState<string | null>(null);
  const folderOf = (run: PipelineRun) =>
    SEARCH_FOLDERS.find((name) => name.toLowerCase() === run.project_name.trim().toLowerCase()) ??
    "other";
  const folders = [...SEARCH_FOLDERS, "other"]
    .map((key) => ({
      key,
      name: key === "other" ? t("clientSearches.otherFolder") : key,
      runs: runs.filter((run) => folderOf(run) === key),
    }))
    .filter((folder) => folder.key !== "other" || folder.runs.length > 0);
  const selected = folders.find((folder) => folder.key === openFolder);
  const otherSearches = folders.find((folder) => folder.key === "other");
  const explorations = (count: number) =>
    count === 1 ? t("clientSearches.oneExploration") : t("clientSearches.explorations", { count });

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
    details?.draft && soldNames(t, details.draft).join(", ");

  return (
    <PageShell>
      <PageScroll>
        <div className="mx-auto max-w-5xl space-y-6 p-6">
          <header>
            <h1 className="text-2xl font-semibold">{t("clientSearch.history")}</h1>
            <p className="mt-1 text-muted-foreground">{t("clientSearches.folderDescription")}</p>
          </header>
          {!selected ? (
            <>
              <div className="grid gap-4 sm:grid-cols-3">
                {folders
                  .filter((folder) => folder.key !== "other")
                  .map((folder) => (
                    <button
                      key={folder.key}
                      type="button"
                      aria-label={t("clientSearches.openFolder", {
                        name: folder.name,
                        explorations: explorations(folder.runs.length),
                      })}
                      onClick={() => setOpenFolder(folder.key)}
                      className="flex items-center gap-4 rounded-xl border bg-card p-5 text-left transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <Folder className="size-7 text-muted-foreground" aria-hidden="true" />
                      <span className="min-w-0">
                        <span className="block font-semibold">{folder.name}</span>
                        <span className="mt-1 block text-sm text-muted-foreground">
                          {explorations(folder.runs.length)}
                        </span>
                      </span>
                    </button>
                  ))}
              </div>
              {otherSearches && (
                <Button variant="ghost" onClick={() => setOpenFolder("other")}>
                  {t("clientSearches.openFolder", {
                    name: otherSearches.name,
                    explorations: explorations(otherSearches.runs.length),
                  })}
                </Button>
              )}
            </>
          ) : (
            <section className="rounded-xl border bg-card p-5">
              <div className="mb-4 flex flex-wrap items-center gap-3">
                <Button variant="ghost" onClick={() => setOpenFolder(null)}>
                  <ArrowLeft aria-hidden="true" />
                  {t("clientSearches.allFolders")}
                </Button>
                <div>
                  <h2 className="text-lg font-semibold">{selected.name}</h2>
                  <p className="text-sm text-muted-foreground">
                    {explorations(selected.runs.length)}
                  </p>
                </div>
              </div>
              {selected.runs.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t("clientSearch.noHistory")}</p>
              ) : (
                <ul className="divide-y">
                  {selected.runs.map((run) => {
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
                              <Link to={`/buscar-clientes?job=${run.job_id}`}>
                                {t("clientSearches.progress")}
                              </Link>
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
                        {run.status === "failed" && (
                          <div className="basis-full">
                            <SearchFailure run={run} onResolved={reload} />
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
          )}
        </div>
      </PageScroll>
    </PageShell>
  );
}
