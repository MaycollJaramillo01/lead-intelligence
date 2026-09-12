import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export type ScoreConfidence = "low" | "medium" | "high";

export interface ScoreFactor {
  label: string;
  /** Signed contribution to the final value. */
  contribution: number;
  note?: string;
}

/** A score is never just a colour: value, model version, confidence and factors travel together. */
export interface ScoreRecord {
  label: string;
  value: number | null;
  max?: number;
  modelVersion: string;
  confidence: ScoreConfidence | null;
  factors?: ScoreFactor[];
  /** Why there is no value yet (missing evidence, gate failed, not run). */
  unavailableReason?: string;
}

const CONFIDENCE_TONE: Record<ScoreConfidence, string> = {
  high: "text-ok",
  medium: "text-warn",
  low: "text-danger",
};

export function ScoreValue({ score }: { score: ScoreRecord }) {
  const t = useT();
  const max = score.max ?? 100;
  const value = score.value;
  const hasValue = value !== null;

  return (
    <section className="space-y-1.5 rounded-md border bg-card p-3">
      <div className="flex items-baseline justify-between gap-3">
        <h4 className="text-xs font-medium">{score.label}</h4>
        <span className="font-mono text-[11px] text-muted-foreground">
          {t("common.version")} {score.modelVersion}
        </span>
      </div>

      <div className="flex items-center gap-3">
        <span className="font-mono text-lg leading-none tabular-nums">
          {hasValue ? value : "—"}
          {hasValue && <span className="text-xs text-muted-foreground">/{max}</span>}
        </span>
        <progress
          aria-label={score.label}
          max={max}
          value={hasValue ? value : undefined}
          className="h-1.5 min-w-24 flex-1 appearance-none overflow-hidden rounded-full [&::-webkit-progress-bar]:rounded-full [&::-webkit-progress-bar]:bg-muted [&::-webkit-progress-value]:rounded-full [&::-webkit-progress-value]:bg-primary"
        />
      </div>

      {hasValue ? (
        <p className="text-[11px]">
          <span className="text-muted-foreground">{t("common.confidence")}: </span>
          <span
            className={cn("font-medium", score.confidence && CONFIDENCE_TONE[score.confidence])}
          >
            {score.confidence ?? t("common.unknown")}
          </span>
        </p>
      ) : (
        <p className="text-[11px] text-muted-foreground">
          {t("score.notScored")}
          {score.unavailableReason ? `: ${score.unavailableReason}` : null}
        </p>
      )}

      {score.factors && score.factors.length > 0 && (
        <details className="group">
          <summary className="cursor-pointer text-[11px] text-muted-foreground underline-offset-2 hover:underline">
            {t("score.explanation")}
          </summary>
          <ul className="mt-1 space-y-0.5">
            {score.factors.map((factor) => (
              <li
                key={factor.label}
                className="flex items-baseline justify-between gap-3 text-[11px]"
              >
                <span className="min-w-0">
                  {factor.label}
                  {factor.note && <span className="text-muted-foreground"> · {factor.note}</span>}
                </span>
                <span className="font-mono tabular-nums">
                  {factor.contribution > 0 ? "+" : ""}
                  {factor.contribution}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
