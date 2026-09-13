import {
  Activity,
  Building2,
  Cpu,
  Database,
  Download,
  Gauge,
  LayoutDashboard,
  ListChecks,
  Megaphone,
  Microscope,
  Plug,
  Radar,
  ScrollText,
  Settings,
  Target,
  Telescope,
  type LucideIcon,
} from "lucide-react";
import type { TranslationKey } from "@/lib/i18n";

export type NavSectionId =
  "overview" | "acquisition" | "research" | "intelligence" | "operations" | "system";

/** A module is either usable today or openly announced as not built yet. */
export type ModuleStatus = "available" | "not_configured";

export interface NavItem {
  id: string;
  path: string;
  icon: LucideIcon;
  section: NavSectionId;
  status: ModuleStatus;
  /** Delivery phase that turns the module on; only set when not configured. */
  phase?: number;
  titleKey: TranslationKey;
  descriptionKey: TranslationKey;
}

export const NAV_SECTIONS: ReadonlyArray<{ id: NavSectionId; titleKey: TranslationKey }> = [
  { id: "overview", titleKey: "nav.section.overview" },
  { id: "acquisition", titleKey: "nav.section.acquisition" },
  { id: "research", titleKey: "nav.section.research" },
  { id: "intelligence", titleKey: "nav.section.intelligence" },
  { id: "operations", titleKey: "nav.section.operations" },
  { id: "system", titleKey: "nav.section.system" },
];

export const NAV_ITEMS: readonly NavItem[] = [
  {
    id: "overview",
    path: "/overview",
    icon: LayoutDashboard,
    section: "overview",
    status: "available",
    titleKey: "nav.overview",
    descriptionKey: "desc.overview",
  },
  {
    id: "discovery",
    path: "/discovery",
    icon: Radar,
    section: "acquisition",
    status: "available",
    titleKey: "nav.discovery",
    descriptionKey: "desc.discovery",
  },
  {
    id: "campaigns",
    path: "/campaigns",
    icon: Megaphone,
    section: "acquisition",
    status: "available",
    titleKey: "nav.campaigns",
    descriptionKey: "desc.campaigns",
  },
  {
    id: "sources",
    path: "/sources",
    icon: Plug,
    section: "acquisition",
    status: "available",
    titleKey: "nav.sources",
    descriptionKey: "desc.sources",
  },
  {
    id: "businesses",
    path: "/businesses",
    icon: Building2,
    section: "research",
    status: "available",
    titleKey: "nav.businesses",
    descriptionKey: "desc.businesses",
  },
  {
    id: "lead-explorer",
    path: "/lead-explorer",
    icon: Telescope,
    section: "research",
    status: "available",
    titleKey: "nav.lead_explorer",
    descriptionKey: "desc.lead_explorer",
  },
  {
    id: "research-center",
    path: "/research-center",
    icon: Microscope,
    section: "research",
    status: "available",
    titleKey: "nav.research_center",
    descriptionKey: "desc.research_center",
  },
  {
    id: "icp-builder",
    path: "/icp-builder",
    icon: Target,
    section: "intelligence",
    status: "not_configured",
    phase: 11,
    titleKey: "nav.icp_builder",
    descriptionKey: "desc.icp_builder",
  },
  {
    id: "scoring-models",
    path: "/scoring-models",
    icon: Gauge,
    section: "intelligence",
    status: "not_configured",
    phase: 11,
    titleKey: "nav.scoring_models",
    descriptionKey: "desc.scoring_models",
  },
  {
    id: "ai-models",
    path: "/ai-models",
    icon: Cpu,
    section: "intelligence",
    status: "available",
    titleKey: "nav.ai_models",
    descriptionKey: "desc.ai_models",
  },
  {
    id: "jobs",
    path: "/jobs",
    icon: ListChecks,
    section: "operations",
    status: "available",
    titleKey: "nav.jobs",
    descriptionKey: "desc.jobs",
  },
  {
    id: "datasets",
    path: "/datasets",
    icon: Database,
    section: "operations",
    status: "available",
    titleKey: "nav.datasets",
    descriptionKey: "desc.datasets",
  },
  {
    id: "exports",
    path: "/exports",
    icon: Download,
    section: "operations",
    status: "not_configured",
    phase: 13,
    titleKey: "nav.exports",
    descriptionKey: "desc.exports",
  },
  {
    id: "system",
    path: "/system",
    icon: Activity,
    section: "system",
    status: "available",
    titleKey: "nav.system",
    descriptionKey: "desc.system",
  },
  {
    id: "logs",
    path: "/logs",
    icon: ScrollText,
    section: "system",
    status: "not_configured",
    phase: 13,
    titleKey: "nav.logs",
    descriptionKey: "desc.logs",
  },
  {
    id: "settings",
    path: "/settings",
    icon: Settings,
    section: "system",
    status: "available",
    titleKey: "nav.settings",
    descriptionKey: "desc.settings",
  },
];

export const DEFAULT_ROUTE = "/overview";

export function navItemsBySection(section: NavSectionId): NavItem[] {
  return NAV_ITEMS.filter((item) => item.section === section);
}

export function findNavItemByPath(pathname: string): NavItem | undefined {
  return NAV_ITEMS.find((item) => item.path === pathname);
}

export function findNavItem(id: string): NavItem | undefined {
  return NAV_ITEMS.find((item) => item.id === id);
}

export const AVAILABLE_NAV_ITEMS: readonly NavItem[] = NAV_ITEMS.filter(
  (item) => item.status === "available",
);
