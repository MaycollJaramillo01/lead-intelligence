import { useMemo } from "react";
import {
  buildBusinessColumns,
  filterFieldsFrom,
  type BusinessRow,
} from "@/features/businesses/columns";
import { TableWorkspace } from "@/features/businesses/TableWorkspace";
import { useT } from "@/lib/i18n";

/** No data source is connected yet (phase 03); the table itself is real. */
const BUSINESSES: BusinessRow[] = [];

export function BusinessesScreen() {
  const t = useT();
  const columns = useMemo(() => buildBusinessColumns(t), [t]);
  const filterFields = useMemo(() => filterFieldsFrom(columns), [columns]);

  return (
    <TableWorkspace
      tableId="businesses"
      columns={columns}
      data={BUSINESSES}
      getRowId={(row) => row.id}
      filterFields={filterFields}
      emptyTitle={t("businesses.emptyTitle")}
      emptyBody={t("businesses.emptyBody")}
    />
  );
}
