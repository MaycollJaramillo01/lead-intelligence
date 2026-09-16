import { useState } from "react";
import { Download } from "lucide-react";
import { useSearchParams } from "react-router";
import { toast } from "sonner";
import { PageScroll, PageShell } from "@/components/layout/page-chrome";
import { Button } from "@/components/ui/button";
import { PROBLEM_KEYS } from "@/features/scoring/labels";
import { useT } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { clientExportDraft, latinoFirst, offerOf } from "./model";
import { POLL_MS, useClientList, useSearchRuns } from "./useSearches";

const WRITTEN: ReadonlySet<string> = new Set(["succeeded", "failed", "cancelled"]);

/** The clients one search found: who to call, on what number and what to offer (phase 16). */
export function ClientListScreen() {
  const t = useT();
  const [params] = useSearchParams();
  const { runs } = useSearchRuns();
  const [busy, setBusy] = useState(false);
  const job = params.get("job");
  const finished =
    runs.find((run) => run.status === "succeeded" && (job === null || run.job_id === job)) ?? null;
  const list = useClientList(finished);
  const latino = latinoFirst(finished?.campaign_name ?? null);

  /** Writes the whole list to the file the save dialog names; the page never sees a path. */
  const download = async () => {
    if (!list) return;
    setBusy(true);
    try {
      const file = clientExportDraft(list.query, (key) => t(`clientSearch.column.${key}`));
      const destination = await desktop.chooseExportDestination(
        "csv",
        (finished?.campaign_name ?? t("clientSearch.results")).replaceAll(" · ", " - "),
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
              {finished?.campaign_name ?? t("desc.client_list")}
            </p>
          </header>

          {finished === null ? (
            <section className="rounded-xl border bg-card p-5">
              <p className="text-sm text-muted-foreground">{t("clientList.empty")}</p>
              <Button asChild className="mt-3">
                <a href="#/buscar-clientes">{t("clientSearch.button")}</a>
              </Button>
            </section>
          ) : list === null ? (
            <p className="text-sm text-muted-foreground">{t("clientSearch.loading")}</p>
          ) : (
            <section className="space-y-3 rounded-xl border bg-card p-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="font-medium">{t("clientSearch.ready", { count: list.total })}</p>
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
                        t("clientSearch.offerIt", { offer: t(`clientSearch.offer.${offer}`) }),
                      latino && row.latino_status !== "unknown" && t("clientSearch.latinoEvidence"),
                    ].filter(Boolean);
                    return (
                      <li
                        key={row.business_id}
                        className="flex flex-wrap items-start justify-between gap-2 px-3 py-2 text-sm"
                      >
                        <div className="min-w-0">
                          <p className="font-medium">{row.display_name}</p>
                          <p className="text-xs text-muted-foreground">{details.join(" · ")}</p>
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
            </section>
          )}
        </div>
      </PageScroll>
    </PageShell>
  );
}
