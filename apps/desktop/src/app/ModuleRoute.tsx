import type { ComponentType } from "react";
import type { NavItem } from "@/app/navigation";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { AiModelsScreen } from "@/features/ai/AiModelsScreen";
import { SystemScreen } from "@/features/diagnostics/SystemScreen";
import { BusinessesScreen } from "@/features/businesses/BusinessesScreen";
import { CampaignsScreen } from "@/features/campaigns/CampaignsScreen";
import { DiscoveryScreen } from "@/features/discovery/DiscoveryScreen";
import { SourcesScreen } from "@/features/sources/SourcesScreen";
import { LeadExplorerScreen } from "@/features/leads/LeadExplorerScreen";
import { DatasetsScreen } from "@/features/datasets/DatasetsScreen";
import { ExportsScreen } from "@/features/exports/ExportsScreen";
import { JobsScreen } from "@/features/jobs/JobsScreen";
import { LogsScreen } from "@/features/logs/LogsScreen";
import { OverviewScreen } from "@/features/overview/OverviewScreen";
import { IcpBuilderScreen } from "@/features/scoring/IcpBuilderScreen";
import { ScoringModelsScreen } from "@/features/scoring/ScoringModelsScreen";
import { ResearchCenterScreen } from "@/features/research/ResearchCenterScreen";
import { ModuleNotConfigured } from "@/features/placeholder/ModuleNotConfigured";
import { SettingsScreen } from "@/features/settings/SettingsScreen";
import { ClientSearchScreen } from "@/features/client-search/ClientSearchScreen";
import { ClientListScreen } from "@/features/client-search/ClientListScreen";
import { ClientSearchesScreen } from "@/features/client-search/ClientSearchesScreen";

/** Modules that exist today. Everything else renders its honest "not configured" page. */
const SCREENS: Record<string, ComponentType> = {
  "client-search": ClientSearchScreen,
  "client-list": ClientListScreen,
  "client-searches": ClientSearchesScreen,
  overview: OverviewScreen,
  discovery: DiscoveryScreen,
  campaigns: CampaignsScreen,
  sources: SourcesScreen,
  businesses: BusinessesScreen,
  "lead-explorer": LeadExplorerScreen,
  "research-center": ResearchCenterScreen,
  "icp-builder": IcpBuilderScreen,
  "scoring-models": ScoringModelsScreen,
  "ai-models": AiModelsScreen,
  jobs: JobsScreen,
  datasets: DatasetsScreen,
  exports: ExportsScreen,
  system: SystemScreen,
  logs: LogsScreen,
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
