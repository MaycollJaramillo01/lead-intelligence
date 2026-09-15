import { useCallback, useEffect, useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import type { DatasetListItem } from "@lead/shared-types";
import { useSearchParams } from "react-router";
import { DataTable } from "@/components/data/DataTable";
import { DateTime, Mono, TestDataBadge, Text } from "@/components/data/cells";
import { filterFieldsFrom, type FilterCondition } from "@/components/data/filters";
import { PagedFooter } from "@/components/data/PagedFooter";
import { PageShell } from "@/components/layout/page-chrome";
import { StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { SnapshotsPanel } from "@/features/datasets/SnapshotsPanel";
import { useT, type Translator } from "@/lib/i18n";
import { desktop } from "@/lib/ipc";
import { useKeysetPages } from "@/lib/useKeysetPages";

const PAGE_SIZE = 100;

function buildColumns(t: Translator): ColumnDef<DatasetListItem>[] {
  return [
    {
      id: "name",
      accessorFn: (row) => row.dataset.name,
      header: t("datasets.column.name"),
      size: 260,
      cell: ({ row }) => (
        <span className="flex min-w-0 items-center gap-1.5">
          <Text value={row.original.dataset.name} />
          {row.original.is_synthetic && <TestDataBadge />}
        </span>
      ),
    },
    {
      id: "snapshots",
      accessorKey: "snapshot_count",
      header: t("datasets.column.snapshots"),
      size: 100,
      cell: ({ getValue }) => <Mono value={getValue<number>()} />,
    },
    {
      id: "version",
      accessorFn: (row) => row.latest_snapshot?.version ?? null,
      header: t("datasets.column.version"),
      size: 120,
      cell: ({ getValue }) => <Mono value={getValue<number | null>()} />,
    },
    {
      id: "rows",
      accessorFn: (row) => row.latest_snapshot?.row_count ?? null,
      header: t("datasets.column.rows"),
      size: 100,
      cell: ({ getValue }) => <Mono value={getValue<number | null>()} />,
    },
    {
      id: "captured",
      accessorFn: (row) => row.latest_snapshot?.created_at ?? null,
      header: t("datasets.column.captured"),
      size: 180,
      cell: ({ getValue }) => <DateTime value={getValue<string | null>()} />,
    },
  ];
}

export function DatasetsScreen() {
  const t = useT();
  const columns = useMemo(() => buildColumns(t), [t]);
  const filterFields = useMemo(() => filterFieldsFrom(columns), [columns]);
  const [conditions, setConditions] = useState<FilterCondition[]>([]);
  const fetchPage = useCallback(
    (cursor: string | null) => desktop.listDatasets({ project_id: null, limit: PAGE_SIZE, cursor }),
    [],
  );
  const pages = useKeysetPages(fetchPage, "list_datasets");
  // a `?dataset=` link opens its dataset; a `?snapshot=` link, the dataset of its snapshot
  const [params, setParams] = useSearchParams();
  const activeId = params.get("dataset");
  const linkedSnapshot = params.get("snapshot");
  const active = pages.items.find((item) => item.dataset.id === activeId) ?? null;

  useEffect(() => {
    if (linkedSnapshot === null || activeId !== null) return;
    let stale = false;
    desktop.getDatasetSnapshot({ snapshot_id: linkedSnapshot }).then(
      (detail) => {
        if (!stale) {
          const next = { dataset: detail.item.snapshot.dataset_id, snapshot: linkedSnapshot };
          setParams(next, { replace: true });
        }
      },
      () => undefined,
    );
    return () => {
      stale = true;
    };
  }, [linkedSnapshot, activeId, setParams]);

  return (
    <PageShell>
      <div className="min-h-0 flex-1">
        <DataTable
          tableId="datasets"
          columns={columns}
          data={pages.items}
          getRowId={(row) => row.dataset.id}
          filterFields={filterFields}
          conditions={conditions}
          onConditionsChange={setConditions}
          pinnedLeft={["name"]}
          activeRowId={activeId}
          onActivateRow={(id) => setParams(id === null ? {} : { dataset: id })}
          isLoading={pages.loading && pages.items.length === 0 && !pages.error}
          emptyState={
            pages.error ? (
              <StateBlock
                variant={pages.error.code === "desktop_unavailable" ? "offline" : "error"}
                title={t("datasets.errorTitle")}
                body={pages.error.message}
                action={
                  <Button variant="outline" size="sm" onClick={() => void pages.reload()}>
                    {t("common.retry")}
                  </Button>
                }
              />
            ) : (
              <StateBlock
                variant="empty"
                title={t("datasets.emptyTitle")}
                body={t("datasets.emptyBody")}
              />
            )
          }
        />
      </div>
      <PagedFooter
        loaded={pages.items.length}
        total={pages.total}
        hasMore={pages.nextCursor !== null}
        loading={pages.loading}
        onLoadMore={() => void pages.loadMore()}
      />
      {active && (
        <div className="max-h-[55%] min-h-0 overflow-auto border-t">
          <SnapshotsPanel
            key={active.dataset.id}
            dataset={active}
            linkedSnapshot={linkedSnapshot}
            onChanged={() => void pages.reload()}
          />
        </div>
      )}
    </PageShell>
  );
}
