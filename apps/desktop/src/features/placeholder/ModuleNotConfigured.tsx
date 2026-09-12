import { Link } from "react-router";
import { AVAILABLE_NAV_ITEMS, type NavItem } from "@/app/navigation";
import { PageScroll, PageShell, Section } from "@/components/layout/page-chrome";
import { StateBlock } from "@/components/states/StateBlock";
import { useT } from "@/lib/i18n";

/** Honest page for a module that does not exist yet: no fake metrics, no disabled theatre. */
export function ModuleNotConfigured({ item }: { item: NavItem }) {
  const t = useT();
  const Icon = item.icon;

  return (
    <PageShell>
      <PageScroll>
        <Section
          title={t(item.titleKey)}
          description={t(item.descriptionKey)}
          actions={
            <span className="rounded border px-2 py-0.5 text-[11px] text-muted-foreground">
              {t("common.notConfigured")}
            </span>
          }
        >
          <StateBlock
            variant="blocked"
            title={t("module.notConfiguredTitle")}
            body={
              <>
                <p>{t("module.notConfiguredBody")}</p>
                {item.phase !== undefined && (
                  <p className="mt-1 font-medium">
                    {t("common.availableInPhase", { phase: item.phase })}
                  </p>
                )}
              </>
            }
          />
          <div className="mt-4 border-t pt-3">
            <h3 className="mb-2 text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
              {t("module.relatedAvailable")}
            </h3>
            <ul className="flex flex-wrap gap-2">
              {AVAILABLE_NAV_ITEMS.map((available) => (
                <li key={available.id}>
                  <Link
                    to={available.path}
                    className="inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs hover:bg-accent"
                  >
                    {t(available.titleKey)}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
          <p className="sr-only">
            <Icon aria-hidden="true" />
          </p>
        </Section>
      </PageScroll>
    </PageShell>
  );
}
