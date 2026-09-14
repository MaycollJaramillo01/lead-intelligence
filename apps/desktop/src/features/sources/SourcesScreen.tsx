import { useCallback, useEffect } from "react";
import type { SourceHealth, SourceInfo } from "@lead/shared-types";
import { Ban, CircleCheck, ExternalLink, Settings2, TriangleAlert } from "lucide-react";
import { useSearchParams } from "react-router";
import { PageScroll, PageShell, Section } from "@/components/layout/page-chrome";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { formatDateTime, useLanguage, useT, type Translator } from "@/lib/i18n";
import { desktop } from "@/lib/ipc";
import { useLoaded } from "@/lib/useLoaded";
import { cn } from "@/lib/utils";

const HEALTH: Record<SourceHealth, { icon: typeof CircleCheck; tone: string }> = {
  ready: { icon: CircleCheck, tone: "text-ok" },
  needs_configuration: { icon: Settings2, tone: "text-warn" },
  disabled: { icon: Ban, tone: "text-neutral" },
  unavailable: { icon: TriangleAlert, tone: "text-danger" },
};

const HEALTH_LABEL: Record<SourceHealth, Parameters<Translator>[0]> = {
  ready: "sources.health.ready",
  needs_configuration: "sources.health.needsConfiguration",
  disabled: "sources.health.disabled",
  unavailable: "sources.health.unavailable",
};

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col">
      <dt className="text-[10px] tracking-wider text-muted-foreground uppercase">{label}</dt>
      <dd className="truncate text-xs">{children}</dd>
    </div>
  );
}

function SourceCard({ source, linked }: { source: SourceInfo; linked: boolean }) {
  const t = useT();
  const language = useLanguage();
  const { icon: Icon, tone } = HEALTH[source.health];
  const meta = source.metadata;

  return (
    <article
      id={`source-${meta.key}`}
      aria-current={linked ? "true" : undefined}
      className={cn("space-y-2 rounded-md border bg-card p-3", linked && "ring-2 ring-primary/60")}
    >
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-medium">{meta.name}</h3>
          <p className="font-mono text-[11px] text-muted-foreground">
            {meta.key} · v{meta.version} · {t(`sourceKind.${meta.kind}`)}
          </p>
        </div>
        <span className={cn("inline-flex items-center gap-1 text-xs", tone)}>
          <Icon aria-hidden="true" className="size-3.5" />
          {t(HEALTH_LABEL[source.health])}
        </span>
      </header>

      {source.health_detail && (
        <p className="text-xs text-muted-foreground">{source.health_detail}</p>
      )}
      <p className="text-xs leading-relaxed text-muted-foreground">{meta.terms_summary}</p>
      {meta.terms_url && (
        <a
          href={meta.terms_url}
          target="_blank"
          rel="noreferrer noopener"
          className="inline-flex items-center gap-1 text-xs text-primary underline-offset-2 hover:underline"
        >
          <ExternalLink aria-hidden="true" className="size-3" />
          {t("sources.terms")}
        </a>
      )}

      <dl className="grid grid-cols-2 gap-2 border-t pt-2 sm:grid-cols-4">
        <Fact label={t("sources.rateLimit")}>
          <span className="font-mono">
            {t("sources.perMinute", { n: source.requests_per_minute })}
          </span>
        </Fact>
        <Fact label={t("sources.dailyBudget")}>
          <span className="font-mono">{source.daily_request_budget ?? t("sources.noBudget")}</span>
        </Fact>
        <Fact label={t("sources.credentials")}>
          {meta.requires_credentials ? t("common.yes") : t("common.no")}
        </Fact>
        <Fact label={t("sources.lastRun")}>
          {source.last_run_at ? formatDateTime(source.last_run_at, language) : "—"}
        </Fact>
      </dl>

      <div>
        <h4 className="mb-1 text-[10px] tracking-wider text-muted-foreground uppercase">
          {t("sources.capabilities")}
        </h4>
        <ul className="flex flex-wrap gap-1">
          {meta.capabilities.map((capability) => (
            <li
              key={capability}
              className="rounded border px-1.5 py-0.5 text-[10px] text-muted-foreground"
            >
              {t(`capability.${capability}`)}
            </li>
          ))}
        </ul>
      </div>

      {meta.config_fields.length > 0 && (
        <div className="border-t pt-2">
          <h4 className="mb-1 text-[10px] tracking-wider text-muted-foreground uppercase">
            {t("sources.settings")}
          </h4>
          <ul className="space-y-0.5">
            {meta.config_fields.map((field) => (
              <li key={field.key} className="text-xs">
                <span className="font-medium">{field.label}</span>
                {field.required && <span className="text-danger"> *</span>}
                <span className="text-muted-foreground"> — {field.help}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {source.last_error_message && (
        <p className="border-t pt-2 text-xs text-danger">
          {source.last_error_code}: {source.last_error_message}
        </p>
      )}
    </article>
  );
}

/** Every source this build ships: what it may do, on what terms, and whether it could run now. */
export function SourcesScreen() {
  const t = useT();
  const language = useLanguage();
  const fetchCatalog = useCallback(() => desktop.listSources({ project_id: null }), []);
  const { data: catalog, error, reload } = useLoaded(fetchCatalog, "list_sources");
  const [params] = useSearchParams();
  const linked = params.get("source");

  // a `?source=` link brings its source into view once the catalogue is read
  useEffect(() => {
    if (linked && catalog) {
      document.getElementById(`source-${linked}`)?.scrollIntoView({ block: "center" });
    }
  }, [linked, catalog]);

  return (
    <PageShell>
      <PageScroll>
        <Section
          title={t("nav.sources")}
          description={t("desc.sources")}
          actions={
            catalog && (
              <span className="text-[11px] text-muted-foreground">
                {t("sources.checkedAt", { time: formatDateTime(catalog.checked_at, language) })}
              </span>
            )
          }
        >
          {error ? (
            <StateBlock
              variant={error.code === "desktop_unavailable" ? "offline" : "error"}
              title={t("sources.errorTitle")}
              body={error.message}
              action={
                <Button variant="outline" size="sm" onClick={() => void reload()}>
                  {t("common.retry")}
                </Button>
              }
            />
          ) : catalog === null ? (
            <StateBlock variant="loading" title={t("common.loading")} />
          ) : (
            <div className="space-y-3">
              <p className="text-xs text-muted-foreground">{t("sources.intro")}</p>
              {linked && !catalog.sources.some((source) => source.metadata.key === linked) && (
                <InlineNotice variant="partial">
                  {t("sources.notListed", { key: linked })}
                </InlineNotice>
              )}
              {catalog.sources.map((source) => (
                <SourceCard
                  key={source.metadata.key}
                  source={source}
                  linked={source.metadata.key === linked}
                />
              ))}
            </div>
          )}
        </Section>
      </PageScroll>
    </PageShell>
  );
}
