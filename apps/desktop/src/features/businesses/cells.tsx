const EMPTY = "—";

/** Missing values are shown as a muted dash, never as an empty cell or a guessed value. */
export function Text({ value }: { value: string | number | null }) {
  if (value === null || value === "") {
    return <span className="text-muted-foreground">{EMPTY}</span>;
  }
  return <span className="truncate">{value}</span>;
}

export function Mono({ value }: { value: string | number | null }) {
  if (value === null || value === "") {
    return <span className="text-muted-foreground">{EMPTY}</span>;
  }
  return <span className="font-mono tabular-nums">{value}</span>;
}
