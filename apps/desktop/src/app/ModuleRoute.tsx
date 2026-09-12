import type { ComponentType } from "react";
import type { NavItem } from "@/app/navigation";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { SystemScreen } from "@/features/diagnostics/SystemScreen";
import { BusinessesScreen } from "@/features/businesses/BusinessesScreen";
import { CampaignsScreen } from "@/features/campaigns/CampaignsScreen";
import { DiscoveryScreen } from "@/features/discovery/DiscoveryScreen";
import { SourcesScreen } from "@/features/sources/SourcesScreen";
import { LeadExplorerScreen } from "@/features/businesses/LeadExplorerScreen";
import { DatasetsScreen } from "@/features/datasets/DatasetsScreen";
import { JobsScreen } from "@/features/jobs/JobsScreen";
import { OverviewScreen } from "@/features/overview/OverviewScreen";
import { ResearchCenterScreen } from "@/features/research/ResearchCenterScreen";
import { ModuleNotConfigured } from "@/features/placeholder/ModuleNotConfigured";
import { SettingsScreen } from "@/features/settings/SettingsScreen";

/** Modules that exist today. Everything else renders its honest "not configured" page. */
const SCREENS: Record<string, ComponentType> = {
  overview: OverviewScreen,
  discovery: DiscoveryScreen,
  campaigns: CampaignsScreen,
  sources: SourcesScreen,
  businesses: BusinessesScreen,
  "lead-explorer": LeadExplorerScreen,
  "research-center": ResearchCenterScreen,
  jobs: JobsScreen,
  datasets: DatasetsScreen,
  system: SystemScreen,
  settings: SettingsScreen,
};

/** Every module is wrapped in its own error boundary: one broken screen cannot kill the shell. */
export function ModuleRoute({ item }: { item: NavItem }) {
  const Screen = SCREENS[item.id];
  return (
    <ErrorBoundary scope={item.id}>
      {Screen ? <Screen /> : <ModuleNotConfigured item={item} />}
    </ErrorBoundary>
  );
}
