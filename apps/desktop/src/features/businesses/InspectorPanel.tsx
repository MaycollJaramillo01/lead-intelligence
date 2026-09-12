import { X } from "lucide-react";
import { StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { BusinessInspector } from "@/features/businesses/BusinessInspector";
import { useT } from "@/lib/i18n";
import { useUiStore } from "@/stores/ui";

/** Contextual inspector: the selected business with every value and where it came from. */
export function InspectorPanel({ entityId }: { entityId: string | null }) {
  const t = useT();
  const setInspectorOpen = useUiStore((state) => state.setInspectorOpen);

  return (
    <aside
      aria-label={t("common.inspector")}
      className="flex h-full min-h-0 flex-col border-l bg-card"
    >
      <header
        className="flex shrink-0 items-center justify-between gap-2 border-b px-3"
        style={{ height: "calc(var(--row-height) + 0.5rem)" }}
      >
        <h2 className="truncate text-xs font-medium">{t("common.inspector")}</h2>
        <Button
          variant="ghost"
          size="icon"
          className="size-6"
          aria-label={t("common.closeInspector")}
          onClick={() => setInspectorOpen(false)}
        >
          <X aria-hidden="true" className="size-3.5" />
        </Button>
      </header>

      <div className="min-h-0 flex-1 overflow-auto p-3">
        {entityId === null ? (
          <StateBlock
            variant="empty"
            title={t("businesses.inspectorEmptyTitle")}
            body={t("businesses.inspectorEmptyBody")}
          />
        ) : (
          <BusinessInspector key={entityId} businessId={entityId} />
        )}
      </div>
    </aside>
  );
}
