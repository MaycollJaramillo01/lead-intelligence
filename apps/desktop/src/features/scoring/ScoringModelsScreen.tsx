import { useCallback, useEffect, useState } from "react";
import type {
  RunControlAction,
  ScoreComparison,
  ScoreRunItem,
  ScoringCatalog,
  ScoringDefinition,
  ScoringModelView,
} from "@lead/shared-types";
import {
  Archive,
  ArchiveRestore,
  Ban,
  CirclePause,
  CirclePlay,
  Play,
  Plus,
  Save,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { JobProgress, type JobState } from "@/components/domain/JobProgress";
import { PageShell } from "@/components/layout/page-chrome";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  diffDefinitions,
  formulaText,
  fromTerms,
  shownVersion,
  toTerms,
} from "@/features/scoring/definitions";
import {
  COMPONENT_KEYS,
  STAGE_KEYS,
  TIER_KEYS,
  codeLabel,
  percent,
  scoreText,
} from "@/features/scoring/labels";
import {
  ChangeList,
  Distribution,
  Field,
  Heading,
  NumberInput,
  ProblemsList,
  TierLine,
  VersionList,
} from "@/features/scoring/parts";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { useLoaded } from "@/lib/useLoaded";
import { cn } from "@/lib/utils";

const POLL_MS = 1000;
const SELECT = "h-7 rounded-md border bg-transparent px-2 text-xs";
const CONTROLS: Record<RunControlAction, { icon: typeof CirclePause; states: JobState[] }> = {
  pause: { icon: CirclePause, states: ["running"] },
  resume: { icon: CirclePlay, states: ["paused"] },
  cancel: { icon: Ban, states: ["queued", "running", "paused"] },
};

interface ModelDraft {
  modelId: string | null;
  name: string;
  description: string;
  definition: ScoringDefinition;
}

function modelDraft(view: ScoringModelView, versionId?: string): ModelDraft {
  const version = versionId
    ? view.versions.find((item) => item.id === versionId)
    : shownVersion(view.versions, view.model.active_version_id);
  return {
    modelId: view.model.id,
    name: view.model.name,
    description: view.model.description ?? "",
    definition: version?.definition_json as unknown as ScoringDefinition,
  };
}

async function attempt(failure: string, work: () => Promise<void>, busy: (on: boolean) => void) {
  busy(true);
  try {
    await work();
  } catch (cause) {
    toast.error(failure, { description: toIpcError(cause, "scoring").message });
  } finally {
    busy(false);
  }
}

function ModelsTab({
  catalog,
  onChanged,
}: {
  catalog: ScoringCatalog;
  onChanged: () => Promise<void>;
}) {
  const t = useT();
  const [draft, setDraft] = useState<ModelDraft | null>(() =>
    catalog.models[0] ? modelDraft(catalog.models[0]) : null,
  );
  const [revision, setRevision] = useState(0);
  const [reason, setReason] = useState("");
  const [compared, setCompared] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const view = catalog.models.find((item) => item.model.id === draft?.modelId) ?? null;

  if (draft === null) return <StateBlock variant="empty" title={t("scoring.noModels")} />;

  const definition = draft.definition;
  const hasReason = reason.trim().length > 0;
  const open = (next: ModelDraft) => {
    setDraft(next);
    setRevision((value) => value + 1);
    setCompared([]);
  };
  const define = (patch: Partial<ScoringDefinition>) =>
    setDraft({ ...draft, definition: { ...definition, ...patch } });
  const save = (asNew: boolean) =>
    attempt(
      t("scoring.modelSaveFailed"),
      async () => {
        const saved = await desktop.saveScoringModel({
          model_id: asNew ? null : draft.modelId,
          name: draft.name.trim(),
          description: draft.description.trim() || null,
          definition,
        });
        await onChanged();
        open(modelDraft(saved, saved.versions[0]?.id));
        toast.success(t("scoring.modelSaved", { version: saved.versions[0]?.version ?? 1 }));
      },
      setBusy,
    );
  const change = (action: "activate" | "archive" | "restore", versionId: string | null) =>
    view &&
    attempt(
      t("scoring.modelChangeFailed"),
      async () => {
        await desktop.changeScoring({
          target: "scoring_model",
          id: view.model.id,
          action,
          version_id: versionId,
          reason: reason.trim(),
        });
        setReason("");
        await onChanged();
        toast.success(t("scoring.modelChanged"));
      },
      setBusy,
    );
  const comparedVersions = view?.versions.filter((version) => compared.includes(version.id)) ?? [];

  return (
    <div className="grid gap-4 md:grid-cols-[14rem_minmax(0,1fr)]">
      <aside aria-label={t("scoring.models")} className="space-y-1">
        {catalog.models.map((item) => (
          <button
            key={item.model.id}
            type="button"
            onClick={() => open(modelDraft(item))}
            className={cn(
              "w-full rounded-md border px-2 py-1 text-left text-xs",
              draft.modelId === item.model.id && "border-primary/60",
            )}
          >
            <span className="block font-medium break-words">{item.model.name}</span>
            <span className="block font-mono text-[10px] text-muted-foreground">
              {item.model.key}
            </span>
            <span className="block text-[11px] text-muted-foreground">
              {t("icp.versionsLine", {
                n: item.versions.length,
                active:
                  item.versions.find((v) => v.id === item.model.active_version_id)?.version ?? "—",
              })}
              {item.model.archived_at ? ` · ${t("scoring.archived")}` : ""}
            </span>
          </button>
        ))}
      </aside>

      <div key={revision} className="min-w-0 space-y-4 text-xs">
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

        <section className="space-y-1">
          <Heading>{t("scoring.formulaTitle")}</Heading>
          <pre className="overflow-x-auto rounded-md border p-2 font-mono text-[11px] whitespace-pre-wrap">
            {formulaText(definition)}
          </pre>
          <InlineNotice variant="partial">{t("scoring.heuristicNotice")}</InlineNotice>
        </section>

        <section className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1">
            <Heading>{t("scoring.finalWeights")}</Heading>
            {definition.final.map((weight) => (
              <div key={weight.component} className="flex items-center justify-between gap-2">
                <span>{t(COMPONENT_KEYS[weight.component])}</span>
                <NumberInput
                  label={t("scoring.finalWeightLabel", {
                    component: t(COMPONENT_KEYS[weight.component]),
                  })}
                  value={weight.weight}
                  min={0}
                  max={100}
                  onChange={(value) =>
                    define({
                      final: definition.final.map((item) =>
                        item.component === weight.component
                          ? { ...item, weight: value ?? 0 }
                          : item,
                      ),
                    })
                  }
                />
              </div>
            ))}
          </div>
          <div className="space-y-1">
            <Heading>{t("scoring.opportunityWeights")}</Heading>
            {definition.opportunity.map((weight) => (
              <div key={weight.component} className="flex items-center justify-between gap-2">
                <span>{t(COMPONENT_KEYS[weight.component])}</span>
                <NumberInput
                  label={t("scoring.opportunityWeightLabel", {
                    component: t(COMPONENT_KEYS[weight.component]),
                  })}
                  value={weight.weight}
                  min={0}
                  max={100}
                  onChange={(value) =>
                    define({
                      opportunity: definition.opportunity.map((item) =>
                        item.component === weight.component
                          ? { ...item, weight: value ?? 0 }
                          : item,
                      ),
                    })
                  }
                />
              </div>
            ))}
          </div>
        </section>

        <section className="space-y-1">
          <Heading>{t("scoring.thresholds")}</Heading>
          <div className="flex flex-wrap items-center gap-3">
            <span className="flex items-center gap-1">
              {t("scoring.priorityThreshold")}
              <NumberInput
                label={t("scoring.priorityThreshold")}
                value={definition.priority_threshold}
                min={0}
                max={100}
                onChange={(value) => define({ priority_threshold: value ?? 0 })}
              />
            </span>
            <span className="flex items-center gap-1">
              {t("scoring.reviewThreshold")}
              <NumberInput
                label={t("scoring.reviewThreshold")}
                value={definition.review_threshold}
                min={0}
                max={100}
                onChange={(value) => define({ review_threshold: value ?? 0 })}
              />
            </span>
            <span className="flex items-center gap-1">
              {t("scoring.weakSubscore")}
              <NumberInput
                label={t("scoring.weakSubscore")}
                value={definition.weak_subscore_below}
                min={0}
                max={100}
                onChange={(value) => define({ weak_subscore_below: value ?? 0 })}
              />
            </span>
            <span className="flex items-center gap-1">
              {t("scoring.minKnown")}
              <NumberInput
                label={t("scoring.minKnown")}
                value={Math.round(definition.min_known_weight * 100)}
                min={0}
                max={100}
                onChange={(value) => define({ min_known_weight: (value ?? 0) / 100 })}
              />
            </span>
            <label className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={definition.require_observable_problem}
                onChange={(event) => define({ require_observable_problem: event.target.checked })}
              />
              {t("scoring.requireProblem")}
            </label>
          </div>
        </section>

        <section className="space-y-1">
          <Heading>{t("scoring.featureWeights")}</Heading>
          <div className="overflow-x-auto">
            <table className="w-full">
              <tbody>
                {catalog.features.map((info) => (
                  <tr key={info.feature} className="border-t align-top">
                    <td className="py-1 pr-2 text-[11px] whitespace-nowrap text-muted-foreground">
                      {t(COMPONENT_KEYS[info.component])}
                    </td>
                    <td className="py-1 pr-2">
                      <p className="font-medium">{codeLabel(t, info.feature)}</p>
                      <p className="text-[11px] text-muted-foreground">{info.description}</p>
                    </td>
                    <td className="py-1">
                      <NumberInput
                        label={t("scoring.featureWeightLabel", {
                          feature: codeLabel(t, info.feature),
                        })}
                        value={
                          definition.features.find((weight) => weight.feature === info.feature)
                            ?.weight ?? 0
                        }
                        min={0}
                        max={10}
                        onChange={(value) =>
                          define({
                            features: definition.features.map((weight) =>
                              weight.feature === info.feature
                                ? { ...weight, weight: value ?? 0 }
                                : weight,
                            ),
                          })
                        }
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="space-y-1">
          <Heading>{t("scoring.notApplicable")}</Heading>
          {definition.not_applicable.map((rule, index) => (
            <div key={index} className="flex flex-wrap items-center gap-2">
              <select
                aria-label={t("scoring.ruleFeature")}
                value={rule.feature}
                onChange={(event) =>
                  define({
                    not_applicable: definition.not_applicable.map((item, i) =>
                      i === index ? { ...item, feature: event.target.value } : item,
                    ),
                  })
                }
                className={SELECT}
              >
                {catalog.features.map((info) => (
                  <option key={info.feature} value={info.feature}>
                    {codeLabel(t, info.feature)}
                  </option>
                ))}
              </select>
              <Input
                aria-label={t("scoring.ruleCategories")}
                placeholder={t("scoring.ruleCategories")}
                defaultValue={fromTerms(rule.categories)}
                onBlur={(event) =>
                  define({
                    not_applicable: definition.not_applicable.map((item, i) =>
                      i === index ? { ...item, categories: toTerms(event.target.value) } : item,
                    ),
                  })
                }
                className="h-7 min-w-48 flex-1 text-xs"
              />
              <Button
                variant="ghost"
                size="icon"
                className="size-7"
                aria-label={t("scoring.removeRule")}
                onClick={() =>
                  define({
                    not_applicable: definition.not_applicable.filter((_, i) => i !== index),
                  })
                }
              >
                <X aria-hidden="true" className="size-3.5" />
              </Button>
            </div>
          ))}
          <Button
            variant="outline"
            size="sm"
            className="h-7"
            onClick={() =>
              define({
                not_applicable: [
                  ...definition.not_applicable,
                  { feature: catalog.features[0]?.feature ?? "", categories: [] },
                ],
              })
            }
          >
            <Plus aria-hidden="true" />
            {t("scoring.addRule")}
          </Button>
        </section>

        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            className="h-7"
            disabled={busy || !draft.modelId || !draft.name.trim()}
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
        </div>

        {view && view.problems.length > 0 && (
          <section className="space-y-1">
            <Heading>{t("scoring.problemsTitle")}</Heading>
            <ProblemsList problems={view.problems} />
          </section>
        )}

        {view && (
          <section className="space-y-2">
            <Heading>{t("scoring.versions")}</Heading>
            {view.model.archived_at && (
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
              activeId={view.model.active_version_id}
              compared={compared}
              canActivate={hasReason && !busy && !view.model.archived_at}
              onLoad={(id) => open(modelDraft(view, id))}
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
              onClick={() => void change(view.model.archived_at ? "restore" : "archive", null)}
            >
              {view.model.archived_at ? (
                <ArchiveRestore aria-hidden="true" />
              ) : (
                <Archive aria-hidden="true" />
              )}
              {t(view.model.archived_at ? "scoring.restore" : "scoring.archive")}
            </Button>
          </section>
        )}
      </div>
    </div>
  );
}

function RunsTab({
  catalog,
  runs,
  onChanged,
}: {
  catalog: ScoringCatalog;
  runs: readonly ScoreRunItem[];
  onChanged: () => Promise<void>;
}) {
  const t = useT();
  const language = useLanguage();
  const profiles = catalog.icp_profiles.filter((item) => !item.profile.archived_at);
  const models = catalog.models.filter((item) => !item.model.archived_at);
  const [profileId, setProfileId] = useState(profiles[0]?.profile.id ?? "");
  const [icpVersion, setIcpVersion] = useState("");
  const [modelVersion, setModelVersion] = useState("");
  const [factsFrom, setFactsFrom] = useState("");
  const [left, setLeft] = useState("");
  const [right, setRight] = useState("");
  const [topK, setTopK] = useState<number | null>(10);
  const [comparison, setComparison] = useState<ScoreComparison | null>(null);
  const [busy, setBusy] = useState(false);

  const profile = profiles.find((item) => item.profile.id === profileId) ?? null;
  const [firstModel] = models;
  const chosenIcp =
    icpVersion ||
    (profile ? (shownVersion(profile.versions, profile.profile.active_version_id)?.id ?? "") : "");
  const chosenModel =
    modelVersion ||
    (firstModel
      ? (shownVersion(firstModel.versions, firstModel.model.active_version_id)?.id ?? "")
      : "");
  const finished = runs.filter((run) => run.finished_at !== null);
  const reusable = finished.filter((run) => run.project_id === profile?.profile.project_id);
  const runLabel = (run: ScoreRunItem) =>
    t("scoring.runName", {
      project: run.project_name,
      icp: run.icp_profile_name,
      icpVersion: run.icp_profile_version,
      model: run.scoring_model_name,
      modelVersion: run.scoring_model_version,
      time: formatDateTime(run.requested_at, language),
    });

  const start = () =>
    profile &&
    attempt(
      t("scoring.startFailed"),
      async () => {
        const page = await desktop.startScoring({
          project_id: profile.profile.project_id,
          icp_profile_version_id: chosenIcp,
          scoring_model_version_id: chosenModel,
          facts_from_run_id: factsFrom || null,
        });
        toast.success(t("scoring.started", { n: page.items.length }));
        await onChanged();
      },
      setBusy,
    );
  const control = (jobId: string, action: RunControlAction) =>
    attempt(
      t("discovery.controlFailed"),
      async () => {
        await desktop.controlScoring({ job_id: jobId, action });
        await onChanged();
      },
      setBusy,
    );
  const compare = () =>
    attempt(
      t("scoring.compareFailed"),
      async () => {
        setComparison(
          await desktop.compareScoreRuns({
            left_run_id: left,
            right_run_id: right,
            top_k: topK ?? 10,
          }),
        );
      },
      setBusy,
    );
  const rankCell = (rank: number | null, tier: string | null, total: number | null) =>
    rank === null ? "—" : `#${rank} · ${tier ?? ""} · ${scoreText(total)}`;

  return (
    <div className="space-y-4 text-xs">
      <section aria-label={t("scoring.startTitle")} className="space-y-2 rounded-md border p-2">
        <Heading>{t("scoring.startTitle")}</Heading>
        {profiles.length === 0 ? (
          <p className="text-muted-foreground">{t("scoring.noProfiles")}</p>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <select
              aria-label={t("scoring.icpProfile")}
              value={profileId}
              onChange={(event) => {
                setProfileId(event.target.value);
                setIcpVersion("");
                setFactsFrom("");
              }}
              className={SELECT}
            >
              {profiles.map((item) => (
                <option key={item.profile.id} value={item.profile.id}>
                  {item.profile.name}
                </option>
              ))}
            </select>
            <select
              aria-label={t("scoring.icpVersion")}
              value={chosenIcp}
              onChange={(event) => setIcpVersion(event.target.value)}
              className={SELECT}
            >
              {profile?.versions.map((version) => (
                <option key={version.id} value={version.id}>
                  {`v${version.version}${version.id === profile.profile.active_version_id ? ` (${t("scoring.active")})` : ""}`}
                </option>
              ))}
            </select>
            <select
              aria-label={t("scoring.modelVersion")}
              value={chosenModel}
              onChange={(event) => setModelVersion(event.target.value)}
              className={SELECT}
            >
              {models.flatMap((item) =>
                item.versions.map((version) => (
                  <option key={version.id} value={version.id}>
                    {`${item.model.name} v${version.version}${version.id === item.model.active_version_id ? ` (${t("scoring.active")})` : ""}`}
                  </option>
                )),
              )}
            </select>
            <select
              aria-label={t("scoring.factsFrom")}
              value={factsFrom}
              onChange={(event) => setFactsFrom(event.target.value)}
              className={SELECT}
            >
              <option value="">{t("scoring.factsNow")}</option>
              {reusable.map((run) => (
                <option key={run.score_run_id} value={run.score_run_id}>
                  {t("scoring.factsOf", { run: runLabel(run) })}
                </option>
              ))}
            </select>
            <Button
              size="sm"
              className="h-7"
              disabled={busy || !chosenIcp || !chosenModel}
              onClick={() => void start()}
            >
              <Play aria-hidden="true" />
              {t("scoring.start")}
            </Button>
          </div>
        )}
      </section>

      <section className="space-y-2">
        <Heading>{t("scoring.runs")}</Heading>
        {runs.length === 0 && <p className="text-muted-foreground">{t("scoring.noRuns")}</p>}
        <ul className="space-y-2">
          {runs.map((run) => (
            <li key={run.job_id} className="space-y-1.5 rounded-md border p-2">
              <JobProgress
                job={{
                  id: run.job_id,
                  name: runLabel(run),
                  state: run.status,
                  done: run.progress_done,
                  total: run.progress_total,
                  errors: run.events.filter((event) => event.level === "error").length,
                }}
              />
              <div className="flex flex-wrap items-center gap-2">
                {(Object.keys(CONTROLS) as RunControlAction[]).map((action) => {
                  const { icon: Icon, states } = CONTROLS[action];
                  if (!states.includes(run.status)) return null;
                  return (
                    <Button
                      key={action}
                      variant="outline"
                      size="sm"
                      className="h-7"
                      disabled={busy}
                      onClick={() => void control(run.job_id, action)}
                    >
                      <Icon aria-hidden="true" />
                      {t(`discovery.control.${action}`)}
                    </Button>
                  );
                })}
                <TierLine counts={run.stats} />
              </div>
              {run.facts_from_run_id && (
                <p className="text-[11px] text-muted-foreground">{t("scoring.factsReused")}</p>
              )}
              {run.finished_at && (
                <Distribution bins={run.distribution} label={t("scoring.distribution")} />
              )}
            </li>
          ))}
        </ul>
      </section>

      <section aria-label={t("scoring.compareTitle")} className="space-y-2 rounded-md border p-2">
        <Heading>{t("scoring.compareTitle")}</Heading>
        <div className="flex flex-wrap items-center gap-2">
          {(
            [
              [t("scoring.left"), left, setLeft],
              [t("scoring.right"), right, setRight],
            ] as const
          ).map(([label, value, setValue]) => (
            <select
              key={label}
              aria-label={label}
              value={value}
              onChange={(event) => setValue(event.target.value)}
              className={SELECT}
            >
              <option value="">{t("scoring.chooseRun")}</option>
              {finished.map((run) => (
                <option key={run.score_run_id} value={run.score_run_id}>
                  {runLabel(run)}
                </option>
              ))}
            </select>
          ))}
          <NumberInput
            label={t("scoring.topK")}
            value={topK}
            min={1}
            max={500}
            onChange={setTopK}
          />
          <Button
            variant="outline"
            size="sm"
            className="h-7"
            disabled={busy || !left || !right || left === right}
            onClick={() => void compare()}
          >
            {t("scoring.compareRun")}
          </Button>
        </div>
        {comparison && (
          <div className="space-y-1">
            <p>
              {t("scoring.compareLine", {
                entered: comparison.entered,
                leftOut: comparison.left_out,
                k: comparison.top_k,
                change:
                  comparison.mean_abs_rank_change === null
                    ? "—"
                    : comparison.mean_abs_rank_change.toFixed(2),
              })}
            </p>
            <p className="text-[11px] text-muted-foreground">
              {t(comparison.same_facts ? "scoring.sameFacts" : "scoring.differentFacts")}
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="text-left text-muted-foreground">
                    <th className="font-normal">{t("businesses.column.name")}</th>
                    <th className="text-right font-normal">{t("scoring.leftRank")}</th>
                    <th className="text-right font-normal">{t("scoring.rightRank")}</th>
                  </tr>
                </thead>
                <tbody>
                  {comparison.changes.map((change) => (
                    <tr key={change.business_id} className="border-t">
                      <td className="py-0.5 pr-2">{change.display_name}</td>
                      <td className="text-right font-mono tabular-nums">
                        {rankCell(
                          change.left_rank,
                          change.left_tier ? t(TIER_KEYS[change.left_tier]) : null,
                          change.left_total,
                        )}
                      </td>
                      <td className="text-right font-mono tabular-nums">
                        {rankCell(
                          change.right_rank,
                          change.right_tier ? t(TIER_KEYS[change.right_tier]) : null,
                          change.right_total,
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

function MetricTable({
  title,
  head,
  rows,
}: {
  title: string;
  head: readonly string[];
  rows: ReadonlyArray<ReadonlyArray<string | number>>;
}) {
  return (
    <section className="space-y-1">
      <Heading>{title}</Heading>
      <div className="overflow-x-auto">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="text-left text-muted-foreground">
              {head.map((cell) => (
                <th key={cell} className="font-normal">
                  {cell}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index} className="border-t">
                {row.map((cell, column) => (
                  <td key={column} className="py-0.5 font-mono tabular-nums">
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function CalibrationTab({ runs }: { runs: readonly ScoreRunItem[] }) {
  const t = useT();
  const language = useLanguage();
  const finished = runs.filter((run) => run.finished_at !== null);
  const [runId, setRunId] = useState(finished[0]?.score_run_id ?? "");
  const fetchReport = useCallback(
    () => (runId ? desktop.getCalibration({ score_run_id: runId }) : Promise.resolve(null)),
    [runId],
  );
  const { data: report, error } = useLoaded(fetchReport, "get_calibration");
  const rate = (value: number | null) => (value === null ? "—" : percent(value));

  if (finished.length === 0) {
    return <StateBlock variant="empty" title={t("scoring.noCalibrationRun")} />;
  }
  return (
    <div className="space-y-3 text-xs">
      <select
        aria-label={t("scoring.calibrationRun")}
        value={runId}
        onChange={(event) => setRunId(event.target.value)}
        className={SELECT}
      >
        {finished.map((run) => (
          <option key={run.score_run_id} value={run.score_run_id}>
            {`${run.icp_profile_name} v${run.icp_profile_version} · ${run.scoring_model_name} v${run.scoring_model_version} · ${formatDateTime(run.finished_at ?? run.requested_at, language)}`}
          </option>
        ))}
      </select>
      {error && (
        <StateBlock variant="error" title={t("scoring.calibrationError")} body={error.message} />
      )}
      {report && (
        <>
          {report.weights_are_heuristic && (
            <InlineNotice variant="partial">{t("scoring.heuristicNotice")}</InlineNotice>
          )}
          <p>
            {t("scoring.calibrationLine", {
              businesses: report.businesses,
              labeled: report.labeled,
              positives: report.positives,
              leakage: report.leakage_excluded,
              coverage: percent(report.coverage),
            })}
          </p>
          <p className="text-[11px] text-muted-foreground">
            {t("scoring.calibrationStages", {
              positive: report.positive_stages.map((stage) => t(STAGE_KEYS[stage])).join(", "),
              negative: report.negative_stages.map((stage) => t(STAGE_KEYS[stage])).join(", "),
            })}
          </p>
          {report.warnings.length > 0 && (
            <ul className="space-y-0.5">
              {report.warnings.map((code) => (
                <li key={code} className="rounded border border-warn/60 px-1.5 py-0.5">
                  {codeLabel(t, code)}
                </li>
              ))}
            </ul>
          )}
          <MetricTable
            title={t("scoring.precision")}
            head={[t("scoring.k"), t("scoring.labeled"), t("scoring.positives"), t("scoring.rate")]}
            rows={report.precision_at_k.map((row) => [
              row.k,
              row.labeled,
              row.positives,
              rate(row.precision),
            ])}
          />
          <MetricTable
            title={t("scoring.lift")}
            head={[
              t("scoring.decile"),
              t("scoring.businesses"),
              t("scoring.labeled"),
              t("scoring.positives"),
              t("scoring.rate"),
              t("scoring.liftValue"),
            ]}
            rows={report.lift_by_decile.map((row) => [
              row.decile,
              row.businesses,
              row.labeled,
              row.positives,
              rate(row.rate),
              row.lift === null ? "—" : row.lift.toFixed(2),
            ])}
          />
          <MetricTable
            title={t("scoring.bands")}
            head={[
              t("scoring.band"),
              t("scoring.businesses"),
              t("scoring.labeled"),
              t("scoring.meanScore"),
              t("scoring.observed"),
            ]}
            rows={report.calibration.map((row) => [
              `${row.low}–${row.high}`,
              row.businesses,
              row.labeled,
              scoreText(row.mean_score),
              rate(row.observed_rate),
            ])}
          />
          <MetricTable
            title={t("scoring.cohorts")}
            head={[
              t("scoring.tier"),
              t("scoring.businesses"),
              t("scoring.labeled"),
              t("scoring.positives"),
              t("scoring.rate"),
            ]}
            rows={report.cohorts.map((row) => [
              t(TIER_KEYS[row.tier]),
              row.businesses,
              row.labeled,
              row.positives,
              rate(row.conversion),
            ])}
          />
        </>
      )}
    </div>
  );
}

/**
 * Scoring Models (phase 11): the declarative formula of every model in plain sight, its weights,
 * thresholds and the features it can weigh; versions to save, clone, compare, activate and archive;
 * score runs with their tiers and distribution, compared side by side; and the calibration of a run
 * against the outcomes recorded after its facts were read. Weights stay declared heuristics.
 */
export function ScoringModelsScreen() {
  const t = useT();
  const fetchCatalog = useCallback(() => desktop.getScoringCatalog({ project_id: null }), []);
  const catalogState = useLoaded(fetchCatalog, "get_scoring_catalog");
  const fetchRuns = useCallback(
    () => desktop.listScoreRuns({ project_id: null, limit: 50, cursor: null }),
    [],
  );
  const runsState = useLoaded(fetchRuns, "list_score_runs");
  const { reload: reloadCatalog } = catalogState;
  const { reload: reloadRuns } = runsState;
  const runs = runsState.data?.items ?? [];

  // polled only while a run moves: the worker never pushes, and an idle screen asks nothing
  const active = runs.some((run) => run.status === "queued" || run.status === "running");
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => void reloadRuns(), POLL_MS);
    return () => clearInterval(timer);
  }, [active, reloadRuns]);

  const error = catalogState.error ?? runsState.error;
  if (error) {
    return (
      <StateBlock
        variant={error.code === "desktop_unavailable" ? "offline" : "error"}
        title={t("scoring.errorTitle")}
        body={error.message}
        action={
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              void reloadCatalog();
              void reloadRuns();
            }}
          >
            {t("common.retry")}
          </Button>
        }
      />
    );
  }
  if (!catalogState.data) return <StateBlock variant="loading" title={t("state.loading")} />;

  return (
    <PageShell>
      <div className="min-h-0 flex-1 overflow-auto p-4">
        <Tabs defaultValue="models" className="mx-auto w-full max-w-5xl">
          <TabsList>
            <TabsTrigger value="models">{t("scoring.tabModels")}</TabsTrigger>
            <TabsTrigger value="runs">{t("scoring.tabRuns")}</TabsTrigger>
            <TabsTrigger value="calibration">{t("scoring.tabCalibration")}</TabsTrigger>
          </TabsList>
          <TabsContent value="models">
            <ModelsTab catalog={catalogState.data} onChanged={reloadCatalog} />
          </TabsContent>
          <TabsContent value="runs">
            <RunsTab catalog={catalogState.data} runs={runs} onChanged={reloadRuns} />
          </TabsContent>
          <TabsContent value="calibration">
            <CalibrationTab runs={runs} />
          </TabsContent>
        </Tabs>
      </div>
    </PageShell>
  );
}
