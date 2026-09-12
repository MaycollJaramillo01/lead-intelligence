import {
  Ban,
  CircleAlert,
  CircleCheck,
  CirclePause,
  Clock,
  LoaderCircle,
  type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export type JobState = "queued" | "running" | "paused" | "succeeded" | "failed" | "cancelled";

export interface JobSummary {
  id: string;
  name: string;
  state: JobState;
  /** Units finished; null while the worker cannot tell yet. */
  done?: number | null;
  /** Total units; null means indeterminate progress. */
  total?: number | null;
  /** Units per minute, already measured (never estimated for display). */
  ratePerMinute?: number | null;
  errors?: number;
}

const STATES: Record<JobState, { icon: LucideIcon; tone: string; spin?: boolean }> = {
  queued: { icon: Clock, tone: "text-neutral" },
  running: { icon: LoaderCircle, tone: "text-info", spin: true },
  paused: { icon: CirclePause, tone: "text-warn" },
  succeeded: { icon: CircleCheck, tone: "text-ok" },
  failed: { icon: CircleAlert, tone: "text-danger" },
  cancelled: { icon: Ban, tone: "text-neutral" },
};

const CANCELLABLE: JobState[] = ["queued", "running", "paused"];

interface JobProgressProps {
  job: JobSummary;
  onCancel?: (id: string) => void;
}

export function JobProgress({ job, onCancel }: JobProgressProps) {
  const t = useT();
  const { icon: Icon, tone, spin } = STATES[job.state];
  const determinate =
    typeof job.total === "number" && job.total > 0 && typeof job.done === "number";
  const percent = determinate ? Math.round(((job.done ?? 0) / (job.total ?? 1)) * 100) : null;

  return (
    <article className="space-y-1.5 rounded-md border bg-card p-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <Icon
            aria-hidden="true"
            className={cn("size-3.5 shrink-0", tone, spin && "motion-safe:animate-spin")}
          />
          <span className="truncate text-xs font-medium">{job.name}</span>
          <span className={cn("text-[11px]", tone)}>{job.state}</span>
        </div>
        {onCancel && CANCELLABLE.includes(job.state) && (
          <Button size="sm" variant="outline" onClick={() => onCancel(job.id)}>
            {t("job.cancel")}
          </Button>
        )}
      </div>

      <progress
        aria-label={`${job.name}: ${t("job.progress")}`}
        max={100}
        value={percent ?? undefined}
        className="h-1.5 w-full appearance-none overflow-hidden rounded-full [&::-webkit-progress-bar]:rounded-full [&::-webkit-progress-bar]:bg-muted [&::-webkit-progress-value]:rounded-full [&::-webkit-progress-value]:bg-primary"
      />

      <dl className="flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-muted-foreground">
        <div className="flex gap-1">
          <dt>{t("job.progress")}</dt>
          <dd className="font-mono text-foreground">
            {determinate ? `${job.done}/${job.total} (${percent}%)` : t("job.indeterminate")}
          </dd>
        </div>
        {typeof job.ratePerMinute === "number" && (
          <div className="flex gap-1">
            <dt>{t("job.rate")}</dt>
            <dd className="font-mono text-foreground">{job.ratePerMinute}/min</dd>
          </div>
        )}
        {typeof job.errors === "number" && job.errors > 0 && (
          <div className="flex gap-1">
            <dt>{t("job.errors")}</dt>
            <dd className="font-mono text-danger">{job.errors}</dd>
          </div>
        )}
      </dl>
    </article>
  );
}
