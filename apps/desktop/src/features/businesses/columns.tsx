import type { ColumnDef } from "@tanstack/react-table";
import type { FilterField } from "@/components/data/filters";
import { Mono, Text } from "@/features/businesses/cells";
import type { Translator } from "@/lib/i18n";

/**
 * Provisional row shapes for the research tables. The canonical data model arrives in phase 03;
 * these describe what the analyst needs to see, so the table can be built and tested today.
 */
export interface BusinessRow {
  id: string;
  name: string;
  location: string | null;
  vertical: string | null;
  website: string | null;
  phone: string | null;
  status: string | null;
  sources: number | null;
  lastSeen: string | null;
}

export interface LeadRow extends BusinessRow {
  stage: string | null;
  score: number | null;
  latinoEvidence: string | null;
  digitalGap: number | null;
  contactability: number | null;
  opportunity: string | null;
  freshness: string | null;
}

export function buildBusinessColumns(t: Translator): ColumnDef<BusinessRow>[] {
  return [
    {
      accessorKey: "name",
      header: t("businesses.column.name"),
      size: 220,
      cell: ({ getValue }) => <Text value={getValue<string>()} />,
    },
    {
      accessorKey: "location",
      header: t("businesses.column.location"),
      size: 180,
      cell: ({ getValue }) => <Text value={getValue<string | null>()} />,
    },
    {
      accessorKey: "vertical",
      header: t("businesses.column.vertical"),
      size: 150,
      cell: ({ getValue }) => <Text value={getValue<string | null>()} />,
    },
    {
      accessorKey: "website",
      header: t("businesses.column.website"),
      size: 190,
      cell: ({ getValue }) => <Mono value={getValue<string | null>()} />,
    },
    {
      accessorKey: "phone",
      header: t("businesses.column.phone"),
      size: 130,
      cell: ({ getValue }) => <Mono value={getValue<string | null>()} />,
    },
    {
      accessorKey: "status",
      header: t("businesses.column.status"),
      size: 120,
      cell: ({ getValue }) => <Text value={getValue<string | null>()} />,
    },
    {
      accessorKey: "sources",
      header: t("businesses.column.sources"),
      size: 90,
      cell: ({ getValue }) => <Mono value={getValue<number | null>()} />,
    },
    {
      accessorKey: "lastSeen",
      header: t("businesses.column.lastSeen"),
      size: 140,
      cell: ({ getValue }) => <Mono value={getValue<string | null>()} />,
    },
  ];
}

export function buildLeadColumns(t: Translator): ColumnDef<LeadRow>[] {
  return [
    {
      accessorKey: "name",
      header: t("businesses.column.name"),
      size: 220,
      cell: ({ getValue }) => <Text value={getValue<string>()} />,
    },
    {
      accessorKey: "location",
      header: t("businesses.column.location"),
      size: 170,
      cell: ({ getValue }) => <Text value={getValue<string | null>()} />,
    },
    {
      accessorKey: "vertical",
      header: t("businesses.column.vertical"),
      size: 140,
      cell: ({ getValue }) => <Text value={getValue<string | null>()} />,
    },
    {
      accessorKey: "stage",
      header: t("businesses.column.stage"),
      size: 120,
      cell: ({ getValue }) => <Text value={getValue<string | null>()} />,
    },
    {
      accessorKey: "score",
      header: t("businesses.column.score"),
      size: 110,
      cell: ({ getValue }) => <Mono value={getValue<number | null>()} />,
    },
    {
      accessorKey: "latinoEvidence",
      header: t("businesses.column.latino"),
      size: 160,
      cell: ({ getValue }) => <Text value={getValue<string | null>()} />,
    },
    {
      accessorKey: "digitalGap",
      header: t("businesses.column.digitalGap"),
      size: 120,
      cell: ({ getValue }) => <Mono value={getValue<number | null>()} />,
    },
    {
      accessorKey: "contactability",
      header: t("businesses.column.contactability"),
      size: 140,
      cell: ({ getValue }) => <Mono value={getValue<number | null>()} />,
    },
    {
      accessorKey: "opportunity",
      header: t("businesses.column.opportunity"),
      size: 200,
      cell: ({ getValue }) => <Text value={getValue<string | null>()} />,
    },
    {
      accessorKey: "freshness",
      header: t("businesses.column.freshness"),
      size: 120,
      cell: ({ getValue }) => <Text value={getValue<string | null>()} />,
    },
  ];
}

export function filterFieldsFrom<TData>(columns: ColumnDef<TData>[]): FilterField[] {
  return columns.flatMap((column) => {
    const id = "accessorKey" in column ? String(column.accessorKey) : column.id;
    const label = typeof column.header === "string" ? column.header : (id ?? "");
    return id ? [{ id, label }] : [];
  });
}
