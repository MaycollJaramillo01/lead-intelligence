import type { ColumnDef } from "@tanstack/react-table";
import type {
  BusinessListItem,
  BusinessStatus,
  LeadScoreItem,
  ScoreComponent,
} from "@lead/shared-types";
import { DateTime, Mono, TestDataBadge, Text } from "@/components/data/cells";
import { LATINO_STATUS_KEYS } from "@/features/latino/labels";
import { STAGE_KEYS, TIER_KEYS, scoreText } from "@/features/scoring/labels";
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

function componentValue(row: LeadScoreItem, component: ScoreComponent): number | null {
  return row.components.find((item) => item.component === component)?.value ?? null;
}

/** The ranked leads of a score run (phase 11): every number next to its tier and its components. */
export function buildLeadColumns(t: Translator): ColumnDef<LeadScoreItem>[] {
  const component = (
    id: string,
    name: ScoreComponent,
    header: string,
  ): ColumnDef<LeadScoreItem> => ({
    id,
    accessorFn: (row) => componentValue(row, name),
    header,
    size: 120,
    cell: ({ getValue }) => <Mono value={scoreText(getValue<number | null>())} />,
  });
  return [
    {
      id: "rank",
      accessorKey: "rank",
      header: t("businesses.column.rank"),
      size: 70,
      cell: ({ getValue }) => <Mono value={getValue<number>()} />,
    },
    {
      id: "name",
      accessorKey: "display_name",
      header: t("businesses.column.name"),
      size: 240,
      cell: ({ row }) => (
        <span className="flex min-w-0 items-center gap-1.5">
          <Text value={row.original.display_name} />
          {row.original.is_synthetic && <TestDataBadge />}
        </span>
      ),
    },
    {
      id: "tier",
      accessorFn: (row) => t(TIER_KEYS[row.tier]),
      header: t("businesses.column.tier"),
      size: 130,
      cell: ({ getValue }) => <Text value={getValue<string>()} />,
    },
    {
      id: "score",
      accessorFn: (row) => row.total_score,
      header: t("businesses.column.score"),
      size: 100,
      cell: ({ getValue }) => <Mono value={scoreText(getValue<number | null>())} />,
    },
    {
      id: "confidence",
      accessorFn: (row) => Math.round(row.confidence * 100),
      header: t("common.confidence"),
      size: 100,
      cell: ({ getValue }) => <Mono value={`${getValue<number>()}%`} />,
    },
    component("icp", "ICP_MATCH", t("scoring.component.ICP_MATCH")),
    component("opportunityScore", "OPPORTUNITY_SCORE", t("scoring.component.OPPORTUNITY_SCORE")),
    component("digitalGap", "DIGITAL_GAP", t("businesses.column.digitalGap")),
    component("contactability", "CONTACTABILITY", t("businesses.column.contactability")),
    {
      id: "latino",
      accessorFn: (row) => t(LATINO_STATUS_KEYS[row.latino_status]),
      header: t("businesses.column.latino"),
      size: 200,
      cell: ({ getValue }) => <Text value={getValue<string>()} />,
    },
    {
      id: "offer",
      accessorKey: "offer_key",
      header: t("businesses.column.offer"),
      size: 150,
      cell: ({ getValue }) => <Mono value={getValue<string | null>()} />,
    },
    {
      id: "opportunity",
      accessorKey: "top_reason",
      header: t("businesses.column.opportunity"),
      size: 280,
      cell: ({ getValue }) => <Text value={getValue<string | null>()} />,
    },
    {
      id: "stage",
      accessorFn: (row) => t(STAGE_KEYS[row.lead_stage]),
      header: t("businesses.column.stage"),
      size: 130,
      cell: ({ getValue }) => <Text value={getValue<string>()} />,
    },
    {
      id: "location",
      accessorFn: (row) => [row.locality, row.region].filter(Boolean).join(", ") || null,
      header: t("businesses.column.location"),
      size: 160,
      cell: ({ getValue }) => <Text value={getValue<string | null>()} />,
    },
    {
      id: "vertical",
      accessorKey: "primary_category",
      header: t("businesses.column.vertical"),
      size: 150,
      cell: ({ getValue }) => <Text value={getValue<string | null>()} />,
    },
  ];
}
