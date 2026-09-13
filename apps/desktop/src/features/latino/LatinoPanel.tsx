import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import type {
  LatinoAssessmentView,
  LatinoDetail,
  LatinoEvidence,
  LatinoStatus,
} from "@lead/shared-types";
import { ExternalLink } from "lucide-react";
import { toast } from "sonner";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  EVIDENCE_STATUS_KEYS,
  FRESHNESS_KEYS,
  LATINO_STATUSES,
  LATINO_STATUS_KEYS,
  LATINO_TRACKS,
  METHOD_KEYS,
  OWNERSHIP_STATUSES,
  SOURCE_KIND_KEYS,
  STRENGTH_KEYS,
  TRACK_KEYS,
  claimLabel,
  latinoDecisionLabel,
  limitLabel,
  noteLabel,
} from "@/features/latino/labels";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop, toIpcError, type IpcError } from "@/lib/ipc";
import { cn } from "@/lib/utils";

type DetailState = { detail: LatinoDetail; error: null } | { detail: null; error: IpcError };

/** The review select's value for withdrawing a review: the request carries a null status. */
const WITHDRAW = "withdraw";
const percent = (value: number) => Math.round(value * 100);

function Heading({ children }: { children: ReactNode }) {
  return (
    <h4 className="text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
      {children}
    </h4>
  );
}

function EvidenceEntry({ entry }: { entry: LatinoEvidence }) {
  const t = useT();
  const language = useLanguage();
  const { value, provenance } = entry.evidence;
  const active = value.status === "active";
  return (
    <li className={cn("space-y-1 rounded-md border p-2", !active && "border-dashed")}>
      <p className="font-medium break-words">{claimLabel(t, entry.claim)}</p>
      <p className="flex flex-wrap gap-1 text-[11px]">
        <span className={cn("rounded border px-1", !active && "border-warn/60 text-warn")}>
          {t(EVIDENCE_STATUS_KEYS[value.status])}
        </span>
        <span className="rounded border px-1">
          {t(entry.counted ? "latino.counted" : "latino.notCounted")}
        </span>
        {entry.organizational && (
          <span className="rounded border px-1">{t("latino.organizational")}</span>
        )}
      </p>
      {value.snippet && (
        <blockquote className="border-l-2 pl-2 break-words text-muted-foreground">
          {value.snippet}
        </blockquote>
      )}
      <p className="text-[11px] text-muted-foreground">
        {[
          t("latino.strength", { value: t(STRENGTH_KEYS[value.strength]) }),
          t("latino.freshness", { value: t(FRESHNESS_KEYS[entry.freshness_label]) }),
          t("latino.reliability", { value: percent(entry.source_reliability) }),
        ].join(" · ")}
      </p>
      <p className="text-[11px] break-words text-muted-foreground">
        {provenance.source_url ? (
          <a
            href={provenance.source_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 underline underline-offset-2"
          >
            {provenance.connector_name}
            <ExternalLink aria-hidden="true" className="size-3 shrink-0" />
          </a>
        ) : (
          provenance.connector_name
        )}
        {` (${t(SOURCE_KIND_KEYS[provenance.connector_kind])}) · `}
        {t("latino.field", { path: value.field_path ?? "—" })}
        {` · ${provenance.extractor} ${provenance.extractor_version} · `}
        {formatDateTime(value.observed_at, language)}
      </p>
    </li>
  );
}

/**
 * The Latino evidence of a business (phase 07): ownership and market affinity kept apart, every
 * piece of evidence with its source and fragment, warnings, what the result cannot conclude, its
 * history, and a review that needs a reason. No flag, colour or icon turns a probability into a
 * certainty: every status reads the same way, in words, with its stable code beside it.
 */
export function LatinoPanel({ businessId }: { businessId: string }) {
  const t = useT();
  const language = useLanguage();
  const [state, setState] = useState<DetailState | null>(null);
  const [choice, setChoice] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let current = true;
    desktop.getLatino({ business_id: businessId }).then(
      (detail) => current && setState({ detail, error: null }),
      (cause: unknown) =>
        current && setState({ detail: null, error: toIpcError(cause, "get_latino") }),
    );
    return () => {
      current = false;
    };
  }, [businessId]);

  if (state === null) return <StateBlock variant="loading" title={t("state.loading")} />;
  if (state.error !== null) {
    return (
      <StateBlock variant="error" title={t("latino.loadErrorTitle")} body={state.error.message} />
    );
  }

  const { detail } = state;
  const shown = detail.effective;
  const reviewed = shown?.assessment.method === "manual_review";
  const merged = detail.business_id !== detail.root_business_id;
  const statusOf = (view: LatinoAssessmentView) => t(LATINO_STATUS_KEYS[view.assessment.status]);
  const market = detail.evidence.filter((entry) => entry.track === "market");
  // a review carries no warnings of its own: the calculation's stay visible under it
  const warnings = (detail.automatic ?? shown)?.warnings ?? [];

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!choice || !reason.trim()) return;
    setBusy(true);
    try {
      const updated = await desktop.reviewLatino({
        business_id: detail.root_business_id,
        status: choice === WITHDRAW ? null : (choice as LatinoStatus),
        reason: reason.trim(),
      });
      setState({ detail: updated, error: null });
      setChoice("");
      setReason("");
      toast.success(t("latino.reviewDone"));
    } catch (cause) {
      toast.error(t("latino.reviewFailed"), {
        description: toIpcError(cause, "review_latino").message,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-label={t("latino.title")} className="space-y-3 text-xs">
      <Heading>{t("latino.title")}</Heading>
      {merged && <InlineNotice variant="partial">{t("latino.belongsTo")}</InlineNotice>}

      {shown === null ? (
        <p className="text-muted-foreground">{t("latino.notEvaluated")}</p>
      ) : (
        <div className="space-y-1.5 rounded-md border p-2">
          <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1">
            <dt className="text-muted-foreground">{t("latino.ownership")}</dt>
            <dd>
              {OWNERSHIP_STATUSES.has(shown.assessment.status)
                ? statusOf(shown)
                : t("latino.noOwnership")}
            </dd>
            <dt className="text-muted-foreground">{t("latino.market")}</dt>
            <dd>
              {shown.assessment.status === "spanish_market_affinity"
                ? t("latino.marketAffinity")
                : market.length > 0
                  ? t("latino.marketSignals", { n: market.length })
                  : t("latino.noMarket")}
            </dd>
          </dl>
          <p>
            <span className="font-medium">{statusOf(shown)}</span>{" "}
            <span className="font-mono text-[11px] text-muted-foreground">
              {shown.assessment.status}
            </span>
          </p>
          <p className="text-[11px] text-muted-foreground">
            {t("latino.confidence", {
              confidence: percent(shown.assessment.assessment_confidence),
            })}
            {" · "}
            {reviewed
              ? t("latino.byReview", { actor: detail.decisions[0]?.actor ?? "" })
              : t("latino.byRules", { version: shown.assessment.rules_version })}
          </p>
          {reviewed && detail.automatic && (
            <p className="text-[11px] text-muted-foreground">
              {t("latino.automatic", {
                status: statusOf(detail.automatic),
                confidence: percent(detail.automatic.assessment.assessment_confidence),
              })}
            </p>
          )}
          {detail.review_outdated && (
            <InlineNotice variant="partial">{t("latino.reviewOutdated")}</InlineNotice>
          )}
        </div>
      )}

      {warnings.length > 0 && (
        <div className="space-y-1">
          <Heading>{t("latino.warnings")}</Heading>
          <ul className="space-y-0.5">
            {warnings.map((note) => (
              <li key={note.code} className="rounded border border-warn/60 px-1.5 py-0.5 text-warn">
                {noteLabel(t, note.code)}
                {note.detail && ` (${note.detail})`}
              </li>
            ))}
          </ul>
        </div>
      )}
      {shown && shown.cannot_conclude.length > 0 && (
        <div className="space-y-1">
          <Heading>{t("latino.cannotConclude")}</Heading>
          <ul className="list-disc space-y-0.5 pl-4 text-muted-foreground">
            {shown.cannot_conclude.map((code) => (
              <li key={code}>{limitLabel(t, code)}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="space-y-1.5">
        <Heading>{t("latino.evidence", { n: detail.evidence.length })}</Heading>
        {detail.evidence.length === 0 && (
          <p className="text-muted-foreground">{t("latino.noEvidence")}</p>
        )}
        {LATINO_TRACKS.map((track) => {
          const entries = detail.evidence.filter((entry) => entry.track === track);
          return entries.length === 0 ? null : (
            <div key={track} className="space-y-1">
              <p className="font-medium">{t(TRACK_KEYS[track])}</p>
              <ul className="space-y-1.5">
                {entries.map((entry) => (
                  <EvidenceEntry key={entry.evidence.value.id} entry={entry} />
                ))}
              </ul>
            </div>
          );
        })}
      </div>

      {detail.history.length > 0 && (
        <div className="space-y-1">
          <Heading>{t("latino.history")}</Heading>
          <ul className="space-y-0.5 text-[11px] text-muted-foreground">
            {detail.history.map((view) => (
              <li key={view.assessment.id}>
                {t("latino.historyLine", {
                  status: statusOf(view),
                  method: t(METHOD_KEYS[view.assessment.method]),
                  confidence: percent(view.assessment.assessment_confidence),
                  version: view.assessment.rules_version,
                  time: formatDateTime(view.assessment.assessed_at, language),
                })}
                {view.assessment.superseded_at && ` · ${t("latino.superseded")}`}
              </li>
            ))}
          </ul>
        </div>
      )}
      {detail.decisions.length > 0 && (
        <div className="space-y-1">
          <Heading>{t("latino.decisions")}</Heading>
          <ul className="space-y-1">
            {detail.decisions.map((decision) => (
              <li key={decision.id}>
                <p>
                  {t("latino.decisionLine", {
                    decision: latinoDecisionLabel(t, decision.decision),
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

      {!merged && (
        <form className="space-y-1.5" onSubmit={(event) => void submit(event)}>
          <Heading>{t("latino.review")}</Heading>
          <p className="text-[11px] text-muted-foreground">{t("latino.reviewHint")}</p>
          <Label htmlFor="latino-review-status" className="text-xs">
            {t("latino.reviewStatus")}
          </Label>
          <select
            id="latino-review-status"
            value={choice}
            onChange={(event) => setChoice(event.target.value)}
            className="h-7 w-full rounded-md border bg-transparent px-2 text-xs"
          >
            <option value="" disabled>
              {t("latino.reviewChoose")}
            </option>
            {LATINO_STATUSES.map((value) => (
              <option key={value} value={value}>
                {t(LATINO_STATUS_KEYS[value])}
              </option>
            ))}
            {reviewed && <option value={WITHDRAW}>{t("latino.reviewWithdraw")}</option>}
          </select>
          <Label htmlFor="latino-review-reason" className="text-xs">
            {t("latino.reviewReason")}
          </Label>
          <Textarea
            id="latino-review-reason"
            value={reason}
            required
            maxLength={2000}
            placeholder={t("latino.reviewReasonPlaceholder")}
            onChange={(event) => setReason(event.target.value)}
            className="min-h-12 text-xs"
          />
          <Button
            type="submit"
            size="sm"
            className="h-7"
            disabled={busy || !choice || !reason.trim()}
          >
            {t("latino.reviewSubmit")}
          </Button>
        </form>
      )}
    </section>
  );
}
