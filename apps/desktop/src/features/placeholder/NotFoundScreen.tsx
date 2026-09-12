import { Link, useLocation } from "react-router";
import { DEFAULT_ROUTE } from "@/app/navigation";
import { PageScroll, PageShell } from "@/components/layout/page-chrome";
import { StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";

export function NotFoundScreen() {
  const t = useT();
  const { pathname } = useLocation();

  return (
    <PageShell>
      <PageScroll>
        <StateBlock
          variant="blocked"
          title={t("state.empty")}
          body={<span className="font-mono">{pathname}</span>}
          action={
            <Button asChild size="sm" variant="outline">
              <Link to={DEFAULT_ROUTE}>{t("nav.overview")}</Link>
            </Button>
          }
        />
      </PageScroll>
    </PageShell>
  );
}
