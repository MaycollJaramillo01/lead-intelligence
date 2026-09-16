import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import type { ExplorerQuery, ExplorerRow, PipelineRun } from "@lead/shared-types";
import { desktop, toIpcError } from "@/lib/ipc";
import { clientListQuery, contactOf } from "./model";

export const POLL_MS = 2_000;
const RUNNING = ["queued", "running", "paused"];

/** The searches of the simple mode, newest first, polled while one of them is running. */
export function useSearchRuns() {
  const [runs, setRuns] = useState<PipelineRun[]>([]);
  const reload = useCallback(async () => {
    try {
      const page = await desktop.listPipelines({ project_id: null, limit: 50, cursor: null });
      setRuns(page.items.filter((run) => run.campaign_name?.startsWith("Buscar clientes ·")));
    } catch {
      /* diagnostics already explains an unavailable worker */
    }
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => void reload(), 0);
    return () => window.clearTimeout(timer);
  }, [reload]);
  useEffect(() => {
    if (!runs.some((run) => ["queued", "running"].includes(run.status))) return;
    const timer = window.setInterval(() => void reload(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [reload, runs]);
  return { runs, active: runs.find((run) => RUNNING.includes(run.status)) ?? runs[0], reload };
}

export interface ClientList {
  jobId: string;
  /** The clients of the search, as the file downloads them: every match, not only `rows`. */
  query: ExplorerQuery;
  /** Every client of the search; `rows` holds the first of them. */
  total: number;
  /** Every lead of the search, whatever its tier. */
  found: number;
  rows: { row: ExplorerRow; contact: string | null }[];
}

/** The clients of a finished search: what the screen lists and what its file exports. */
export function useClientList(finished: PipelineRun | null): ClientList | null {
  const [clients, setClients] = useState<ClientList | null>(null);
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
  return finished && clients?.jobId === finished.job_id ? clients : null;
}
