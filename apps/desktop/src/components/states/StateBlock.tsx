import type { ReactNode } from "react";
import {
  Ban,
  CircleAlert,
  CircleCheck,
  Clock,
  Inbox,
  LoaderCircle,
  TriangleAlert,
  WifiOff,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

/** The states every screen can be in. Each one carries an icon AND a label. */
export type StateVariant =
  | "empty"
  | "loading"
  | "processing"
  | "error"
  | "offline"
  | "partial"
  | "stale"
  | "success"
  | "blocked";

const VARIANTS: Record<StateVariant, { icon: LucideIcon; tone: string; spin?: boolean }> = {
  empty: { icon: Inbox, tone: "text-muted-foreground" },
  loading: { icon: LoaderCircle, tone: "text-muted-foreground", spin: true },
  processing: { icon: LoaderCircle, tone: "text-info", spin: true },
  error: { icon: CircleAlert, tone: "text-danger" },
  offline: { icon: WifiOff, tone: "text-warn" },
  partial: { icon: TriangleAlert, tone: "text-warn" },
  stale: { icon: Clock, tone: "text-warn" },
  success: { icon: CircleCheck, tone: "text-ok" },
  blocked: { icon: Ban, tone: "text-neutral" },
};

interface StateBlockProps {
  variant: StateVariant;
  title: string;
  body?: ReactNode;
  action?: ReactNode;
  className?: string;
}

/** Centred block for a whole pane: empty tables, failed loads, offline core. */
export function StateBlock({ variant, title, body, action, className }: StateBlockProps) {
  const { icon: Icon, tone, spin } = VARIANTS[variant];
  const alert = variant === "error" || variant === "offline";
  return (
    <div
      role={alert ? "alert" : undefined}
      className={cn(
        "mx-auto flex max-w-md flex-col items-center gap-2 px-6 py-10 text-center",
        className,
      )}
    >
      <Icon aria-hidden="true" className={cn("size-5", tone, spin && "motion-safe:animate-spin")} />
      <p className="text-sm font-medium">{title}</p>
      {body && <div className="text-xs leading-relaxed text-muted-foreground">{body}</div>}
      {action && <div className="pt-1">{action}</div>}
    </div>
  );
}

interface InlineNoticeProps {
  variant: StateVariant;
  children: ReactNode;
  action?: ReactNode;
  className?: string;
}

/** Compact banner for partial, stale or offline data shown above live content. */
export function InlineNotice({ variant, children, action, className }: InlineNoticeProps) {
  const { icon: Icon, tone, spin } = VARIANTS[variant];
  const alert = variant === "error" || variant === "offline";
  return (
    <div
      role={alert ? "alert" : "status"}
      className={cn(
        "flex items-center gap-2 rounded-md border bg-muted/40 px-3 py-1.5 text-xs",
        className,
      )}
    >
      <Icon
        aria-hidden="true"
        className={cn("size-3.5 shrink-0", tone, spin && "motion-safe:animate-spin")}
      />
      <span className="min-w-0 flex-1">{children}</span>
      {action}
    </div>
  );
}
