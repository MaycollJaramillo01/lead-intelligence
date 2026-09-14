import { useEffect, useState, type FormEvent } from "react";
import type {
  LeadAction,
  LeadScorecardDetail,
  LeadStage,
  ScoreEvidence,
  ScoreFeature,
} from "@lead/shared-types";
import { toast } from "sonner";
import { TestDataBadge } from "@/components/data/cells";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  ACTION_KEYS,
  COMPONENT_KEYS,
  FEATURE_STATE_KEYS,
  GATE_OUTCOME_KEYS,
  LEAD_ACTIONS,
  LEAD_STAGES,
  PROBLEM_KEYS,
  STAGE_KEYS,
  TIER_KEYS,
  codeLabel,
  percent,
  scoreText,
} from "@/features/scoring/labels";
import { Heading } from "@/features/scoring/parts";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop, toIpcError, type IpcError } from "@/lib/ipc";
import { cn } from "@/lib/utils";

type Loaded = { detail: LeadScorecardDetail | null; error: IpcError | null } | null;

const SELECT = "h-7 w-full rounded-md border bg-transparent px-2 text-xs";

function Evidence({ items }: { items: readonly ScoreEvidence[] }) {
  if (items.length === 0) return null;
  return (
    <ul className="mt-0.5 flex flex-wrap gap-1">
      {items.map((item, index) => (
        <li
          key={`${item.kind}:${item.ref}:${index}`}
          title={item.url ?? item.ref}
          className="max-w-full truncate rounded border px-1 font-mono text-[10px] text-muted-foreground"
        >
          {item.label}
        </li>
      ))}
    </ul>
  );
}

function FeatureRow({ feature }: { feature: ScoreFeature }) {
  const t = useT();
  return (
    <li className={cn("rounded border p-1.5", feature.state !== "known" && "border-dashed")}>
      <p className="flex flex-wrap items-baseline gap-x-2">
        <span className="font-medium">{codeLabel(t, feature.feature)}</span>
        <span className="font-mono text-[10px] text-muted-foreground">{feature.feature}</span>
      </p>
      <p className="text-[11px] text-muted-foreground">
        {t("scoring.featureLine", {
          state: t(FEATURE_STATE_KEYS[feature.state]),
          value: feature.value === null ? "—" : percent(feature.value),
          weight: feature.weight,
          points: scoreText(feature.points),
        })}
      </p>
      <p className="text-[11px] break-words">{feature.detail}</p>
      <Evidence items={feature.evidence} />
    </li>
  );
}

/**
 * The scorecard of a lead (phase 11): its tier, total, confidence and versions; the gates decided
 * before any weight; the top reasons, reasons to contact now, the offer and the probable impact
 * (labelled as inference); risks and missing data; every component and feature with its weight,
 * contribution and evidence; the message draft; a local model's wording, checked; and a review or
 * outcome that needs a reason. Nothing here changes a score or contacts anyone.
 */
export function ScorecardPanel({
  businessId,
  scoreRunId = null,
}: {
  businessId: string;
  scoreRunId?: string | null;
}) {
  const t = useT();
  const language = useLanguage();
  const [loaded, setLoaded] = useState<Loaded>(null);
  const [action, setAction] = useState<LeadAction>("confirm");
  const [stage, setStage] = useState<LeadStage>("attempted");
  const [lostReason, setLostReason] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let current = true;
    desktop.getLeadScorecard({ business_id: businessId, score_run_id: scoreRunId }).then(
      (detail) => current && setLoaded({ detail, error: null }),
      (cause: unknown) =>
        current && setLoaded({ detail: null, error: toIpcError(cause, "get_lead_scorecard") }),
    );
    return () => {
      current = false;
    };
  }, [businessId, scoreRunId]);

  if (loaded === null) return <StateBlock variant="loading" title={t("state.loading")} />;
  if (loaded.detail === null) {
    return loaded.error?.code === "not_found" ? (
      <section aria-label={t("scoring.panelTitle")} className="space-y-1 text-xs">
        <Heading>{t("scoring.panelTitle")}</Heading>
        <p className="text-muted-foreground">{t("scoring.notScored")}</p>
      </section>
    ) : (
      <StateBlock
        variant="error"
        title={t("scoring.loadErrorTitle")}
        body={loaded.error?.message}
      />
    );
  }

  const detail = loaded.detail;
  const card = detail.scorecard;
  const run = detail.score_run;
  const recording = action === "record_outcome";
  const needsLostReason = recording && stage === "lost";
  const formulas = card.components.filter(
    (view) => view.component === "OPPORTUNITY_SCORE" || view.component === "FINAL_LEAD_SCORE",
  );

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!reason.trim() || (needsLostReason && !lostReason.trim())) return;
    setBusy(true);
    try {
      const updated = await desktop.actOnLead({
        score_run_id: run.score_run_id,
        business_id: detail.business_id,
        action,
        stage: recording ? stage : null,
        lost_reason: needsLostReason ? lostReason.trim() : null,
        reason: reason.trim(),
      });
      setLoaded({ detail: updated, error: null });
      setReason("");
      setLostReason("");
      toast.success(t("scoring.actionDone"));
    } catch (cause) {
      toast.error(t("scoring.actionFailed"), {
        description: toIpcError(cause, "act_on_lead").message,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-label={t("scoring.panelTitle")} className="space-y-3 text-xs">
      <div className="flex items-center justify-between gap-2">
        <Heading>{t("scoring.panelTitle")}</Heading>
        {detail.is_synthetic && <TestDataBadge />}
      </div>

      <div className="space-y-1 rounded-md border p-2">
        <p className="flex flex-wrap items-baseline gap-2">
          <span className="font-mono text-lg leading-none tabular-nums">
            {scoreText(card.total_score)}
          </span>
          <span className="text-muted-foreground">/100</span>
          <span className="rounded border px-1 font-medium">{t(TIER_KEYS[card.tier])}</span>
          <span className="text-[11px] text-muted-foreground">
            {t("scoring.rank", { rank: detail.rank, total: run.stats.businesses })}
          </span>
        </p>
        <p className="text-[11px] text-muted-foreground">
          {t("scoring.confidenceLine", {
            confidence: percent(card.confidence),
            known: percent(card.known_weight),
          })}
        </p>
        <p className="font-mono text-[11px] break-all text-muted-foreground">
          {t("scoring.versionsLine", {
            model: card.versions.scoring_model_key,
            modelVersion: card.versions.scoring_model_version,
            icp: card.versions.icp_profile_name,
            icpVersion: card.versions.icp_profile_version,
            engine: card.versions.engine_version,
          })}
        </p>
        {card.versions.is_heuristic && (
          <InlineNotice variant="partial">{t("scoring.heuristicNotice")}</InlineNotice>
        )}
      </div>

      <div className="space-y-1">
        <Heading>{t("scoring.gates")}</Heading>
        <ul className="space-y-0.5">
          {card.gates.map((gate) => (
            <li
              key={gate.gate}
              className={cn(
                "rounded border px-1.5 py-0.5",
                gate.outcome === "fail" && "border-danger/60",
                gate.outcome === "unknown" && "border-warn/60",
              )}
            >
              <span className="font-medium">{t(GATE_OUTCOME_KEYS[gate.outcome])}</span>
              {` · ${codeLabel(t, gate.gate)} · `}
              <span className="text-muted-foreground">{gate.detail}</span>
            </li>
          ))}
        </ul>
      </div>

      {card.reasons.length > 0 && (
        <div className="space-y-1">
          <Heading>{t("scoring.reasons")}</Heading>
          <ol className="list-decimal space-y-1 pl-4">
            {card.reasons.map((item) => (
              <li key={item.feature}>
                {item.text}{" "}
                <span className="font-mono text-[11px] text-muted-foreground">
                  +{item.impact.toFixed(1)}
                </span>
                <Evidence items={item.evidence} />
              </li>
            ))}
          </ol>
        </div>
      )}
      {card.contact_now.length > 0 && (
        <div className="space-y-1">
          <Heading>{t("scoring.contactNow")}</Heading>
          <ul className="space-y-1">
            {card.contact_now.map((item) => (
              <li key={item.feature}>
                {item.text}
                <Evidence items={item.evidence} />
              </li>
            ))}
          </ul>
        </div>
      )}
      {card.offer && (
        <div className="space-y-1">
          <Heading>{t("scoring.offer")}</Heading>
          <p className="font-medium">{card.offer.offer_name}</p>
          <p className="text-[11px]">{card.offer.why}</p>
          <p className="text-[11px] text-muted-foreground">
            {card.offer.problems.map((problem) => t(PROBLEM_KEYS[problem])).join(" · ")}
          </p>
          <Evidence items={card.offer.evidence} />
        </div>
      )}
      {card.impacts.length > 0 && (
        <div className="space-y-1">
          <Heading>{t("scoring.impacts")}</Heading>
          <ul className="list-disc space-y-0.5 pl-4">
            {card.impacts.map((impact) => (
              <li key={impact.problem}>
                {impact.text}{" "}
                <span className="rounded border px-1 text-[10px] text-muted-foreground">
                  {t("scoring.inference")}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {card.risks.length > 0 && (
        <div className="space-y-1">
          <Heading>{t("scoring.risks")}</Heading>
          <ul className="space-y-0.5">
            {card.risks.map((risk, index) => (
              <li
                key={`${risk.code}:${index}`}
                className="rounded border border-warn/60 px-1.5 py-0.5"
              >
                <span className="font-medium">{codeLabel(t, risk.code)}</span>
                {` · ${risk.detail}`}
              </li>
            ))}
          </ul>
        </div>
      )}
      {card.missing.length > 0 && (
        <div className="space-y-1">
          <Heading>{t("scoring.missing")}</Heading>
          <ul className="space-y-0.5 text-[11px]">
            {card.missing.map((item) => (
              <li key={item.feature}>
                <span className="font-medium">{codeLabel(t, item.feature)}</span>
                {` · ${t("scoring.missingLine", { detail: item.detail, share: percent(item.weight_share) })}`}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="space-y-1">
        <Heading>{t("scoring.components")}</Heading>
        <div className="overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="text-left text-muted-foreground">
                <th className="font-normal">{t("scoring.component")}</th>
                <th className="text-right font-normal">{t("scoring.value")}</th>
                <th className="text-right font-normal">{t("scoring.weight")}</th>
                <th className="text-right font-normal">{t("scoring.contribution")}</th>
                <th className="text-right font-normal">{t("scoring.coverage")}</th>
              </tr>
            </thead>
            <tbody>
              {card.components.map((view) => (
                <tr key={view.component} className="border-t" title={view.formula}>
                  <td className="py-0.5">{t(COMPONENT_KEYS[view.component])}</td>
                  <td className="text-right font-mono tabular-nums">{scoreText(view.value)}</td>
                  <td className="text-right font-mono tabular-nums">{view.weight}</td>
                  <td className="text-right font-mono tabular-nums">
                    {scoreText(view.contribution)}
                  </td>
                  <td className="text-right font-mono tabular-nums">{percent(view.coverage)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {formulas.map((view) => (
          <p
            key={view.component}
            className="font-mono text-[10px] break-words text-muted-foreground"
          >
            {view.formula}
          </p>
        ))}
      </div>

      <details>
        <summary className="cursor-pointer text-[11px] text-muted-foreground">
          {t("scoring.features", { n: card.features.length })}
        </summary>
        <ul className="mt-1 space-y-1">
          {card.features.map((feature) => (
            <FeatureRow key={feature.feature} feature={feature} />
          ))}
        </ul>
      </details>

      {card.message && (
        <details>
          <summary className="cursor-pointer text-[11px] text-muted-foreground">
            {t("scoring.messageDraft")}
          </summary>
          <div className="mt-1 space-y-1">
            <InlineNotice variant="partial">{t("scoring.messageNotice")}</InlineNotice>
            <p className="font-medium">{card.message.subject}</p>
            <pre className="rounded border p-1.5 font-sans text-[11px] whitespace-pre-wrap">
              {card.message.body}
            </pre>
          </div>
        </details>
      )}

      {(detail.ai_reasons.length > 0 || detail.ai_dropped > 0) && (
        <div className="space-y-1">
          <Heading>{t("scoring.aiReasons")}</Heading>
          <InlineNotice variant="partial">{t("scoring.aiNotice")}</InlineNotice>
          <ul className="space-y-1">
            {detail.ai_reasons.map((item, index) => (
              <li key={`${item.inference_run_id}:${index}`}>
                {item.explanation}
                <span className="text-[11px] text-muted-foreground">{` · ${item.model}`}</span>
                <Evidence items={item.evidence} />
              </li>
            ))}
          </ul>
          {detail.ai_dropped > 0 && (
            <p className="text-[11px] text-muted-foreground">
              {t("scoring.aiDropped", { n: detail.ai_dropped })}
            </p>
          )}
        </div>
      )}

      {detail.history.length > 1 && (
        <div className="space-y-1">
          <Heading>{t("scoring.history")}</Heading>
          <ul className="space-y-0.5 text-[11px] text-muted-foreground">
            {detail.history.map((item) => (
              <li key={item.score_run_id}>
                {t("scoring.historyLine", {
                  tier: t(TIER_KEYS[item.tier]),
                  total: scoreText(item.total_score),
                  model: item.scoring_model_key,
                  modelVersion: item.scoring_model_version,
                  icp: item.icp_profile_name,
                  icpVersion: item.icp_profile_version,
                  time: formatDateTime(item.created_at, language),
                })}
              </li>
            ))}
          </ul>
        </div>
      )}
      {detail.outcomes.length > 0 && (
        <div className="space-y-1">
          <Heading>{t("scoring.outcomes")}</Heading>
          <ul className="space-y-1">
            {detail.outcomes.map((outcome) => (
              <li key={outcome.id}>
                <p>
                  {t("scoring.outcomeLine", {
                    from: t(STAGE_KEYS[outcome.previous_stage]),
                    to: t(STAGE_KEYS[outcome.stage]),
                    actor: outcome.actor,
                    time: formatDateTime(outcome.occurred_at, language),
                  })}
                </p>
                <p className="text-[11px] break-words text-muted-foreground">
                  {outcome.lost_reason
                    ? `${outcome.lost_reason} · ${outcome.reason}`
                    : outcome.reason}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}
      {detail.decisions.length > 0 && (
        <div className="space-y-1">
          <Heading>{t("scoring.decisions")}</Heading>
          <ul className="space-y-1">
            {detail.decisions.map((decision) => (
              <li key={decision.id}>
                <p>
                  {t("scoring.decisionLine", {
                    decision: codeLabel(t, decision.decision),
                    actor: decision.actor,
                    time: formatDateTime(decision.decided_at, language),
                  })}
                </p>
                <p className="text-[11px] break-words text-muted-foreground">{decision.reason}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      <form className="space-y-1.5" onSubmit={(event) => void submit(event)}>
        <Heading>{t("scoring.act")}</Heading>
        <p className="text-[11px] text-muted-foreground">
          {t("scoring.actHint", { stage: t(STAGE_KEYS[detail.lead_stage]) })}
        </p>
        <Label htmlFor="lead-action" className="text-xs">
          {t("scoring.action")}
        </Label>
        <select
          id="lead-action"
          value={action}
          onChange={(event) => setAction(event.target.value as LeadAction)}
          className={SELECT}
        >
          {LEAD_ACTIONS.map((value) => (
            <option key={value} value={value}>
              {t(ACTION_KEYS[value])}
            </option>
          ))}
        </select>
        {recording && (
          <>
            <Label htmlFor="lead-stage" className="text-xs">
              {t("scoring.stage")}
            </Label>
            <select
              id="lead-stage"
              value={stage}
              onChange={(event) => setStage(event.target.value as LeadStage)}
              className={SELECT}
            >
              {LEAD_STAGES.map((value) => (
                <option key={value} value={value}>
                  {t(STAGE_KEYS[value])}
                </option>
              ))}
            </select>
          </>
        )}
        {needsLostReason && (
          <>
            <Label htmlFor="lead-lost-reason" className="text-xs">
              {t("scoring.lostReason")}
            </Label>
            <Textarea
              id="lead-lost-reason"
              value={lostReason}
              required
              maxLength={500}
              onChange={(event) => setLostReason(event.target.value)}
              className="min-h-10 text-xs"
            />
          </>
        )}
        <Label htmlFor="lead-reason" className="text-xs">
          {t("scoring.reason")}
        </Label>
        <Textarea
          id="lead-reason"
          value={reason}
          required
          maxLength={2000}
          onChange={(event) => setReason(event.target.value)}
          className="min-h-12 text-xs"
        />
        <Button
          type="submit"
          size="sm"
          className="h-7"
          disabled={busy || !reason.trim() || (needsLostReason && !lostReason.trim())}
        >
          {t("scoring.submit")}
        </Button>
      </form>
    </section>
  );
}
