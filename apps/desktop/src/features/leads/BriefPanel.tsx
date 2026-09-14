import { useState } from "react";
import type { AiReviewDecision, BriefClaim, BriefSection, SalesBrief } from "@lead/shared-types";
import { Sparkles } from "lucide-react";
import { toast } from "sonner";
import { InlineNotice } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { REVIEW_KEYS } from "@/features/ai/labels";
import { EvidenceChip } from "@/features/leads/EvidenceChip";
import { BRIEF_SECTION_KEYS, codeText } from "@/features/leads/labels";
import { Heading } from "@/features/scoring/parts";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { cn } from "@/lib/utils";

function Claim({
  businessId,
  claim,
  section,
  onPinned,
}: {
  businessId: string;
  claim: BriefClaim;
  section?: BriefSection;
  onPinned?: () => void;
}) {
  const t = useT();
  return (
    <li className={cn("space-y-1 rounded border p-1.5", claim.is_inference && "border-dashed")}>
      <p className="break-words">
        {section && (
          <span className="mr-1 text-[10px] tracking-wider text-muted-foreground uppercase">
            {t(BRIEF_SECTION_KEYS[section])}
          </span>
        )}
        {claim.text}
        {claim.is_inference && (
          <span className="ml-1 rounded border px-1 text-[10px] text-muted-foreground">
            {t("brief.inference")}
          </span>
        )}
        <span className="ml-1 font-mono text-[10px] text-muted-foreground">{claim.ref}</span>
      </p>
      {claim.evidence.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {claim.evidence.map((ref, index) => (
            <EvidenceChip
              key={`${ref.kind}:${ref.ref}:${index}`}
              businessId={businessId}
              evidence={ref}
              onPinned={onPinned}
            />
          ))}
        </div>
      )}
    </li>
  );
}

interface BriefClaimsProps {
  brief: SalesBrief;
  sections: readonly BriefSection[];
  /** Facts and inferences in two groups, instead of section by section. */
  split?: boolean;
  onPinned?: () => void;
}

/**
 * Claims of a sales brief (phase 12), each with the stored rows it cites. A section with nothing to
 * say gives its reason; an inference is marked as one, and kept apart from facts when split.
 */
export function BriefClaims({ brief, sections, split = false, onPinned }: BriefClaimsProps) {
  const t = useT();
  const shown = brief.sections.filter((view) => sections.includes(view.section));
  const empty = shown.filter((view) => view.claims.length === 0);
  const reasonOf = (reason: string | null) =>
    reason ? codeText(t, "brief.reason", reason) : t("business.noClaims");

  if (split) {
    const claims = shown.flatMap((view) =>
      view.claims.map((claim) => ({ claim, section: view.section })),
    );
    return (
      <div className="space-y-2">
        {([false, true] as const).map((inference) => {
          const group = claims.filter(({ claim }) => claim.is_inference === inference);
          return (
            <div key={String(inference)} className="space-y-1">
              <Heading>
                {t(inference ? "business.inferences" : "business.facts")} ({group.length})
              </Heading>
              {group.length === 0 ? (
                <p className="text-muted-foreground">{t("business.noClaims")}</p>
              ) : (
                <ul className="space-y-1">
                  {group.map(({ claim, section }) => (
                    <Claim
                      key={claim.ref}
                      businessId={brief.business_id}
                      claim={claim}
                      section={section}
                      onPinned={onPinned}
                    />
                  ))}
                </ul>
              )}
            </div>
          );
        })}
        {empty.length > 0 && (
          <ul className="space-y-0.5 text-[11px] text-muted-foreground">
            {empty.map((view) => (
              <li key={view.section}>
                {`${t(BRIEF_SECTION_KEYS[view.section])}: ${reasonOf(view.empty_reason)}`}
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {shown.map((view) => (
        <div key={view.section} className="space-y-1">
          {sections.length > 1 && <Heading>{t(BRIEF_SECTION_KEYS[view.section])}</Heading>}
          {view.claims.length === 0 ? (
            <p className="text-muted-foreground">{reasonOf(view.empty_reason)}</p>
          ) : (
            <ul className="space-y-1">
              {view.claims.map((claim) => (
                <Claim
                  key={claim.ref}
                  businessId={brief.business_id}
                  claim={claim}
                  onPinned={onPinned}
                />
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}

/** When the brief was built, what it rests on, and whether the lead may be contacted at all. */
export function BriefSummary({ brief }: { brief: SalesBrief }) {
  const t = useT();
  const language = useLanguage();
  return (
    <div className="space-y-1 text-[11px] text-muted-foreground">
      <p>{t("brief.help")}</p>
      <p>
        {brief.last_verified_at
          ? t("brief.lastVerified", { time: formatDateTime(brief.last_verified_at, language) })
          : t("brief.neverVerified")}
        {` · ${t("brief.generatedAt", { time: formatDateTime(brief.generated_at, language) })}`}
      </p>
      {brief.versions && (
        <p className="font-mono break-all">
          {t("scoring.versionsLine", {
            model: brief.versions.scoring_model_key,
            modelVersion: brief.versions.scoring_model_version,
            icp: brief.versions.icp_profile_name,
            icpVersion: brief.versions.icp_profile_version,
            engine: brief.versions.engine_version,
          })}
        </p>
      )}
      {brief.suppressed && <InlineNotice variant="blocked">{t("brief.suppressed")}</InlineNotice>}
    </div>
  );
}

/**
 * A local model's wording of the brief (phase 12). Every sentence restates claims of this brief and
 * names them; the worker drops a sentence whose claims are gone, and rejects an answer that cites a
 * claim the brief never had. Accepting or rejecting it keeps the model's answer and records why.
 */
export function BriefDraftPanel({
  brief,
  onChanged,
}: {
  brief: SalesBrief;
  onChanged: () => void;
}) {
  const t = useT();
  const language = useLanguage();
  const [queued, setQueued] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const { draft } = brief;

  const generate = async () => {
    setBusy(true);
    try {
      await desktop.startAi({
        project_id: null,
        business_ids: [brief.business_id],
        task_types: ["sales_brief"],
      });
      setQueued(true);
    } catch (cause) {
      toast.error(t("brief.generateFailed"), {
        description: toIpcError(cause, "start_ai").message,
      });
    } finally {
      setBusy(false);
    }
  };

  const review = async (decision: AiReviewDecision) => {
    if (!draft || !reason.trim()) return;
    setBusy(true);
    try {
      await desktop.reviewAiInference({
        inference_run_id: draft.inference_run_id,
        decision,
        reason: reason.trim(),
      });
      setReason("");
      onChanged();
    } catch (cause) {
      toast.error(t("brief.reviewFailed"), {
        description: toIpcError(cause, "review_ai_inference").message,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-1.5 rounded-md border p-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Heading>{t("brief.draft")}</Heading>
        <Button
          variant="outline"
          size="sm"
          className="h-6"
          disabled={busy}
          onClick={() => void generate()}
        >
          <Sparkles aria-hidden="true" />
          {t("brief.generate")}
        </Button>
      </div>
      {queued && (
        <InlineNotice
          variant="processing"
          action={
            <Button variant="ghost" size="sm" className="h-6" onClick={onChanged}>
              {t("common.refresh")}
            </Button>
          }
        >
          {t("brief.generateQueued")}
        </InlineNotice>
      )}
      {draft === null ? (
        <p className="text-muted-foreground">{t("brief.noDraft")}</p>
      ) : (
        <>
          <InlineNotice variant="partial">{t("brief.draftNotice")}</InlineNotice>
          <p className="text-[11px] text-muted-foreground">
            {[
              draft.model,
              t(REVIEW_KEYS[draft.review_status]),
              draft.finished_at && formatDateTime(draft.finished_at, language),
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
          {draft.sections.map((section) => (
            <div key={section.section} className="space-y-0.5">
              <p className="text-[10px] tracking-wider text-muted-foreground uppercase">
                {t(BRIEF_SECTION_KEYS[section.section])}
              </p>
              <ul className="space-y-0.5">
                {section.sentences.map((sentence, index) => (
                  <li key={index} className="break-words">
                    {sentence.text}{" "}
                    <span className="font-mono text-[10px] text-muted-foreground">
                      {t("brief.claims", { refs: sentence.claims.join(", ") })}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {draft.dropped > 0 && (
            <p className="text-[11px] text-warn">{t("brief.draftDropped", { n: draft.dropped })}</p>
          )}
          <div className="space-y-1">
            <Label htmlFor={`brief-review-${draft.inference_run_id}`} className="text-xs">
              {t("brief.reviewReason")}
            </Label>
            <Textarea
              id={`brief-review-${draft.inference_run_id}`}
              value={reason}
              maxLength={2000}
              onChange={(event) => setReason(event.target.value)}
              className="min-h-10 text-xs"
            />
            <div className="flex gap-1">
              <Button
                size="sm"
                className="h-7"
                disabled={busy || !reason.trim() || draft.review_status === "accepted"}
                onClick={() => void review("accept")}
              >
                {t("brief.accept")}
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="h-7"
                disabled={busy || !reason.trim()}
                onClick={() => void review("reject")}
              >
                {t("brief.reject")}
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
