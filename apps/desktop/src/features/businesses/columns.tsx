import type { ColumnDef } from "@tanstack/react-table";
import type { BusinessListItem, BusinessStatus } from "@lead/shared-types";
import { DateTime, Mono, TestDataBadge, Text } from "@/components/data/cells";
import { LATINO_STATUS_KEYS } from "@/features/latino/labels";
import type { TranslationKey, Translator } from "@/lib/i18n";

export const BUSINESS_STATUS_KEYS: Record<BusinessStatus, TranslationKey> = {
  candidate: "business.status.candidate",
  active: "business.status.active",
  closed: "business.status.closed",
  merged: "business.status.merged",
};

export function buildBusinessColumns(t: Translator): ColumnDef<BusinessListItem>[] {
  return [
    {
      id: "name",
      accessorKey: "display_name",
      header: t("businesses.column.name"),
      size: 300,
      cell: ({ row }) => (
        <span className="flex min-w-0 items-center gap-1.5">
          <Text value={row.original.display_name} />
          {row.original.member_count > 1 && (
            <span className="shrink-0 rounded border px-1 text-[10px] text-muted-foreground">
              {t("businesses.records", { n: row.original.member_count })}
            </span>
          )}
          {row.original.conflict_count > 0 && (
            <span className="shrink-0 rounded border border-warn/60 px-1 text-[10px] text-warn">
              {t("businesses.conflicts", { n: row.original.conflict_count })}
            </span>
          )}
          {row.original.is_synthetic && <TestDataBadge />}
        </span>
      ),
    },
    {
      id: "location",
      accessorFn: (row) => [row.locality, row.region].filter(Boolean).join(", ") || null,
      header: t("businesses.column.location"),
      size: 170,
      cell: ({ getValue }) => <Text value={getValue<string | null>()} />,
    },
    {
      id: "vertical",
      accessorKey: "primary_category",
      header: t("businesses.column.vertical"),
      size: 150,
      cell: ({ getValue }) => <Text value={getValue<string | null>()} />,
    },
    {
      id: "website",
      accessorKey: "primary_domain",
      header: t("businesses.column.website"),
      size: 220,
      cell: ({ getValue }) => <Mono value={getValue<string | null>()} />,
    },
    {
      id: "phone",
      accessorKey: "primary_phone",
      header: t("businesses.column.phone"),
      size: 130,
      cell: ({ getValue }) => <Mono value={getValue<string | null>()} />,
    },
    {
      id: "status",
      accessorFn: (row) => t(BUSINESS_STATUS_KEYS[row.status]),
      header: t("businesses.column.status"),
      size: 110,
      cell: ({ getValue }) => <Text value={getValue<string>()} />,
    },
    {
      id: "latino",
      accessorFn: (row) => t(LATINO_STATUS_KEYS[row.latino_status]),
      header: t("businesses.column.latino"),
      size: 240,
      cell: ({ row, getValue }) => (
        <span className="flex min-w-0 items-center gap-1.5">
          <Text value={getValue<string>()} />
          {row.original.latino_status !== "unknown" && row.original.latino_confidence !== null && (
            <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
              {Math.round(row.original.latino_confidence * 100)}%
            </span>
          )}
          {row.original.latino_reviewed && (
            <span className="shrink-0 rounded border px-1 text-[10px] text-muted-foreground">
              {t("latino.reviewedBadge")}
            </span>
          )}
        </span>
      ),
    },
    {
      id: "sources",
      accessorKey: "source_count",
      header: t("businesses.column.sources"),
      size: 90,
      cell: ({ getValue }) => <Mono value={getValue<number>()} />,
    },
    {
      id: "lastSeen",
      accessorKey: "last_seen_at",
      header: t("businesses.column.lastSeen"),
      size: 170,
      cell: ({ getValue }) => <DateTime value={getValue<string>()} />,
    },
  ];
}
