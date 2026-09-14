import { useCallback } from "react";
import type { ExplorerField } from "@lead/shared-types";
import { TestDataBadge } from "@/components/data/cells";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { FIELD_KEYS, cellText } from "@/features/leads/labels";
import { useT } from "@/lib/i18n";
import { desktop } from "@/lib/ipc";
import { useLoaded } from "@/lib/useLoaded";

const COMPARED: readonly ExplorerField[] = [
  "tier",
  "total_score",
  "confidence",
  "data_coverage",
  "icp_match",
  "opportunity_score",
  "digital_gap",
  "business_health",
  "buying_capacity",
  "contactability",
  "commercial_intent",
  "data_confidence",
  "latino_status",
  "latino_confidence",
  "main_channel",
  "channel_status",
  "usable_channels",
  "top_problem",
  "offer",
  "research_status",
  "lead_stage",
  "facts_age_days",
  "conflicts",
  "suppressed",
  "stale",
  "partial",
];

interface CompareDialogProps {
  onOpenChange: (open: boolean) => void;
  ids: readonly string[];
  scoreRunId: string | null;
}

/** Two to four leads of one score run side by side (phase 12), with their top reasons and risks. */
export function CompareDialog({ onOpenChange, ids, scoreRunId }: CompareDialogProps) {
  const t = useT();
  const key = ids.join(",");
  const load = useCallback(
    () => desktop.compareLeads({ business_ids: key.split(","), score_run_id: scoreRunId }),
    [key, scoreRunId],
  );
  const { data, error } = useLoaded(load, "compare_leads");

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-auto sm:max-w-5xl">
        <DialogHeader>
          <DialogTitle>{t("compare.title")}</DialogTitle>
          <DialogDescription>{t("compare.help")}</DialogDescription>
        </DialogHeader>
        {error ? (
          <StateBlock
            variant={error.code === "desktop_unavailable" ? "offline" : "error"}
            title={t("compare.failed")}
            body={error.message}
          />
        ) : data === null ? (
          <StateBlock variant="loading" title={t("state.loading")} />
        ) : (
          <div className="space-y-2 text-xs">
            <p className="text-[11px] text-muted-foreground">
              {t("scoring.runLine", {
                icp: data.score_run.icp_profile_name,
                icpVersion: data.score_run.icp_profile_version,
                model: data.score_run.scoring_model_name,
                modelVersion: data.score_run.scoring_model_version,
              })}
            </p>
            {data.missing.length > 0 && (
              <InlineNotice variant="partial">
                {t("compare.missing", { n: data.missing.length })}
              </InlineNotice>
            )}
            <div className="overflow-auto rounded-md border">
              <table className="w-full text-[11px]">
                <thead>
                  <tr>
                    <th
                      scope="col"
                      className="px-1.5 py-1 text-left font-medium text-muted-foreground"
                    >
                      {t("filter.field")}
                    </th>
                    {data.leads.map((lead) => (
                      <th key={lead.row.business_id} scope="col" className="px-1.5 py-1 text-left">
                        <span className="flex flex-wrap items-center gap-1 font-medium">
                          {lead.row.display_name}
                          {lead.row.is_synthetic && <TestDataBadge />}
                        </span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {COMPARED.map((field) => (
                    <tr key={field} className="border-t">
                      <th
                        scope="row"
                        className="px-1.5 py-0.5 text-left font-normal text-muted-foreground"
                      >
                        {t(FIELD_KEYS[field])}
                      </th>
                      {data.leads.map((lead) => (
                        <td key={lead.row.business_id} className="px-1.5 py-0.5">
                          {cellText(t, lead.row, field) ?? "—"}
                        </td>
                      ))}
                    </tr>
                  ))}
                  <tr className="border-t align-top">
                    <th
                      scope="row"
                      className="px-1.5 py-0.5 text-left font-normal text-muted-foreground"
                    >
                      {t("scoring.reasons")}
                    </th>
                    {data.leads.map((lead) => (
                      <td key={lead.row.business_id} className="px-1.5 py-0.5">
                        {lead.scorecard.reasons.length === 0 ? (
                          "—"
                        ) : (
                          <ol className="list-decimal space-y-0.5 pl-4">
                            {lead.scorecard.reasons.slice(0, 3).map((reason) => (
                              <li key={reason.feature}>{reason.text}</li>
                            ))}
                          </ol>
                        )}
                      </td>
                    ))}
                  </tr>
                  <tr className="border-t align-top">
                    <th
                      scope="row"
                      className="px-1.5 py-0.5 text-left font-normal text-muted-foreground"
                    >
                      {t("scoring.risks")}
                    </th>
                    {data.leads.map((lead) => (
                      <td key={lead.row.business_id} className="px-1.5 py-0.5">
                        {lead.scorecard.risks.length === 0 ? (
                          "—"
                        ) : (
                          <ul className="space-y-0.5">
                            {lead.scorecard.risks.map((risk, index) => (
                              <li key={`${risk.code}:${index}`}>{risk.detail}</li>
                            ))}
                          </ul>
                        )}
                      </td>
                    ))}
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
