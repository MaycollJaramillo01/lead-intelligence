import { ExternalLink } from "lucide-react";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export type EvidenceStrength = "strong" | "moderate" | "weak";
export type EvidenceFreshness = "fresh" | "aging" | "stale";

/** One observed fact with where it came from. Inferences are never rendered as evidence. */
export interface EvidenceRecord {
  id: string;
  claim: string;
  observedValue?: string | null;
  source: { label: string; type: string; url?: string | null };
  capturedAt: string;
  /** 0..1 */
  confidence: number;
  strength: EvidenceStrength;
  freshness?: EvidenceFreshness;
  context?: string | null;
}

const STRENGTH_TONE: Record<EvidenceStrength, string> = {
  strong: "text-ok",
  moderate: "text-info",
  weak: "text-neutral",
};

const FRESHNESS_TONE: Record<EvidenceFreshness, string> = {
  fresh: "text-ok",
  aging: "text-warn",
  stale: "text-danger",
};

export function EvidenceItem({ evidence }: { evidence: EvidenceRecord }) {
  const t = useT();
  const language = useLanguage();
  const confidence = `${Math.round(evidence.confidence * 100)}%`;
  return (
    <article className="space-y-1.5 rounded-md border bg-card p-3">
      <div className="flex items-baseline justify-between gap-3">
        <h4 className="text-xs font-medium">{evidence.claim}</h4>
        <span className={cn("text-[11px] font-medium", STRENGTH_TONE[evidence.strength])}>
          {t("evidence.strength")}: {evidence.strength}
        </span>
      </div>
      {evidence.observedValue && (
        <p className="font-mono text-xs break-words">{evidence.observedValue}</p>
      )}
      {evidence.context && <p className="text-xs text-muted-foreground">{evidence.context}</p>}
      <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
        <dt>{t("common.source")}</dt>
        <dd className="min-w-0">
          {evidence.source.url ? (
            <a
              href={evidence.source.url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 underline underline-offset-2"
            >
              <span className="truncate">{evidence.source.label}</span>
              <ExternalLink aria-hidden="true" className="size-3 shrink-0" />
              <span className="sr-only">{t("evidence.openSource")}</span>
            </a>
          ) : (
            <span>{evidence.source.label}</span>
          )}
          <span className="ml-1 opacity-70">({evidence.source.type})</span>
        </dd>
        <dt>{t("common.capturedAt")}</dt>
        <dd>{formatDateTime(evidence.capturedAt, language)}</dd>
        <dt>{t("common.confidence")}</dt>
        <dd>{confidence}</dd>
        {evidence.freshness && (
          <>
            <dt>{t("evidence.freshness")}</dt>
            <dd className={FRESHNESS_TONE[evidence.freshness]}>{evidence.freshness}</dd>
          </>
        )}
      </dl>
    </article>
  );
}
