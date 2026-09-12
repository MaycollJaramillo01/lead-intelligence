import { Link } from "react-router";
import { ArrowRight } from "lucide-react";
import { AVAILABLE_NAV_ITEMS, NAV_ITEMS } from "@/app/navigation";
import { PageScroll, PageShell, Section } from "@/components/layout/page-chrome";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { StateBadge } from "@/features/diagnostics/StateBadge";
import { toRows } from "@/features/diagnostics/model";
import { useDiagnosticsStore } from "@/features/diagnostics/store";
import { useT } from "@/lib/i18n";

/** Pipeline stages shown as what they are today: planned, with the phase that delivers them. */
const PIPELINE_STAGES = [
  "discovery",
  "research-center",
  "businesses",
  "icp-builder",
  "scoring-models",
  "exports",
] as const;

export function OverviewScreen() {
  const t = useT();
  const report = useDiagnosticsStore((state) => state.report);
  const worker = useDiagnosticsStore((state) => state.worker);
  const error = useDiagnosticsStore((state) => state.error);
  const rows = report ? toRows(report, worker) : [];

  return (
    <PageShell>
      <PageScroll>
        <Section
          title={t("overview.system")}
          description={t("overview.systemHelp")}
          actions={
            <Button asChild variant="outline" size="sm" className="h-7">
              <Link to="/system">
                {t("overview.openSystem")}
                <ArrowRight aria-hidden="true" />
              </Link>
            </Button>
          }
        >
          {error && (
            <InlineNotice
              variant={error.code === "desktop_unavailable" ? "offline" : "error"}
              className="mb-3"
            >
              {error.message}
            </InlineNotice>
          )}
          {rows.length > 0 ? (
            <ul className="grid gap-x-6 gap-y-1.5 sm:grid-cols-2">
              {rows.map((row) => (
                <li key={row.id} className="flex items-baseline gap-3">
                  <span className="w-28 shrink-0 text-xs font-medium">{row.label}</span>
                  <StateBadge state={row.state} />
                  <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                    {row.summary}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            !error && <StateBlock variant="loading" title={t("state.loading")} />
          )}
        </Section>

        <Section title={t("overview.available")} description={t("overview.availableHelp")}>
          <ul className="grid gap-2 sm:grid-cols-2">
            {AVAILABLE_NAV_ITEMS.filter((item) => item.id !== "overview").map((item) => {
              const Icon = item.icon;
              return (
                <li key={item.id}>
                  <Link
                    to={item.path}
                    className="flex items-start gap-2 rounded-md border p-2.5 hover:bg-accent"
                  >
                    <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
                    <span className="min-w-0">
                      <span className="block text-xs font-medium">{t(item.titleKey)}</span>
                      <span className="block text-xs text-muted-foreground">
                        {t(item.descriptionKey)}
                      </span>
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </Section>

        <Section title={t("overview.pipeline")} description={t("overview.pipelineHelp")}>
          <ol className="space-y-1.5">
            {PIPELINE_STAGES.map((id) => {
              const item = NAV_ITEMS.find((candidate) => candidate.id === id);
              if (!item) return null;
              const Icon = item.icon;
              return (
                <li key={id} className="flex items-center gap-2 text-xs">
                  <Icon aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
                  <span className="w-40 shrink-0">{t(item.titleKey)}</span>
                  <span className="text-muted-foreground">
                    {item.status === "available"
                      ? t("overview.available")
                      : `${t("common.notConfigured")} · ${t("common.availableInPhase", { phase: item.phase ?? 0 })}`}
                  </span>
                </li>
              );
            })}
          </ol>
        </Section>
      </PageScroll>
    </PageShell>
  );
}
