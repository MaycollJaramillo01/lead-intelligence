import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import type { ExplorerQuery, ExplorerRow, PipelineRun } from "@lead/shared-types";
import { desktop, toIpcError } from "@/lib/ipc";
import {
  clientListQuery,
  contactsOf,
  draftOf,
  NO_FILTERS,
  offersOf,
  type ClientContacts,
  type ClientFilters,
  type ClientSearchDraft,
} from "./model";

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

export interface ClientRowData {
  row: ExplorerRow;
  contacts: ClientContacts;
}

export interface ClientList {
  /** The search and filters the list answers; a newer answer replaces it. */
  key: string;
  /** The clients of the search, as the file downloads them: every match, not only `rows`. */
  query: ExplorerQuery;
  /** Every client the filters keep; `rows` holds the first of them. */
  total: number;
  /** Every lead the search found, whatever its tier. */
  found: number;
  rows: ClientRowData[];
}

const NO_CONTACTS: ClientContacts = { phone: null, email: null };

/**
 * The clients of a finished search under the chosen filters: what the screen lists and what its
 * file exports. `reload` reads them again after something changed one of them (a stage, the
 * do-not-contact list); a client's contacts are read once.
 */
export function useClientList(finished: PipelineRun | null, filters: ClientFilters = NO_FILTERS) {
  const [clients, setClients] = useState<ClientList | null>(null);
  const known = useRef(new Map<string, ClientContacts>());
  const jobId = finished?.job_id ?? null;
  const scoreRunId = finished?.score_run_id ?? null;
  const campaignId = finished?.campaign_id ?? null;
  const campaignName = finished?.campaign_name ?? null;
  const { phone, email, callFirst } = filters;
  const key = `${jobId}:${phone}:${email}:${callFirst}`;

  /** Reads the list; an answer that arrives after the filters changed is never shown. */
  const load = useCallback(async (): Promise<ClientList | null> => {
    if (!campaignId) return null;
    const search = { campaign_id: campaignId, campaign_name: campaignName };
    const query = clientListQuery(scoreRunId, search, { phone, email, callFirst });
    const [page, found] = await Promise.all([
      desktop.exploreLeads(query),
      desktop.exploreLeads(clientListQuery(scoreRunId, search, NO_FILTERS, true)),
    ]);
    const rows = await Promise.all(
      page.rows.map(async (row) => {
        let contacts = known.current.get(row.business_id);
        if (!contacts) {
          const detail = await desktop
            .getBusiness({ business_id: row.business_id })
            .catch(() => null);
          contacts = detail ? contactsOf(detail.contacts.map((c) => c.value)) : NO_CONTACTS;
          if (detail) known.current.set(row.business_id, contacts);
        }
        return { row, contacts };
      }),
    );
    return { key, query, total: page.total ?? rows.length, found: found.total ?? 0, rows };
  }, [key, scoreRunId, campaignId, campaignName, phone, email, callFirst]);

  const show = useCallback(
    (reading: Promise<ClientList | null>, stale: () => boolean = () => false) =>
      reading.then(
        (list) => {
          if (list && !stale()) setClients(list);
        },
        (cause: unknown) => {
          if (!stale()) toast.error(toIpcError(cause, "client list").message);
        },
      ),
    [],
  );

  useEffect(() => {
    let cancelled = false;
    void show(load(), () => cancelled);
    return () => {
      cancelled = true;
    };
  }, [load, show]);

  /** Reads the list again after something changed one of its clients (a stage, do not contact). */
  const reload = useCallback(() => void show(load()), [load, show]);
  return { list: clients?.key === key ? clients : null, reload };
}

export interface SearchDetails {
  /** The clients a finished search left; null until it finishes. */
  clients: number | null;
  /** The search's form, to run it again; null when its name is not one this screen gave. */
  draft: ClientSearchDraft | null;
}

/** A run changes what it shows only when its status or its score run does. */
const detailKey = (run: PipelineRun) => `${run.job_id}:${run.status}:${run.score_run_id}`;

/**
 * What each search asked for and how many clients it left, read once per run and status: the list
 * of searches is polled while one runs, and nothing here changes between two polls.
 */
export function useSearchDetails(runs: readonly PipelineRun[]) {
  const [details, setDetails] = useState<ReadonlyMap<string, SearchDetails>>(new Map());
  const known = useRef(new Map<string, SearchDetails>());

  useEffect(() => {
    const missing = runs.filter((run) => !known.current.has(detailKey(run)));
    if (missing.length === 0) return;
    // ponytail: no cancelling: the cache only grows, so a late answer is still a right one
    void (async () => {
      const projects = [...new Set(missing.map((run) => run.project_id))];
      const [campaigns, catalogs] = await Promise.all([
        desktop.listCampaigns({ project_id: null, limit: 100, cursor: null }),
        Promise.all(projects.map((id) => desktop.getScoringCatalog({ project_id: id }))),
      ]);
      const versions = catalogs.flatMap((catalog) =>
        catalog.icp_profiles.flatMap((profile) => profile.versions),
      );
      await Promise.all(
        missing.map(async (run) => {
          const campaign = campaigns.items.find((item) => item.id === run.campaign_id);
          let clients: number | null = null;
          let offers: ReturnType<typeof offersOf> = [];
          if (run.status === "succeeded" && run.campaign_id && run.score_run_id) {
            const search = { campaign_id: run.campaign_id, campaign_name: run.campaign_name };
            const page = await desktop.exploreLeads({
              ...clientListQuery(run.score_run_id, search),
              limit: 1,
            });
            clients = page.total ?? 0;
            // the offer profile version the search was scored with says what it was selling
            const scored = page.score_run;
            const version = versions.find(
              (item) =>
                item.icp_profile_id === scored?.icp_profile_id &&
                item.version === scored.icp_profile_version,
            );
            offers = offersOf(version?.definition_json);
          }
          known.current.set(detailKey(run), {
            clients,
            draft: draftOf(run.campaign_name, campaign?.criteria, offers),
          });
        }),
      );
      setDetails(new Map(known.current));
    })().catch((cause: unknown) => toast.error(toIpcError(cause, "search history").message));
  }, [runs]);

  return (run: PipelineRun) => details.get(detailKey(run)) ?? null;
}
