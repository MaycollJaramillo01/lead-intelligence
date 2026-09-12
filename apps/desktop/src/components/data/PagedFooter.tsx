import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";

interface PagedFooterProps {
  loaded: number;
  total: number;
  hasMore: boolean;
  loading: boolean;
  onLoadMore: () => void;
}

/** How much of a keyset-paginated list is on screen, and the way to fetch the next page. */
export function PagedFooter({ loaded, total, hasMore, loading, onLoadMore }: PagedFooterProps) {
  const t = useT();
  if (total === 0) return null;
  return (
    <div className="flex shrink-0 items-center justify-between gap-3 border-t px-3 py-1.5 text-[11px] text-muted-foreground">
      <span role="status">{t("common.showingOf", { loaded, total })}</span>
      {hasMore && (
        <Button variant="outline" size="sm" className="h-6" disabled={loading} onClick={onLoadMore}>
          {loading ? t("state.loading") : t("common.loadMore")}
        </Button>
      )}
    </div>
  );
}
