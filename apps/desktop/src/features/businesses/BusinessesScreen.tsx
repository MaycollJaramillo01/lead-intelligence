import { useCallback, useEffect, useMemo, useState } from "react";
import { FlaskConical, Search } from "lucide-react";
import { toast } from "sonner";
import { filterFieldsFrom } from "@/components/data/filters";
import { PagedFooter } from "@/components/data/PagedFooter";
import { StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { buildBusinessColumns } from "@/features/businesses/columns";
import { TableWorkspace } from "@/features/businesses/TableWorkspace";
import { useT } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { useKeysetPages } from "@/lib/useKeysetPages";
import { useUiStore } from "@/stores/ui";

export const BUSINESS_PAGE_SIZE = 100;
const SEARCH_DEBOUNCE_MS = 250;

export function BusinessesScreen() {
  const t = useT();
  const inspect = useUiStore((state) => state.inspect);
  const columns = useMemo(() => buildBusinessColumns(t), [t]);
  const filterFields = useMemo(() => filterFieldsFrom(columns), [columns]);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [seeding, setSeeding] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [search]);

  const fetchPage = useCallback(
    (cursor: string | null) =>
      desktop.listBusinesses({
        project_id: null,
        search: query || null,
        status: null,
        sort: "name",
        limit: BUSINESS_PAGE_SIZE,
        cursor,
      }),
    [query],
  );
  const pages = useKeysetPages(fetchPage, "list_businesses");

  const seed = async () => {
    setSeeding(true);
    try {
      const result = await desktop.seedSyntheticData();
      toast.success(t(result.created ? "businesses.seedDone" : "businesses.seedReused"));
      setSearch("");
      await pages.reload();
      inspect(result.business_id);
    } catch (cause) {
      toast.error(t("toast.actionFailed", { action: t("businesses.seed") }), {
        description: toIpcError(cause, "seed_synthetic_data").message,
      });
    } finally {
      setSeeding(false);
    }
  };

  const emptyState = pages.error ? (
    <StateBlock
      variant={pages.error.code === "desktop_unavailable" ? "offline" : "error"}
      title={t("businesses.errorTitle")}
      body={pages.error.message}
      action={
        <Button variant="outline" size="sm" onClick={() => void pages.reload()}>
          {t("common.retry")}
        </Button>
      }
    />
  ) : query ? (
    <StateBlock variant="empty" title={t("businesses.noMatchesTitle")} />
  ) : (
    <StateBlock
      variant="empty"
      title={t("businesses.emptyTitle")}
      body={
        <>
          <p>{t("businesses.emptyBody")}</p>
          {import.meta.env.DEV && <p className="mt-1">{t("businesses.emptyBodyDev")}</p>}
        </>
      }
    />
  );

  return (
    <TableWorkspace
      tableId="businesses"
      columns={columns}
      data={pages.items}
      getRowId={(row) => row.id}
      filterFields={filterFields}
      isLoading={pages.loading && pages.items.length === 0 && !pages.error}
      emptyState={emptyState}
      actions={
        <>
          <div className="relative">
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground"
            />
            <Input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={t("businesses.searchPlaceholder")}
              aria-label={t("businesses.searchLabel")}
              maxLength={200}
              className="h-7 w-56 pl-7 text-xs"
            />
          </div>
          {import.meta.env.DEV && (
            <Button
              variant="outline"
              size="sm"
              className="h-7 border-warn/60 text-warn"
              disabled={seeding}
              onClick={() => void seed()}
            >
              <FlaskConical aria-hidden="true" />
              {t("businesses.seed")}
              <span className="rounded bg-warn/15 px-1 text-[10px]">{t("common.devOnly")}</span>
            </Button>
          )}
        </>
      }
      footer={
        <PagedFooter
          loaded={pages.items.length}
          total={pages.total}
          hasMore={pages.nextCursor !== null}
          loading={pages.loading}
          onLoadMore={() => void pages.loadMore()}
        />
      }
    />
  );
}
