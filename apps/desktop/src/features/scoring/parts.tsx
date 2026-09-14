import type { ReactNode } from "react";
import type { CriteriaProblem, ScoreBin, ScoringSimulation, TierCounts } from "@lead/shared-types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { Change } from "@/features/scoring/definitions";
import { TIERS, TIER_KEYS, codeLabel, scoreText } from "@/features/scoring/labels";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export function Heading({ children }: { children: ReactNode }) {
  return (
    <h4 className="text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
      {children}
    </h4>
  );
}

/** Label wrapping its control: the association holds without hand-managed ids. */
export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block space-y-1">
      <span className="text-xs font-medium">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-muted-foreground">{hint}</span>}
    </label>
  );
}

export function NumberInput({
  label,
  value,
  min,
  max,
  step = 1,
  onChange,
}: {
  label: string;
  value: number | null;
  min: number;
  max: number;
  step?: number;
  onChange: (value: number | null) => void;
}) {
  return (
    <Input
      type="number"
      aria-label={label}
      value={value ?? ""}
      min={min}
      max={max}
      step={step}
      className="h-7 w-20 text-xs"
      onChange={(event) => onChange(event.target.value === "" ? null : Number(event.target.value))}
    />
  );
}

export function Choices<T extends string>({
  legend,
  options,
  selected,
  label,
  onChange,
}: {
  legend: string;
  options: readonly T[];
  selected: readonly T[];
  label: (value: T) => string;
  onChange: (values: T[]) => void;
}) {
  return (
    <fieldset className="flex flex-wrap gap-x-3 gap-y-1">
      <legend className="sr-only">{legend}</legend>
      {options.map((option) => (
        <label key={option} className="flex items-center gap-1 text-xs">
          <input
            type="checkbox"
            checked={selected.includes(option)}
            onChange={() =>
              onChange(
                selected.includes(option)
                  ? selected.filter((value) => value !== option)
                  : [...selected, option],
              )
            }
          />
          {label(option)}
        </label>
      ))}
    </fieldset>
  );
}

export function TierLine({ counts }: { counts: TierCounts }) {
  const t = useT();
  return (
    <p className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px]">
      {TIERS.map((tier) => (
        <span key={tier}>
          {t(TIER_KEYS[tier])}: <span className="font-mono tabular-nums">{counts[tier]}</span>
        </span>
      ))}
    </p>
  );
}

/** Scored leads per ten-point bin, as numbers next to bars of the same length: never colour alone. */
export function Distribution({ bins, label }: { bins: readonly ScoreBin[]; label: string }) {
  const largest = Math.max(1, ...bins.map((bin) => bin.businesses));
  return (
    <figure aria-label={label} className="space-y-0.5">
      <figcaption className="text-[11px] text-muted-foreground">{label}</figcaption>
      <ul className="space-y-0.5">
        {bins.map((bin) => (
          <li
            key={bin.low}
            className="grid grid-cols-[3.5rem_minmax(0,1fr)_2.5rem] items-center gap-2 text-[11px]"
          >
            <span className="font-mono tabular-nums">
              {bin.low}–{bin.low === 90 ? 100 : bin.low + 9}
            </span>
            <span className="h-1.5 rounded-full bg-muted">
              <span
                className="block h-1.5 rounded-full bg-primary"
                style={{ width: `${(100 * bin.businesses) / largest}%` }}
              />
            </span>
            <span className="text-right font-mono tabular-nums">{bin.businesses}</span>
          </li>
        ))}
      </ul>
    </figure>
  );
}

export function ChangeList({ changes }: { changes: readonly Change[] }) {
  const t = useT();
  if (changes.length === 0) {
    return <p className="text-[11px] text-muted-foreground">{t("scoring.noChanges")}</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11px]">
        <thead>
          <tr className="text-left text-muted-foreground">
            <th className="font-normal">{t("scoring.path")}</th>
            <th className="font-normal">{t("scoring.before")}</th>
            <th className="font-normal">{t("scoring.after")}</th>
          </tr>
        </thead>
        <tbody>
          {changes.map((change) => (
            <tr key={change.path} className="border-t align-top">
              <td className="py-0.5 pr-2 font-mono break-all">{change.path}</td>
              <td className="py-0.5 pr-2 font-mono break-all">{change.before}</td>
              <td className="py-0.5 font-mono break-all">{change.after}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ProblemsList({ problems }: { problems: readonly CriteriaProblem[] }) {
  const t = useT();
  if (problems.length === 0) return null;
  return (
    <ul className="space-y-0.5 text-xs">
      {problems.map((problem) => (
        <li
          key={`${problem.code}:${problem.path}`}
          className={cn(
            "rounded border px-1.5 py-0.5",
            problem.severity === "error" ? "border-danger/60" : "border-warn/60",
          )}
        >
          <span className="font-medium">
            {t(problem.severity === "error" ? "scoring.problemError" : "scoring.problemWarning")}
          </span>
          {" · "}
          {problem.message}
        </li>
      ))}
    </ul>
  );
}

export interface VersionRow {
  id: string;
  version: number;
  created_at: string;
  created_by: string;
}

/** Versions newest first: load one into the editor, activate it (with the reason typed above), or
 * tick two to compare them. */
export function VersionList({
  versions,
  activeId,
  compared,
  canActivate,
  onLoad,
  onActivate,
  onCompare,
}: {
  versions: readonly VersionRow[];
  activeId: string | null;
  compared: readonly string[];
  canActivate: boolean;
  onLoad: (id: string) => void;
  onActivate: (id: string) => void;
  onCompare: (id: string) => void;
}) {
  const t = useT();
  const language = useLanguage();
  return (
    <ul className="space-y-1">
      {versions.map((version) => (
        <li
          key={version.id}
          className="flex flex-wrap items-center gap-2 rounded-md border px-2 py-1 text-xs"
        >
          <span className="font-medium">v{version.version}</span>
          {version.id === activeId && (
            <span className="rounded border border-primary/60 px-1 text-[10px] text-primary">
              {t("scoring.active")}
            </span>
          )}
          <span className="text-[11px] text-muted-foreground">
            {`${formatDateTime(version.created_at, language)} · ${version.created_by}`}
          </span>
          <span className="ml-auto flex items-center gap-1">
            <label className="flex items-center gap-1 text-[11px]">
              <input
                type="checkbox"
                checked={compared.includes(version.id)}
                onChange={() => onCompare(version.id)}
              />
              {t("scoring.compare")}
            </label>
            <Button
              variant="ghost"
              size="sm"
              className="h-6 px-1.5 text-[11px]"
              onClick={() => onLoad(version.id)}
            >
              {t("scoring.load")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-6 px-1.5 text-[11px]"
              disabled={!canActivate || version.id === activeId}
              onClick={() => onActivate(version.id)}
            >
              {t("scoring.activate")}
            </Button>
          </span>
        </li>
      ))}
    </ul>
  );
}

/** A draft scored in memory: counts per tier and gate, examples of why a gate stops a business, the
 * first leads, and what keeps the drafts from running. Nothing was written. */
export function SimulationView({ simulation }: { simulation: ScoringSimulation }) {
  const t = useT();
  return (
    <section aria-label={t("scoring.preview")} className="space-y-2 rounded-md border p-2 text-xs">
      <Heading>{t("scoring.preview")}</Heading>
      <p className="text-[11px] text-muted-foreground">
        {t("scoring.previewLine", { n: simulation.businesses })}
      </p>
      <ProblemsList problems={simulation.problems} />
      <TierLine counts={simulation.tiers} />
      <Distribution bins={simulation.distribution} label={t("scoring.distribution")} />
      <div className="overflow-x-auto">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="text-left text-muted-foreground">
              <th className="font-normal">{t("scoring.gate")}</th>
              <th className="text-right font-normal">{t("scoring.passed")}</th>
              <th className="text-right font-normal">{t("scoring.failed")}</th>
              <th className="text-right font-normal">{t("scoring.undecided")}</th>
            </tr>
          </thead>
          <tbody>
            {simulation.gates.map((gate) => (
              <tr key={gate.gate} className="border-t align-top">
                <td className="py-0.5 pr-2">
                  {codeLabel(t, gate.gate)}
                  {gate.examples.length > 0 && (
                    <ul className="text-muted-foreground">
                      {gate.examples.map((example) => (
                        <li key={example.business_id}>
                          {`${example.display_name}: ${example.detail}`}
                        </li>
                      ))}
                    </ul>
                  )}
                </td>
                <td className="text-right font-mono tabular-nums">{gate.passed}</td>
                <td className="text-right font-mono tabular-nums">{gate.failed}</td>
                <td className="text-right font-mono tabular-nums">{gate.unknown}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {simulation.top.length > 0 && (
        <ol className="space-y-0.5">
          {simulation.top.map((item) => (
            <li key={item.business_id}>
              {`#${item.rank} ${item.display_name} · ${t(TIER_KEYS[item.tier])} · ${scoreText(item.total_score)}`}
              {item.top_reason && (
                <span className="text-muted-foreground">{` · ${item.top_reason}`}</span>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
