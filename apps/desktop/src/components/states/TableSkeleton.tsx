import { Skeleton } from "@/components/ui/skeleton";

/** Row-shaped placeholder that matches the density of the real table. */
export function TableSkeleton({ rows = 8, columns = 5 }: { rows?: number; columns?: number }) {
  return (
    <div aria-hidden="true" className="divide-y divide-border">
      {Array.from({ length: rows }, (_row, row) => (
        <div
          key={row}
          className="flex items-center gap-4 px-[var(--cell-padding-x)]"
          style={{ height: "var(--row-height)" }}
        >
          {Array.from({ length: columns }, (_column, column) => (
            <Skeleton
              key={column}
              className="h-3"
              style={{ width: column === 0 ? "18%" : `${10 + ((row + column) % 4) * 3}%` }}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
