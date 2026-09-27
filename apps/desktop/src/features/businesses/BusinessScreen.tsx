import { useEffect } from "react";
import { ArrowLeft } from "lucide-react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router";
import { PageShell } from "@/components/layout/page-chrome";
import { BUSINESS_SECTIONS, BusinessInspector } from "@/features/businesses/BusinessInspector";
import { useT } from "@/lib/i18n";

const jump = (id: string) => {
  const heading = document.getElementById(`business-${id}-title`);
  heading?.scrollIntoView({ block: "start" });
  heading?.focus({ preventScroll: true });
};

/**
 * A business in full (phase 12): the inspector's eleven sections one after another, with a list to
 * jump between them. Opened from the inspector, the timeline, the client list or a
 * `/businesses/<id>` link; `?section=<id>` opens it at one section (phase 16: the script).
 */
export function BusinessScreen() {
  const t = useT();
  const { businessId = "" } = useParams();
  const [params] = useSearchParams();
  const openAt = params.get("section");
  const navigate = useNavigate();
  // a screen that opened this one is where "back" returns; a link from nowhere goes to the explorer
  const opened = useLocation().key !== "default";

  useEffect(() => {
    if (openAt) jump(openAt);
  }, [openAt]);

  const back = (
    <Link
      to="/lead-explorer"
      onClick={(event) => {
        if (!opened) return;
        event.preventDefault();
        void navigate(-1);
      }}
      className="flex items-center gap-1 text-xs text-primary hover:underline"
    >
      <ArrowLeft aria-hidden="true" className="size-3.5" />
      {t(opened ? "business.return" : "business.back")}
    </Link>
  );

  return (
    <PageShell>
      <div className="flex min-h-0 flex-1">
        <nav
          aria-label={t("business.sections")}
          className="hidden w-52 shrink-0 space-y-2 overflow-auto border-r p-2 text-xs md:block"
        >
          {back}
          <ul className="space-y-0.5">
            {BUSINESS_SECTIONS.map((section) => (
              <li key={section.id}>
                <button
                  type="button"
                  onClick={() => jump(section.id)}
                  className="w-full rounded px-1.5 py-1 text-left hover:bg-accent"
                >
                  {t(section.key)}
                </button>
              </li>
            ))}
          </ul>
        </nav>
        <div className="min-h-0 flex-1 overflow-auto p-4">
          <div className="mx-auto w-full max-w-4xl space-y-3">
            <div className="md:hidden">{back}</div>
            <BusinessInspector key={businessId} businessId={businessId} layout="page" />
          </div>
        </div>
      </div>
    </PageShell>
  );
}
