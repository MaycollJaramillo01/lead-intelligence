import { useMemo } from "react";
import { filterFieldsFrom } from "@/components/data/filters";
import { StateBlock } from "@/components/states/StateBlock";
import { buildLeadColumns, type LeadRow } from "@/features/businesses/columns";
import { TableWorkspace } from "@/features/businesses/TableWorkspace";
import { useT } from "@/lib/i18n";

/** Leads exist once ICP matching and scoring run (phase 11). */
const LEADS: LeadRow[] = [];

export function LeadExplorerScreen() {
  const t = useT();
  const columns = useMemo(() => buildLeadColumns(t), [t]);
  const filterFields = useMemo(() => filterFieldsFrom(columns), [columns]);

  return (
    <TableWorkspace
      tableId="lead-explorer"
      columns={columns}
      data={LEADS}
      getRowId={(row) => row.id}
      filterFields={filterFields}
      emptyState={
        <StateBlock variant="empty" title={t("leads.emptyTitle")} body={t("leads.emptyBody")} />
      }
    />
  );
}
