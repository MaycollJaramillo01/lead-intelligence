import { PageScroll, PageShell, Section } from "@/components/layout/page-chrome";
import { StateBlock } from "@/components/states/StateBlock";
import { JobProgress, type JobSummary } from "@/components/domain/JobProgress";
import { useT } from "@/lib/i18n";

/** Jobs come from the orchestrator (phase 13). Until then the list is genuinely empty. */
const JOBS: JobSummary[] = [];

export function JobsScreen() {
  const t = useT();

  return (
    <PageShell>
      <PageScroll>
        <Section title={t("nav.jobs")} description={t("desc.jobs")}>
          {JOBS.length === 0 ? (
            <StateBlock variant="empty" title={t("jobs.emptyTitle")} body={t("jobs.emptyBody")} />
          ) : (
            <ul className="space-y-2">
              {JOBS.map((job) => (
                <li key={job.id}>
                  <JobProgress job={job} />
                </li>
              ))}
            </ul>
          )}
        </Section>
      </PageScroll>
    </PageShell>
  );
}
