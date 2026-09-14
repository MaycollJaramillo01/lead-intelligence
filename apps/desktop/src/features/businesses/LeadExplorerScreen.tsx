import { useCallback, useDeferredValue, useMemo, useState } from "react";
import type {
  LeadScoreItem,
  LeadScorePage,
  LeadTier,
  ProblemType,
  ScoreRunItem,
} from "@lead/shared-types";
import { Link } from "react-router";
import { filterFieldsFrom } from "@/components/data/filters";
import { PagedFooter } from "@/components/data/PagedFooter";
import { StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { buildLeadColumns } from "@/features/businesses/columns";
import { TableWorkspace } from "@/features/businesses/TableWorkspace";
import { PROBLEMS, PROBLEM_KEYS, TIERS, TIER_KEYS } from "@/features/scoring/labels";
import { Distribution, TierLine } from "@/features/scoring/parts";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop } from "@/lib/ipc";
import { useKeysetPages } from "@/lib/useKeysetPages";

const PAGE = 200;
const SELECT = "h-7 rounded-md border bg-transparent px-2 text-xs";

/**
 * The ranked leads of the newest finished score run (phase 11): tier, total, confidence and the
 * components behind them, filtered in the store. The rank is each lead's place in the whole run,
 * whatever the filters; the inspector shows the business with its scorecard.
 */
export function LeadExplorerScreen() {
  const t = useT();
  const language = useLanguage();
  const columns = useMemo(() => buildLeadColumns(t), [t]);
  const filterFields = useMemo(() => filterFieldsFrom(columns), [columns]);
  const [tier, setTier] = useState<LeadTier | "">("");
  const [problem, setProblem] = useState<ProblemType | "">("");
  const [minScore, setMinScore] = useState("");
  const [search, setSearch] = useState("");
  const query = useDeferredValue(search.trim());
  const [run, setRun] = useState<ScoreRunItem | null>(null);

  const fetchPage = useCallback(
    async (cursor: string | null): Promise<LeadScorePage> => {
      const page = await desktop.listLeadScores({
        score_run_id: null,
        tier: tier || null,
        min_score: minScore === "" ? null : Number(minScore),
        problem: problem || null,
        search: query || null,
        limit: PAGE,
        cursor,
      });
      setRun(page.score_run);
      return page;
    },
    [tier, minScore, problem, query],
  );
  const { items, total, nextCursor, loading, error, loadMore, reload } =
    useKeysetPages<LeadScoreItem>(fetchPage, "list_lead_scores");

  const toolbar = (
    <div className="shrink-0 space-y-2 border-b p-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label={t("scoring.filterTier")}
          value={tier}
          onChange={(event) => setTier(event.target.value as LeadTier | "")}
          className={SELECT}
        >
          <option value="">{t("scoring.anyTier")}</option>
          {TIERS.map((value) => (
            <option key={value} value={value}>
              {t(TIER_KEYS[value])}
            </option>
          ))}
        </select>
        <select
          aria-label={t("scoring.filterProblem")}
          value={problem}
          onChange={(event) => setProblem(event.target.value as ProblemType | "")}
          className={SELECT}
        >
          <option value="">{t("scoring.anyProblem")}</option>
          {PROBLEMS.map((value) => (
            <option key={value} value={value}>
              {t(PROBLEM_KEYS[value])}
            </option>
          ))}
        </select>
        <Input
          type="number"
          min={0}
          max={100}
          aria-label={t("scoring.minScore")}
          placeholder={t("scoring.minScore")}
          value={minScore}
          onChange={(event) => setMinScore(event.target.value)}
          className="h-7 w-28 text-xs"
        />
        <Input
          type="search"
          aria-label={t("scoring.search")}
          placeholder={t("scoring.search")}
          value={search}
          maxLength={200}
          onChange={(event) => setSearch(event.target.value)}
          className="h-7 w-48 text-xs"
        />
      </div>
      {run && (
        <div className="flex flex-wrap items-start gap-4">
          <div className="space-y-0.5">
            <p className="font-medium">
              {t("scoring.runLine", {
                icp: run.icp_profile_name,
                icpVersion: run.icp_profile_version,
                model: run.scoring_model_name,
                modelVersion: run.scoring_model_version,
              })}
            </p>
            <p className="text-[11px] text-muted-foreground">
              {run.finished_at
                ? t("scoring.finishedAt", { time: formatDateTime(run.finished_at, language) })
                : t("scoring.runOpen")}
            </p>
            <TierLine counts={run.stats} />
          </div>
          <div className="min-w-56 flex-1">
            <Distribution bins={run.distribution} label={t("scoring.distribution")} />
          </div>
        </div>
      )}
    </div>
  );

  const emptyState = error ? (
    <StateBlock
      variant={error.code === "desktop_unavailable" ? "offline" : "error"}
      title={t("leads.errorTitle")}
      body={error.message}
      action={
        <Button variant="outline" size="sm" onClick={() => void reload()}>
          {t("common.retry")}
        </Button>
      }
    />
  ) : (
    <StateBlock
      variant="empty"
      title={t("leads.emptyTitle")}
      body={t("leads.emptyBody")}
      action={
        <Link to="/scoring-models" className="text-xs text-primary hover:underline">
          {t("nav.scoring_models")}
        </Link>
      }
    />
  );

  return (
    <TableWorkspace
      tableId="lead-explorer"
      columns={columns}
      data={items}
      getRowId={(row) => row.business_id}
      filterFields={filterFields}
      emptyState={emptyState}
      isLoading={loading && items.length === 0 && error === null}
      toolbar={toolbar}
      footer={
        <PagedFooter
          loaded={items.length}
          total={total}
          hasMore={nextCursor !== null}
          loading={loading}
          onLoadMore={() => void loadMore()}
        />
      }
      pinnedLeft={["rank", "name"]}
    />
  );
}
