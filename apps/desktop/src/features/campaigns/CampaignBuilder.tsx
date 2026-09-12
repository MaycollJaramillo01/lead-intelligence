import { useEffect, useState, type ReactNode } from "react";
import type {
  CampaignListItem,
  CriteriaCheck,
  CriteriaProblem,
  DiscoveryCriteria,
  GeoScope,
  SourceInfo,
} from "@lead/shared-types";
import { CircleAlert, Plus, TriangleAlert, X } from "lucide-react";
import { toast } from "sonner";
import { Section } from "@/components/layout/page-chrome";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { CampaignDraft } from "@/features/campaigns/draft";
import {
  fromCommas,
  fromLines,
  selectionFor,
  toLines,
  withSource,
  withSourceConfig,
} from "@/features/discovery/criteria";
import { useT, type Translator } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { cn } from "@/lib/utils";

const SEEDS_KEY = "manual.seeds";
const VALIDATE_DEBOUNCE_MS = 400;

/** Label wrapping its control: the association holds without hand-managed ids. */
function Field({ label, help, children }: { label: string; help?: string; children: ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-xs font-medium">{label}</span>
      {children}
      {help && <span className="block text-[11px] text-muted-foreground">{help}</span>}
    </label>
  );
}

function emptyScope(): GeoScope {
  return {
    country_code: "US",
    region: null,
    metro: null,
    locality: null,
    postal_code: null,
    radius_km: null,
  };
}

function blank(value: string): string | null {
  return value.trim() === "" ? null : value.trim();
}

function GeographyEditor({
  scopes,
  onChange,
}: {
  scopes: GeoScope[];
  onChange: (scopes: GeoScope[]) => void;
}) {
  const t = useT();
  const update = (index: number, patch: Partial<GeoScope>) =>
    onChange(scopes.map((scope, i) => (i === index ? { ...scope, ...patch } : scope)));

  return (
    <div className="space-y-2">
      {scopes.length === 0 && (
        <p className="text-[11px] text-muted-foreground">{t("builder.noGeography")}</p>
      )}
      {scopes.map((scope, index) => (
        <div key={index} className="flex flex-wrap items-center gap-2">
          <Input
            value={scope.country_code}
            onChange={(event) => update(index, { country_code: event.target.value.toUpperCase() })}
            aria-label={t("builder.country")}
            placeholder={t("builder.country")}
            maxLength={2}
            className="h-7 w-16 text-xs"
          />
          <Input
            value={scope.region ?? ""}
            onChange={(event) => update(index, { region: blank(event.target.value.toUpperCase()) })}
            aria-label={t("builder.region")}
            placeholder={t("builder.region")}
            maxLength={2}
            className="h-7 w-16 text-xs"
          />
          <Input
            value={scope.locality ?? ""}
            onChange={(event) => update(index, { locality: blank(event.target.value) })}
            aria-label={t("builder.locality")}
            placeholder={t("builder.locality")}
            className="h-7 w-40 text-xs"
          />
          <Input
            value={scope.postal_code ?? ""}
            onChange={(event) => update(index, { postal_code: blank(event.target.value) })}
            aria-label={t("builder.postalCode")}
            placeholder={t("builder.postalCode")}
            className="h-7 w-24 text-xs"
          />
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            aria-label={t("builder.removePlace")}
            onClick={() => onChange(scopes.filter((_, i) => i !== index))}
          >
            <X aria-hidden="true" className="size-3.5" />
          </Button>
        </div>
      ))}
      <Button
        variant="outline"
        size="sm"
        className="h-7"
        onClick={() => onChange([...scopes, emptyScope()])}
      >
        <Plus aria-hidden="true" />
        {t("builder.addPlace")}
      </Button>
    </div>
  );
}

function SourcePicker({
  criteria,
  sources,
  onChange,
}: {
  criteria: DiscoveryCriteria;
  sources: SourceInfo[];
  onChange: (criteria: DiscoveryCriteria) => void;
}) {
  const t = useT();
  return (
    <ul className="space-y-2">
      {sources.map((source) => {
        const selection = selectionFor(criteria, source.metadata.key);
        const blocked = source.health === "disabled";
        return (
          <li key={source.metadata.key} className="rounded-md border p-2">
            <div className="flex items-start gap-2">
              <Checkbox
                id={`source-${source.metadata.key}`}
                checked={selection !== undefined}
                disabled={blocked}
                onCheckedChange={(checked) =>
                  onChange(withSource(criteria, source, checked === true))
                }
              />
              <div className="min-w-0 flex-1">
                <Label htmlFor={`source-${source.metadata.key}`} className="text-xs">
                  {source.metadata.name}
                </Label>
                <p className="text-[11px] text-muted-foreground">
                  {t("sources.perMinute", { n: source.requests_per_minute })} ·{" "}
                  {t(`sourceKind.${source.metadata.kind}`)}
                </p>
                {blocked && (
                  <p className="text-[11px] text-warn">
                    {source.health_detail ?? t("sources.health.disabled")}
                  </p>
                )}
              </div>
              {selection && (
                <Input
                  type="number"
                  min={1}
                  value={selection.max_records ?? ""}
                  onChange={(event) =>
                    onChange({
                      ...criteria,
                      sources: criteria.sources.map((item) =>
                        item.connector_key === source.metadata.key
                          ? {
                              ...item,
                              max_records: event.target.value ? Number(event.target.value) : null,
                            }
                          : item,
                      ),
                    })
                  }
                  aria-label={t("builder.maxRecords")}
                  placeholder={t("builder.maxRecords")}
                  className="h-7 w-28 text-xs"
                />
              )}
            </div>
            {selection && source.metadata.key === SEEDS_KEY && (
              <Textarea
                value={String(selection.config_json.seeds ?? "")}
                onChange={(event) =>
                  onChange(withSourceConfig(criteria, SEEDS_KEY, { seeds: event.target.value }))
                }
                aria-label={t("builder.seeds")}
                placeholder={t("builder.seedsPlaceholder")}
                rows={3}
                className="mt-2 text-xs"
              />
            )}
          </li>
        );
      })}
    </ul>
  );
}

function ProblemList({ problems }: { problems: CriteriaProblem[] }) {
  const t = useT();
  if (problems.length === 0) {
    return <p className="text-xs text-ok">{t("builder.noProblems")}</p>;
  }
  return (
    <ul className="space-y-1">
      {problems.map((problem, index) => {
        const isError = problem.severity === "error";
        const Icon = isError ? CircleAlert : TriangleAlert;
        return (
          <li key={index} className="flex items-start gap-1.5 text-xs">
            <Icon
              aria-hidden="true"
              className={cn("mt-0.5 size-3.5 shrink-0", isError ? "text-danger" : "text-warn")}
            />
            <span>
              <span className="font-mono text-[10px] text-muted-foreground">{problem.path}</span>{" "}
              {problem.message}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function EstimateList({ check, t }: { check: CriteriaCheck; t: Translator }) {
  if (check.estimates.length === 0) return null;
  return (
    <div className="space-y-1">
      <p className="text-[10px] tracking-wider text-muted-foreground uppercase">
        {t("builder.estimateTitle")}
      </p>
      <ul className="space-y-1">
        {check.estimates.map((estimate) => (
          <li key={estimate.connector_key} className="text-xs">
            <span className="font-medium">{estimate.connector_name}</span>{" "}
            <span className="font-mono">
              {estimate.value === null ? "—" : t("builder.estimateValue", { n: estimate.value })}
            </span>
            <p className="text-[11px] text-muted-foreground">{estimate.basis}</p>
            {estimate.unsupported_fields.length > 0 && (
              <p className="text-[11px] text-warn">
                {t("builder.ignores", { fields: estimate.unsupported_fields.join(", ") })}
              </p>
            )}
          </li>
        ))}
      </ul>
      <p className="text-[11px] text-muted-foreground">{t("builder.estimateCaveat")}</p>
    </div>
  );
}

export interface CampaignBuilderProps {
  draft: CampaignDraft;
  sources: SourceInfo[];
  onSaved: (campaign: CampaignListItem) => void;
  onCancel: () => void;
}

/** Builds one campaign: what to look for, where, from which sources and within what limits. */
export function CampaignBuilder({ draft, sources, onSaved, onCancel }: CampaignBuilderProps) {
  const t = useT();
  const [name, setName] = useState(draft.name);
  const [objective, setObjective] = useState(draft.objective);
  const [criteria, setCriteria] = useState<DiscoveryCriteria>(draft.criteria);
  const [check, setCheck] = useState<CriteriaCheck | null>(null);
  const [busy, setBusy] = useState(false);

  // the criteria are checked as they are edited, debounced so typing does not flood the worker
  useEffect(() => {
    const timer = setTimeout(() => {
      void desktop
        .validateCriteria({ project_id: null, criteria, with_estimates: false })
        .then(setCheck, () => setCheck(null));
    }, VALIDATE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [criteria]);

  const estimate = () => {
    setBusy(true);
    desktop
      .validateCriteria({ project_id: null, criteria, with_estimates: true })
      .then(setCheck, (cause: unknown) => {
        toast.error(t("builder.validateFailed"), {
          description: toIpcError(cause, "validate_criteria").message,
        });
      })
      .finally(() => setBusy(false));
  };

  const problems = check?.problems ?? [];
  const blocked = problems.some((problem) => problem.severity === "error");

  const save = async () => {
    setBusy(true);
    try {
      const saved = await desktop.saveCampaign({
        campaign_id: draft.campaignId,
        project_id: draft.projectId,
        name: name.trim(),
        objective: objective.trim() || null,
        criteria,
      });
      toast.success(t("builder.saved"));
      onSaved(saved.campaign);
    } catch (cause) {
      toast.error(t("builder.saveFailed"), {
        description: toIpcError(cause, "save_campaign").message,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-[var(--section-gap)]">
      <Section
        title={draft.campaignId === null ? t("builder.newTitle") : t("builder.editTitle")}
        description={t("builder.description")}
        actions={
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" className="h-7" onClick={onCancel}>
              {t("common.cancel")}
            </Button>
            <Button
              size="sm"
              className="h-7"
              disabled={busy || name.trim() === ""}
              onClick={() => void save()}
            >
              {t("common.save")}
            </Button>
          </div>
        }
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t("builder.name")}>
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={300}
              className="h-8 text-xs"
            />
          </Field>
          <Field label={t("builder.objective")}>
            <Input
              value={objective}
              onChange={(event) => setObjective(event.target.value)}
              className="h-8 text-xs"
            />
          </Field>
        </div>
      </Section>

      <Section title={t("builder.whereTitle")} description={t("builder.whereHelp")}>
        <GeographyEditor
          scopes={criteria.geography}
          onChange={(geography) => setCriteria({ ...criteria, geography })}
        />
      </Section>

      <Section title={t("builder.whatTitle")} description={t("builder.whatHelp")}>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t("builder.verticals")} help={t("builder.verticalsHelp")}>
            <Input
              value={criteria.verticals.join(", ")}
              onChange={(event) =>
                setCriteria({ ...criteria, verticals: fromCommas(event.target.value) })
              }
              className="h-8 text-xs"
              placeholder="23, 722"
            />
          </Field>
          <Field label={t("builder.categories")}>
            <Input
              value={criteria.categories.join(", ")}
              onChange={(event) =>
                setCriteria({ ...criteria, categories: fromCommas(event.target.value) })
              }
              className="h-8 text-xs"
            />
          </Field>
          <Field label={t("builder.services")}>
            <Input
              value={criteria.services.join(", ")}
              onChange={(event) =>
                setCriteria({ ...criteria, services: fromCommas(event.target.value) })
              }
              className="h-8 text-xs"
            />
          </Field>
          <Field label={t("builder.keywords")}>
            <Input
              value={criteria.keywords.join(", ")}
              onChange={(event) =>
                setCriteria({ ...criteria, keywords: fromCommas(event.target.value) })
              }
              className="h-8 text-xs"
            />
          </Field>
          <Field label={t("builder.languages")} help={t("builder.languagesHelp")}>
            <Input
              value={criteria.market_languages.join(", ")}
              onChange={(event) =>
                setCriteria({ ...criteria, market_languages: fromCommas(event.target.value) })
              }
              className="h-8 text-xs"
              placeholder="es"
            />
          </Field>
          <Field label={t("builder.freshness")} help={t("builder.freshnessHelp")}>
            <Input
              type="date"
              value={criteria.freshness_since ?? ""}
              onChange={(event) =>
                setCriteria({ ...criteria, freshness_since: blank(event.target.value) })
              }
              className="h-8 text-xs"
            />
          </Field>
        </div>
        <div className="mt-3 flex items-center gap-2">
          <Checkbox
            id="strategy-narrow"
            checked={criteria.strategy === "narrow"}
            onCheckedChange={(checked) =>
              setCriteria({ ...criteria, strategy: checked === true ? "narrow" : "broad" })
            }
          />
          <Label htmlFor="strategy-narrow" className="text-xs">
            {t("builder.narrow")}
          </Label>
        </div>
      </Section>

      <Section title={t("builder.excludeTitle")} description={t("builder.excludeHelp")}>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t("builder.excludeNames")}>
            <Textarea
              value={toLines(criteria.exclusions.names)}
              onChange={(event) =>
                setCriteria({
                  ...criteria,
                  exclusions: { ...criteria.exclusions, names: fromLines(event.target.value) },
                })
              }
              rows={3}
              className="text-xs"
            />
          </Field>
          <Field label={t("builder.excludeDomains")}>
            <Textarea
              value={toLines(criteria.exclusions.domains)}
              onChange={(event) =>
                setCriteria({
                  ...criteria,
                  exclusions: { ...criteria.exclusions, domains: fromLines(event.target.value) },
                })
              }
              rows={3}
              className="text-xs"
            />
          </Field>
        </div>
      </Section>

      <Section title={t("builder.sourcesTitle")} description={t("builder.sourcesHelp")}>
        <SourcePicker criteria={criteria} sources={sources} onChange={setCriteria} />
      </Section>

      <Section title={t("builder.limitsTitle")} description={t("builder.limitsHelp")}>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label={t("builder.maxCandidates")}>
            <Input
              type="number"
              min={1}
              value={criteria.limits.max_candidates}
              onChange={(event) =>
                setCriteria({
                  ...criteria,
                  limits: { ...criteria.limits, max_candidates: Number(event.target.value) || 1 },
                })
              }
              className="h-8 text-xs"
            />
          </Field>
          <Field label={t("builder.maxRequests")}>
            <Input
              type="number"
              min={1}
              value={criteria.limits.max_requests}
              onChange={(event) =>
                setCriteria({
                  ...criteria,
                  limits: { ...criteria.limits, max_requests: Number(event.target.value) || 1 },
                })
              }
              className="h-8 text-xs"
            />
          </Field>
          <Field label={t("builder.maxPages")}>
            <Input
              type="number"
              min={1}
              value={criteria.limits.max_pages_per_source}
              onChange={(event) =>
                setCriteria({
                  ...criteria,
                  limits: {
                    ...criteria.limits,
                    max_pages_per_source: Number(event.target.value) || 1,
                  },
                })
              }
              className="h-8 text-xs"
            />
          </Field>
        </div>
      </Section>

      <Section
        title={t("builder.checkTitle")}
        description={t("builder.checkHelp")}
        actions={
          <Button variant="outline" size="sm" className="h-7" disabled={busy} onClick={estimate}>
            {t("builder.estimate")}
          </Button>
        }
      >
        <div className="space-y-3">
          <ProblemList problems={problems} />
          {check && <EstimateList check={check} t={t} />}
          {blocked && <p className="text-xs text-danger">{t("builder.blocked")}</p>}
        </div>
      </Section>
    </div>
  );
}
