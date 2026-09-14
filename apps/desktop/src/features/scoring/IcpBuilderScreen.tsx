import { useCallback, useMemo, useState, type ReactNode } from "react";
import type {
  IcpDefinition,
  IcpProfileView,
  Offer,
  ScoringDefinition,
  ScoringSimulation,
} from "@lead/shared-types";
import { Archive, ArchiveRestore, Eye, Plus, Save, X } from "lucide-react";
import { toast } from "sonner";
import { TestDataBadge } from "@/components/data/cells";
import { PageShell } from "@/components/layout/page-chrome";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  diffDefinitions,
  fromAreas,
  fromTerms,
  shownVersion,
  toAreas,
  toTerms,
} from "@/features/scoring/definitions";
import {
  CHANNEL_KEYS,
  CHANNEL_TYPES,
  IMPORTANCES,
  IMPORTANCE_KEYS,
  LATINO_REQUIREMENTS,
  LATINO_REQUIREMENT_KEYS,
  MATURITIES,
  MATURITY_KEYS,
  PROBLEMS,
  PROBLEM_KEYS,
  codeLabel,
} from "@/features/scoring/labels";
import {
  ChangeList,
  Choices,
  Field,
  Heading,
  NumberInput,
  ProblemsList,
  SimulationView,
  VersionList,
} from "@/features/scoring/parts";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { useLoaded } from "@/lib/useLoaded";
import { cn } from "@/lib/utils";

interface Draft {
  profileId: string | null;
  projectId: string | null;
  name: string;
  description: string;
  definition: IcpDefinition;
}

type CriterionName = Exclude<
  keyof IcpDefinition,
  "schema_version" | "latino" | "exclusions" | "offers"
>;

const CRITERIA: readonly CriterionName[] = [
  "geography",
  "categories",
  "size",
  "freshness",
  "reputation",
  "digital_maturity",
  "problems",
  "channels",
  "buying_capacity",
];
const SELECT = "h-7 rounded-md border bg-transparent px-2 text-xs";

function draftOf(view: IcpProfileView, versionId?: string): Draft {
  const version = versionId
    ? view.versions.find((item) => item.id === versionId)
    : shownVersion(view.versions, view.profile.active_version_id);
  return {
    profileId: view.profile.id,
    projectId: view.profile.project_id,
    name: view.profile.name,
    description: view.profile.description ?? "",
    definition: version?.definition_json as unknown as IcpDefinition,
  };
}

function CriterionRow({
  name,
  definition,
  onChange,
}: {
  name: CriterionName;
  definition: IcpDefinition;
  onChange: (definition: IcpDefinition) => void;
}) {
  const t = useT();
  const criterion = definition[name];
  const update = (patch: object) =>
    onChange({ ...definition, [name]: { ...criterion, ...patch } } as IcpDefinition);
  let parameters: ReactNode = null;
  switch (name) {
    case "geography":
      parameters = (
        <div className="grid gap-1 sm:grid-cols-2">
          <Textarea
            aria-label={t("icp.include")}
            placeholder={`${t("icp.include")}: ${t("icp.areasPlaceholder")}`}
            defaultValue={fromAreas(definition.geography.include)}
            onBlur={(event) => update({ include: toAreas(event.target.value) })}
            className="min-h-12 text-xs"
          />
          <Textarea
            aria-label={t("icp.exclude")}
            placeholder={`${t("icp.exclude")}: ${t("icp.areasPlaceholder")}`}
            defaultValue={fromAreas(definition.geography.exclude)}
            onBlur={(event) => update({ exclude: toAreas(event.target.value) })}
            className="min-h-12 text-xs"
          />
        </div>
      );
      break;
    case "categories":
      parameters = (
        <div className="grid gap-1 sm:grid-cols-2">
          <Input
            aria-label={t("icp.terms")}
            placeholder={t("icp.terms")}
            defaultValue={fromTerms(definition.categories.terms)}
            onBlur={(event) => update({ terms: toTerms(event.target.value) })}
            className="h-7 text-xs"
          />
          <Input
            aria-label={t("icp.excludedTerms")}
            placeholder={t("icp.excludedTerms")}
            defaultValue={fromTerms(definition.categories.excluded_terms)}
            onBlur={(event) => update({ excluded_terms: toTerms(event.target.value) })}
            className="h-7 text-xs"
          />
        </div>
      );
      break;
    case "size":
      parameters = (
        <NumberInput
          label={t("icp.minLocations")}
          value={definition.size.min_locations}
          min={1}
          max={1000}
          onChange={(value) => update({ min_locations: value ?? 1 })}
        />
      );
      break;
    case "freshness":
      parameters = (
        <NumberInput
          label={t("icp.maxDays")}
          value={definition.freshness.max_days_since_seen}
          min={1}
          max={3650}
          onChange={(value) => update({ max_days_since_seen: value ?? 365 })}
        />
      );
      break;
    case "reputation":
      parameters = (
        <div className="space-y-1">
          <div className="flex flex-wrap gap-2">
            <NumberInput
              label={t("icp.minRating")}
              value={definition.reputation.min_rating}
              min={0}
              max={5}
              step={0.1}
              onChange={(value) => update({ min_rating: value })}
            />
            <NumberInput
              label={t("icp.minReviews")}
              value={definition.reputation.min_reviews}
              min={0}
              max={100000}
              onChange={(value) => update({ min_reviews: value })}
            />
          </div>
          <p className="text-[11px] text-muted-foreground">{t("icp.reputationNote")}</p>
        </div>
      );
      break;
    case "digital_maturity":
      parameters = (
        <Choices
          legend={t("icp.maturity")}
          options={MATURITIES}
          selected={definition.digital_maturity.wanted}
          label={(value) => t(MATURITY_KEYS[value])}
          onChange={(wanted) => update({ wanted })}
        />
      );
      break;
    case "problems":
      parameters = (
        <Choices
          legend={t("icp.problems")}
          options={PROBLEMS}
          selected={definition.problems.wanted}
          label={(value) => t(PROBLEM_KEYS[value])}
          onChange={(wanted) => update({ wanted })}
        />
      );
      break;
    case "channels":
      parameters = (
        <div className="space-y-1">
          <Choices
            legend={t("icp.channels")}
            options={CHANNEL_TYPES}
            selected={definition.channels.any_of}
            label={(value) => t(CHANNEL_KEYS[value])}
            onChange={(any_of) => update({ any_of })}
          />
          <label className="flex items-center gap-1 text-xs">
            <input
              type="checkbox"
              checked={definition.channels.require_decision_maker}
              onChange={(event) => update({ require_decision_maker: event.target.checked })}
            />
            {t("icp.requireDecisionMaker")}
          </label>
        </div>
      );
      break;
    case "buying_capacity":
      parameters = (
        <NumberInput
          label={t("icp.minProxies")}
          value={definition.buying_capacity.min_proxies}
          min={1}
          max={4}
          onChange={(value) => update({ min_proxies: value ?? 1 })}
        />
      );
      break;
  }
  const label = codeLabel(t, `criterion.${name}`);
  return (
    <tr className="border-t align-top">
      <td className="py-1.5 pr-2 font-medium">{label}</td>
      <td className="py-1.5 pr-2">
        <select
          aria-label={t("icp.importanceOf", { criterion: label })}
          value={criterion.importance}
          onChange={(event) => update({ importance: event.target.value })}
          className={SELECT}
        >
          {IMPORTANCES.map((value) => (
            <option key={value} value={value}>
              {t(IMPORTANCE_KEYS[value])}
            </option>
          ))}
        </select>
      </td>
      <td className="py-1.5 pr-2">
        <NumberInput
          label={t("icp.weightOf", { criterion: label })}
          value={criterion.weight}
          min={0}
          max={10}
          onChange={(value) => update({ weight: value ?? 0 })}
        />
      </td>
      <td className="py-1.5">{parameters}</td>
    </tr>
  );
}

function OffersEditor({
  offers,
  onChange,
}: {
  offers: Offer[];
  onChange: (offers: Offer[]) => void;
}) {
  const t = useT();
  const update = (index: number, patch: Partial<Offer>) =>
    onChange(offers.map((offer, i) => (i === index ? { ...offer, ...patch } : offer)));
  return (
    <div className="space-y-2">
      {offers.map((offer, index) => (
        <div key={index} className="space-y-1.5 rounded-md border p-2">
          <div className="flex flex-wrap gap-2">
            <Input
              aria-label={t("icp.offerKey")}
              placeholder={t("icp.offerKey")}
              value={offer.key}
              onChange={(event) => update(index, { key: event.target.value })}
              className="h-7 w-40 font-mono text-xs"
            />
            <Input
              aria-label={t("icp.offerName")}
              placeholder={t("icp.offerName")}
              value={offer.name}
              onChange={(event) => update(index, { name: event.target.value })}
              className="h-7 min-w-40 flex-1 text-xs"
            />
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              aria-label={t("icp.removeOffer")}
              onClick={() => onChange(offers.filter((_, i) => i !== index))}
            >
              <X aria-hidden="true" className="size-3.5" />
            </Button>
          </div>
          <Textarea
            aria-label={t("icp.offerDescription")}
            placeholder={t("icp.offerDescription")}
            value={offer.description}
            maxLength={600}
            onChange={(event) => update(index, { description: event.target.value })}
            className="min-h-10 text-xs"
          />
          <Choices
            legend={t("icp.solves")}
            options={PROBLEMS}
            selected={offer.solves}
            label={(value) => t(PROBLEM_KEYS[value])}
            onChange={(solves) => update(index, { solves })}
          />
          <div className="grid gap-1 sm:grid-cols-2">
            <Input
              aria-label={t("icp.capabilities")}
              placeholder={t("icp.capabilities")}
              defaultValue={fromTerms(offer.capabilities)}
              onBlur={(event) => update(index, { capabilities: toTerms(event.target.value) })}
              className="h-7 text-xs"
            />
            <Input
              aria-label={t("icp.notFor")}
              placeholder={t("icp.notFor")}
              defaultValue={fromTerms(offer.not_for_categories)}
              onBlur={(event) => update(index, { not_for_categories: toTerms(event.target.value) })}
              className="h-7 text-xs"
            />
          </div>
        </div>
      ))}
      <Button
        variant="outline"
        size="sm"
        className="h-7"
        onClick={() =>
          onChange([
            ...offers,
            {
              key: `offer_${offers.length + 1}`,
              name: "",
              description: "",
              solves: [],
              capabilities: [],
              not_for_categories: [],
            },
          ])
        }
      >
        <Plus aria-hidden="true" />
        {t("icp.addOffer")}
      </Button>
    </div>
  );
}

/**
 * ICP Builder (phase 11): who is worth researching, what the seller can deliver and how sure a match
 * must be. Every save is a new immutable version; activating, archiving and restoring need a reason;
 * a preview scores the drafts in memory over the project's businesses read now, or over the facts a
 * finished run stored, and writes nothing.
 */
export function IcpBuilderScreen() {
  const t = useT();
  const language = useLanguage();
  const fetchCatalog = useCallback(() => desktop.getScoringCatalog({ project_id: null }), []);
  const { data: catalog, error, reload } = useLoaded(fetchCatalog, "get_scoring_catalog");
  // finished runs whose stored facts a preview can score instead of reading the store now
  const fetchRuns = useCallback(
    () => desktop.listScoreRuns({ project_id: null, limit: 50, cursor: null }),
    [],
  );
  const { data: runs } = useLoaded(fetchRuns, "list_score_runs");
  const [factsFrom, setFactsFrom] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [revision, setRevision] = useState(0);
  const [reason, setReason] = useState("");
  const [preview, setPreview] = useState<ScoringSimulation | null>(null);
  const [compared, setCompared] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const view = catalog?.icp_profiles.find((item) => item.profile.id === draft?.profileId) ?? null;
  const scoring = useMemo(() => {
    const model = catalog?.models[0];
    const version = model && shownVersion(model.versions, model.model.active_version_id);
    return (
      (version?.definition_json as unknown as ScoringDefinition | undefined) ??
      catalog?.scoring_template ??
      null
    );
  }, [catalog]);

  const open = (next: Draft) => {
    setDraft(next);
    setRevision((value) => value + 1);
    setPreview(null);
    setCompared([]);
    setFactsFrom("");
  };
  const edit = (definition: IcpDefinition) => draft && setDraft({ ...draft, definition });

  const act = async (failure: string, work: () => Promise<void>) => {
    setBusy(true);
    try {
      await work();
    } catch (cause) {
      toast.error(failure, { description: toIpcError(cause, "scoring").message });
    } finally {
      setBusy(false);
    }
  };

  const save = (asNew: boolean) =>
    draft &&
    act(t("icp.saveFailed"), async () => {
      const saved = await desktop.saveIcpProfile({
        profile_id: asNew ? null : draft.profileId,
        project_id: draft.projectId,
        name: draft.name.trim(),
        description: draft.description.trim() || null,
        definition: draft.definition,
      });
      await reload();
      open(draftOf(saved, saved.versions[0]?.id));
      toast.success(t("icp.saved", { version: saved.versions[0]?.version ?? 1 }));
    });

  const change = (action: "activate" | "archive" | "restore", versionId: string | null) =>
    view &&
    act(t("icp.changeFailed"), async () => {
      await desktop.changeScoring({
        target: "icp_profile",
        id: view.profile.id,
        action,
        version_id: versionId,
        reason: reason.trim(),
      });
      setReason("");
      await reload();
      toast.success(t("icp.changed"));
    });

  const simulate = () =>
    draft &&
    scoring &&
    act(t("icp.previewFailed"), async () => {
      if (!draft.projectId) {
        toast.error(t("icp.previewNeedsProject"));
        return;
      }
      setPreview(
        await desktop.simulateScoring({
          project_id: draft.projectId,
          facts_from_run_id: factsFrom || null,
          icp: draft.definition,
          scoring,
          top: 10,
        }),
      );
    });

  if (error) {
    return (
      <StateBlock
        variant={error.code === "desktop_unavailable" ? "offline" : "error"}
        title={t("icp.errorTitle")}
        body={error.message}
        action={
          <Button variant="outline" size="sm" onClick={() => void reload()}>
            {t("common.retry")}
          </Button>
        }
      />
    );
  }
  if (!catalog) return <StateBlock variant="loading" title={t("state.loading")} />;

  const comparedVersions = view?.versions.filter((version) => compared.includes(version.id)) ?? [];
  const hasReason = reason.trim().length > 0;
  const stored = (runs?.items ?? []).filter(
    (run) => run.finished_at !== null && run.project_id === draft?.projectId,
  );

  return (
    <PageShell>
      <div className="flex min-h-0 flex-1">
        <aside
          aria-label={t("icp.profiles")}
          className="w-64 shrink-0 space-y-2 overflow-auto border-r p-2"
        >
          <Button
            size="sm"
            className="h-7 w-full"
            onClick={() =>
              open({
                profileId: null,
                projectId: null,
                name: "",
                description: "",
                definition: catalog.icp_template,
              })
            }
          >
            <Plus aria-hidden="true" />
            {t("icp.new")}
          </Button>
          {catalog.icp_profiles.length === 0 && (
            <p className="text-[11px] text-muted-foreground">{t("icp.none")}</p>
          )}
          <ul className="space-y-1">
            {catalog.icp_profiles.map((item) => (
              <li key={item.profile.id}>
                <button
                  type="button"
                  onClick={() => open(draftOf(item))}
                  className={cn(
                    "w-full rounded-md border px-2 py-1 text-left text-xs",
                    draft?.profileId === item.profile.id && "border-primary/60",
                  )}
                >
                  <span className="flex items-center gap-1 font-medium">
                    <span className="min-w-0 break-words">{item.profile.name}</span>
                    {item.is_synthetic && <TestDataBadge />}
                  </span>
                  <span className="block text-[11px] text-muted-foreground">
                    {t("icp.versionsLine", {
                      n: item.versions.length,
                      active:
                        item.versions.find((v) => v.id === item.profile.active_version_id)
                          ?.version ?? "—",
                    })}
                    {item.profile.archived_at ? ` · ${t("scoring.archived")}` : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </aside>

        <main className="min-w-0 flex-1 overflow-auto p-4">
          {draft === null ? (
            <StateBlock variant="empty" title={t("icp.chooseTitle")} body={t("icp.chooseBody")} />
          ) : (
            <div key={revision} className="mx-auto max-w-4xl space-y-4 text-xs">
              <div className="grid gap-2 sm:grid-cols-2">
                <Field label={t("scoring.name")}>
                  <Input
                    value={draft.name}
                    maxLength={300}
                    onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                    className="h-7 text-xs"
                  />
                </Field>
                <Field label={t("scoring.description")}>
                  <Input
                    value={draft.description}
                    maxLength={2000}
                    onChange={(event) => setDraft({ ...draft, description: event.target.value })}
                    className="h-7 text-xs"
                  />
                </Field>
              </div>

              <section className="space-y-2">
                <Heading>{t("icp.criteria")}</Heading>
                <p className="text-[11px] text-muted-foreground">{t("icp.criteriaHint")}</p>
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="text-left text-[11px] text-muted-foreground">
                        <th className="font-normal">{t("icp.criterion")}</th>
                        <th className="font-normal">{t("icp.importance")}</th>
                        <th className="font-normal">{t("scoring.weight")}</th>
                        <th className="font-normal">{t("icp.parameters")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {CRITERIA.map((name) => (
                        <CriterionRow
                          key={name}
                          name={name}
                          definition={draft.definition}
                          onChange={edit}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              <section className="space-y-1">
                <Heading>{t("icp.latino")}</Heading>
                <select
                  aria-label={t("icp.latino")}
                  value={draft.definition.latino}
                  onChange={(event) =>
                    edit({
                      ...draft.definition,
                      latino: event.target.value as IcpDefinition["latino"],
                    })
                  }
                  className={SELECT}
                >
                  {LATINO_REQUIREMENTS.map((value) => (
                    <option key={value} value={value}>
                      {t(LATINO_REQUIREMENT_KEYS[value])}
                    </option>
                  ))}
                </select>
                <p className="text-[11px] text-muted-foreground">{t("icp.latinoHint")}</p>
              </section>

              <section className="space-y-1">
                <Heading>{t("icp.exclusions")}</Heading>
                <div className="grid gap-1 sm:grid-cols-2">
                  {(
                    [
                      ["names", "icp.excludedNames"],
                      ["domains", "icp.excludedDomains"],
                      ["categories", "icp.excludedCategories"],
                      ["regions", "icp.excludedRegions"],
                      ["postal_codes", "icp.excludedPostalCodes"],
                    ] as const
                  ).map(([key, label]) => (
                    <Input
                      key={key}
                      aria-label={t(label)}
                      placeholder={t(label)}
                      defaultValue={fromTerms(draft.definition.exclusions[key])}
                      onBlur={(event) =>
                        edit({
                          ...draft.definition,
                          exclusions: {
                            ...draft.definition.exclusions,
                            [key]: toTerms(event.target.value).map((term) =>
                              key === "regions" ? term.toUpperCase() : term,
                            ),
                          },
                        })
                      }
                      className="h-7 text-xs"
                    />
                  ))}
                </div>
              </section>

              <section className="space-y-1">
                <Heading>{t("icp.offers")}</Heading>
                <OffersEditor
                  offers={draft.definition.offers}
                  onChange={(offers) => edit({ ...draft.definition, offers })}
                />
              </section>

              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  className="h-7"
                  disabled={busy || !draft.profileId || !draft.name.trim()}
                  onClick={() => void save(false)}
                >
                  <Save aria-hidden="true" />
                  {t("scoring.saveVersion")}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7"
                  disabled={busy || !draft.name.trim()}
                  onClick={() => void save(true)}
                >
                  <Plus aria-hidden="true" />
                  {t("scoring.saveAsNew")}
                </Button>
                <select
                  aria-label={t("scoring.factsFrom")}
                  value={factsFrom}
                  onChange={(event) => setFactsFrom(event.target.value)}
                  className={SELECT}
                >
                  <option value="">{t("scoring.factsNow")}</option>
                  {stored.map((run) => (
                    <option key={run.score_run_id} value={run.score_run_id}>
                      {t("scoring.factsOf", {
                        run: `${run.icp_profile_name} v${run.icp_profile_version} · ${run.scoring_model_name} v${run.scoring_model_version} · ${formatDateTime(run.finished_at ?? run.requested_at, language)}`,
                      })}
                    </option>
                  ))}
                </select>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7"
                  disabled={busy || !scoring}
                  onClick={() => void simulate()}
                >
                  <Eye aria-hidden="true" />
                  {t("scoring.preview")}
                </Button>
              </div>

              {view && view.problems.length > 0 && (
                <section className="space-y-1">
                  <Heading>{t("icp.problemsTitle")}</Heading>
                  <ProblemsList problems={view.problems} />
                </section>
              )}
              {preview && <SimulationView simulation={preview} />}

              {view && (
                <section className="space-y-2">
                  <Heading>{t("scoring.versions")}</Heading>
                  {view.profile.archived_at && (
                    <InlineNotice variant="partial">{t("scoring.archivedNotice")}</InlineNotice>
                  )}
                  <Field label={t("scoring.changeReason")}>
                    <Textarea
                      value={reason}
                      maxLength={2000}
                      onChange={(event) => setReason(event.target.value)}
                      className="min-h-10 text-xs"
                    />
                  </Field>
                  <VersionList
                    versions={view.versions}
                    activeId={view.profile.active_version_id}
                    compared={compared}
                    canActivate={hasReason && !busy && !view.profile.archived_at}
                    onLoad={(id) => open(draftOf(view, id))}
                    onActivate={(id) => void change("activate", id)}
                    onCompare={(id) =>
                      setCompared((current) =>
                        current.includes(id)
                          ? current.filter((value) => value !== id)
                          : [...current, id].slice(-2),
                      )
                    }
                  />
                  <p className="text-[11px] text-muted-foreground">{t("scoring.compareHint")}</p>
                  {comparedVersions.length === 2 && comparedVersions[0] && comparedVersions[1] && (
                    <ChangeList
                      changes={diffDefinitions(
                        comparedVersions[1].definition_json,
                        comparedVersions[0].definition_json,
                      )}
                    />
                  )}
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7"
                    disabled={busy || !hasReason}
                    onClick={() =>
                      void change(view.profile.archived_at ? "restore" : "archive", null)
                    }
                  >
                    {view.profile.archived_at ? (
                      <ArchiveRestore aria-hidden="true" />
                    ) : (
                      <Archive aria-hidden="true" />
                    )}
                    {t(view.profile.archived_at ? "scoring.restore" : "scoring.archive")}
                  </Button>
                </section>
              )}
            </div>
          )}
        </main>
      </div>
    </PageShell>
  );
}
