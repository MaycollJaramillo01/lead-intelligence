import { useCallback, useState } from "react";
import type { CampaignListItem, SourceInfo } from "@lead/shared-types";
import { Copy, Pencil, Plus } from "lucide-react";
import { PageScroll, PageShell, Section } from "@/components/layout/page-chrome";
import { StateBlock } from "@/components/states/StateBlock";
import { TestDataBadge } from "@/components/data/cells";
import { Button } from "@/components/ui/button";
import { CampaignBuilder } from "@/features/campaigns/CampaignBuilder";
import { copyOf, draftFrom, newDraft, type CampaignDraft } from "@/features/campaigns/draft";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop } from "@/lib/ipc";
import { useLoaded } from "@/lib/useLoaded";

const PAGE_SIZE = 100;

export function CampaignsScreen() {
  const t = useT();
  const language = useLanguage();
  const [draft, setDraft] = useState<CampaignDraft | null>(null);
  const fetchAll = useCallback(
    async () =>
      Promise.all([
        desktop.listCampaigns({ project_id: null, limit: PAGE_SIZE, cursor: null }),
        desktop.listSources({ project_id: null }),
      ]),
    [],
  );
  const { data, error, reload } = useLoaded(fetchAll, "list_campaigns");
  const campaigns: CampaignListItem[] | null = data ? data[0].items : null;
  const sources: SourceInfo[] = data ? data[1].sources : [];

  if (draft !== null) {
    return (
      <PageShell>
        <PageScroll>
          <CampaignBuilder
            draft={draft}
            sources={sources}
            onCancel={() => setDraft(null)}
            onSaved={() => {
              setDraft(null);
              void reload();
            }}
          />
        </PageScroll>
      </PageShell>
    );
  }

  return (
    <PageShell>
      <PageScroll>
        <Section
          title={t("nav.campaigns")}
          description={t("desc.campaigns")}
          actions={
            <Button
              size="sm"
              className="h-7"
              disabled={sources.length === 0}
              onClick={() => setDraft(newDraft())}
            >
              <Plus aria-hidden="true" />
              {t("campaigns.new")}
            </Button>
          }
        >
          {error ? (
            <StateBlock
              variant={error.code === "desktop_unavailable" ? "offline" : "error"}
              title={t("campaigns.errorTitle")}
              body={error.message}
              action={
                <Button variant="outline" size="sm" onClick={() => void reload()}>
                  {t("common.retry")}
                </Button>
              }
            />
          ) : campaigns === null ? (
            <StateBlock variant="loading" title={t("common.loading")} />
          ) : campaigns.length === 0 ? (
            <StateBlock
              variant="empty"
              title={t("campaigns.emptyTitle")}
              body={t("campaigns.emptyBody")}
            />
          ) : (
            <ul className="space-y-2">
              {campaigns.map((campaign) => (
                <li
                  key={campaign.id}
                  className="flex flex-wrap items-start justify-between gap-3 rounded-md border p-3"
                >
                  <div className="min-w-0">
                    <h3 className="flex items-center gap-1.5 truncate text-sm font-medium">
                      {campaign.name}
                      {campaign.is_synthetic && <TestDataBadge />}
                    </h3>
                    {campaign.objective && (
                      <p className="truncate text-xs text-muted-foreground">{campaign.objective}</p>
                    )}
                    <dl className="mt-1 flex flex-wrap gap-x-4 text-[11px] text-muted-foreground">
                      <div className="flex gap-1">
                        <dt>{t("campaigns.sources")}</dt>
                        <dd className="font-mono text-foreground">
                          {campaign.criteria.sources.length}
                        </dd>
                      </div>
                      <div className="flex gap-1">
                        <dt>{t("campaigns.runs")}</dt>
                        <dd className="font-mono text-foreground">{campaign.run_count}</dd>
                      </div>
                      <div className="flex gap-1">
                        <dt>{t("campaigns.candidates")}</dt>
                        <dd className="font-mono text-foreground">{campaign.candidate_count}</dd>
                      </div>
                      {campaign.last_run_at && (
                        <div className="flex gap-1">
                          <dt>{t("campaigns.lastRun")}</dt>
                          <dd className="text-foreground">
                            {formatDateTime(campaign.last_run_at, language)}
                            {campaign.last_run_status &&
                              ` · ${t(`jobStatus.${campaign.last_run_status}`)}`}
                          </dd>
                        </div>
                      )}
                    </dl>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7"
                      onClick={() => setDraft(draftFrom(campaign))}
                    >
                      <Pencil aria-hidden="true" />
                      {t("common.edit")}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7"
                      onClick={() => setDraft(copyOf(campaign, t("campaigns.copySuffix")))}
                    >
                      <Copy aria-hidden="true" />
                      {t("campaigns.duplicate")}
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </PageScroll>
    </PageShell>
  );
}
