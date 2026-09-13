import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { FlaskConical, Search } from "lucide-react";
import { toast } from "sonner";
import { filterFieldsFrom } from "@/components/data/filters";
import { PagedFooter } from "@/components/data/PagedFooter";
import { StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { buildBusinessColumns } from "@/features/businesses/columns";
import { TableWorkspace } from "@/features/businesses/TableWorkspace";
import { NO_LATINO_FILTER, latinoQuery, type LatinoFilter } from "@/features/latino/filters";
import { LatinoFilters } from "@/features/latino/LatinoFilters";
import { DuplicatesView } from "@/features/resolution/DuplicatesView";
import { useT } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { useKeysetPages } from "@/lib/useKeysetPages";
import { cn } from "@/lib/utils";
import { useUiStore } from "@/stores/ui";

export const BUSINESS_PAGE_SIZE = 100;
const SEARCH_DEBOUNCE_MS = 250;

type View = "businesses" | "duplicates";

function ViewToggle({ view, onChange }: { view: View; onChange: (view: View) => void }) {
  const t = useT();
  const options: Array<[View, string]> = [
    ["businesses", t("resolution.viewBusinesses")],
    ["duplicates", t("resolution.viewDuplicates")],
  ];
  return (
    <div
      role="group"
      aria-label={t("resolution.viewLabel")}
      className="flex h-7 overflow-hidden rounded-md border text-xs"
    >
      {options.map(([value, label]) => (
        <button
          key={value}
          type="button"
          aria-pressed={view === value}
          onClick={() => onChange(value)}
          className={cn(
            "px-2",
            view === value ? "bg-accent font-medium" : "text-muted-foreground hover:bg-accent/50",
          )}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

/** Canonical businesses, and the queue of possible duplicates that resolves them (phase 06). */
export function BusinessesScreen() {
  const [view, setView] = useState<View>("businesses");
  const toggle = <ViewToggle view={view} onChange={setView} />;
  return view === "duplicates" ? (
    <DuplicatesView toggle={toggle} />
  ) : (
    <BusinessList toggle={toggle} />
  );
}

function BusinessList({ toggle }: { toggle: ReactNode }) {
  const t = useT();
  const inspect = useUiStore((state) => state.inspect);
  const columns = useMemo(() => buildBusinessColumns(t), [t]);
  const filterFields = useMemo(() => filterFieldsFrom(columns), [columns]);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [latino, setLatino] = useState<LatinoFilter>(NO_LATINO_FILTER);
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
        ...latinoQuery(latino),
        sort: "name",
        limit: BUSINESS_PAGE_SIZE,
        cursor,
      }),
    [query, latino],
  );
  const pages = useKeysetPages(fetchPage, "list_businesses");
  const filtered = Object.values(latino).some(Boolean);

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
  ) : query || filtered ? (
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
      toolbar={<LatinoFilters value={latino} onChange={setLatino} onEvaluated={pages.reload} />}
      actions={
        <>
          {toggle}
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
