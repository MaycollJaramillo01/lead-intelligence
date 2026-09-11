import {
  CircleCheck,
  CircleHelp,
  CircleMinus,
  CircleX,
  LoaderCircle,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { RowState } from "./model";

const STYLES: Record<RowState, { label: string; icon: LucideIcon; className: string }> = {
  ok: { label: "OK", icon: CircleCheck, className: "text-ok" },
  degraded: { label: "Degraded", icon: TriangleAlert, className: "text-warn" },
  unavailable: { label: "Unavailable", icon: CircleMinus, className: "text-neutral" },
  error: { label: "Error", icon: CircleX, className: "text-danger" },
  pending: { label: "Working", icon: LoaderCircle, className: "text-muted-foreground" },
  unknown: { label: "Unknown", icon: CircleHelp, className: "text-muted-foreground" },
};

/** State is always conveyed by icon + text, never by colour alone. */
export function StateBadge({ state }: { state: RowState }) {
  const { label, icon: Icon, className } = STYLES[state];
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-xs font-medium", className)}>
      <Icon
        aria-hidden="true"
        className={cn("size-3.5", state === "pending" && "motion-safe:animate-spin")}
      />
      {label}
    </span>
  );
}
