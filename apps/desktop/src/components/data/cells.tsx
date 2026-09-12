import { FlaskConical } from "lucide-react";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";

const EMPTY = "—";

/** Missing values are shown as a muted dash, never as an empty cell or a guessed value. */
export function Text({ value }: { value: string | number | null }) {
  if (value === null || value === "") {
    return <span className="text-muted-foreground">{EMPTY}</span>;
  }
  return <span className="min-w-0 truncate">{value}</span>;
}

export function Mono({ value }: { value: string | number | null }) {
  if (value === null || value === "") {
    return <span className="text-muted-foreground">{EMPTY}</span>;
  }
  return <span className="font-mono tabular-nums">{value}</span>;
}

export function DateTime({ value }: { value: string | null }) {
  const language = useLanguage();
  if (value === null) return <span className="text-muted-foreground">{EMPTY}</span>;
  return <span className="tabular-nums">{formatDateTime(value, language)}</span>;
}

/** Synthetic records are always labelled in words, wherever they are shown. */
export function TestDataBadge() {
  const t = useT();
  return (
    <span className="inline-flex shrink-0 items-center gap-1 rounded border border-warn/60 px-1 text-[10px] font-medium text-warn">
      <FlaskConical aria-hidden="true" className="size-3" />
      {t("common.testData")}
    </span>
  );
}
