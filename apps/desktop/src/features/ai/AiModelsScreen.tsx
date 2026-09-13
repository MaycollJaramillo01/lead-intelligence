import { useCallback, useEffect, useState, type FormEvent } from "react";
import type {
  AiProfileChange,
  AiReviewStatus,
  AiStatus,
  AiTaskChange,
  AiTaskType,
  InferenceStatus,
  LlmFitReport,
} from "@lead/shared-types";
import { Download, Gauge, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { PageActions, PageScroll, PageShell, Section } from "@/components/layout/page-chrome";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { StateBadge } from "@/features/diagnostics/StateBadge";
import { InferenceStatusBadge, InferenceView } from "@/features/ai/InferenceView";
import {
  ACTIVITY_KEYS,
  CIRCUIT_KEYS,
  DEVICE_KEYS,
  INFERENCE_STATUSES,
  INFERENCE_STATUS_KEYS,
  PROFILES,
  PROFILE_KEYS,
  REASON_KEYS,
  REVIEW_KEYS,
  REVIEW_STATUSES,
  TASKS,
  TASK_KEYS,
  gigabytes,
  megabytes,
} from "@/features/ai/labels";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { useLoaded } from "@/lib/useLoaded";

const POLL_MS = 5000;
const BUSY_POLL_MS = 2000;
const BENCHMARKED: readonly AiTaskType[] = TASKS.filter((task) => task !== "visual_review");
const CELL = "border-b px-2 py-1 text-left align-top";

function Select<T extends string>({
  label,
  value,
  options,
  labelOf,
  onChange,
  empty,
}: {
  label: string;
  value: T | "";
  options: readonly T[];
  labelOf: (value: T) => string;
  onChange: (value: T | "") => void;
  empty?: string;
}) {
  return (
    <label className="flex items-center gap-1.5 text-xs">
      {label}
      <select
        value={value}
        onChange={(event) => onChange(event.target.value as T | "")}
        className="h-7 rounded-md border bg-transparent px-2 text-xs"
      >
        {empty !== undefined && <option value="">{empty}</option>}
        {options.map((option) => (
          <option key={option} value={option}>
            {labelOf(option)}
          </option>
        ))}
      </select>
    </label>
  );
}

function RuntimeSection({ status }: { status: AiStatus }) {
  const t = useT();
  const language = useLanguage();
  const { runtime, gpu, resources, scheduler } = status;
  return (
    <Section title={t("ai.runtimeTitle")} description={t("ai.runtimeDescription")}>
      <div className="grid gap-3 text-xs md:grid-cols-2">
        <div className="space-y-1">
          <p className="flex items-center gap-2">
            <span className="font-medium">Ollama</span>
            <StateBadge state={runtime.state} />
          </p>
          <p>{runtime.summary}</p>
          <p className="font-mono text-[11px] text-muted-foreground">{runtime.base_url}</p>
          <p className="flex items-center gap-2">
            <span className="font-medium">{t("ai.gpu")}</span>
            <StateBadge state={gpu.state} />
          </p>
          <p>{gpu.summary}</p>
        </div>
        <div className="space-y-1" aria-live="polite">
          <p>
            {t("ai.ramLine", {
              free: megabytes(resources.ram_free_mib),
              total: megabytes(resources.ram_total_mib),
            })}
          </p>
          <p>
            {t("ai.vramLine", {
              free: megabytes(resources.vram_free_mib),
              total: megabytes(resources.vram_total_mib),
            })}
          </p>
          {resources.pressure.length > 0 && (
            <p className="text-warn">
              {t("ai.pressure", { reasons: resources.pressure.join(", ") })}
              {resources.crawl_waiting ? ` · ${t("ai.crawlWaiting")}` : ""}
            </p>
          )}
          <p>
            {t("ai.queueLine", {
              queued: scheduler.queued,
              running: scheduler.running,
              capacity: scheduler.capacity,
              latency: scheduler.latency_p50_ms ?? "—",
            })}
          </p>
          <p>
            {t("ai.circuitLine", {
              state: t(CIRCUIT_KEYS[scheduler.circuit]),
              failures: scheduler.consecutive_failures,
              completed: scheduler.completed,
              failed: scheduler.failed,
              cached: scheduler.cache_hits,
            })}
          </p>
          {scheduler.last_error && (
            <p className="text-[11px] text-danger">{scheduler.last_error}</p>
          )}
          <p className="text-[11px] text-muted-foreground">
            {t("ai.sampledAt", { time: formatDateTime(resources.sampled_at, language) })}
          </p>
        </div>
      </div>
    </Section>
  );
}

function ModelsSection({
  status,
  onStatus,
}: {
  status: AiStatus;
  onStatus: (s: AiStatus) => void;
}) {
  const t = useT();
  const [removing, setRemoving] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const [pullName, setPullName] = useState("");
  const [pullConfirm, setPullConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const change = status.model_change;

  const send = async (action: "pull" | "delete", model: string, confirmation: string) => {
    setBusy(true);
    try {
      onStatus(await desktop.changeAiModel({ action, model, confirmation }));
      toast.success(t(action === "pull" ? "ai.pullStarted" : "ai.deleted", { model }));
      setRemoving(null);
      setTyped("");
      setPullName("");
      setPullConfirm("");
    } catch (cause) {
      toast.error(t("ai.modelChangeFailed"), {
        description: toIpcError(cause, "change_ai_model").message,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section title={t("ai.modelsTitle")} description={t("ai.modelsDescription")}>
      {status.models.length === 0 ? (
        <StateBlock
          variant="empty"
          title={t("ai.noModelsTitle")}
          body={t(status.runtime.running ? "ai.noModelsBody" : "ai.runtimeDownBody")}
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="text-muted-foreground">
                <th className={CELL}>{t("ai.column.model")}</th>
                <th className={CELL}>{t("ai.column.size")}</th>
                <th className={CELL}>{t("ai.column.context")}</th>
                <th className={CELL}>{t("ai.column.capabilities")}</th>
                <th className={CELL}>{t("ai.column.estimate")}</th>
                <th className={CELL}>{t("ai.column.loaded")}</th>
                <th className={CELL}>
                  <span className="sr-only">{t("ai.column.actions")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {status.models.map((model) => (
                <tr key={model.name}>
                  <td className={CELL}>
                    <span className="font-mono">{model.name}</span>
                    <span className="block text-[11px] text-muted-foreground">
                      {[model.family, model.parameter_size, model.quantization]
                        .filter(Boolean)
                        .join(" · ")}
                    </span>
                  </td>
                  <td className={CELL}>{gigabytes(model.size_bytes)}</td>
                  <td className={CELL}>{model.context_length?.toLocaleString("en-US") ?? "—"}</td>
                  <td className={CELL}>{model.capabilities.join(", ") || "—"}</td>
                  <td className={CELL}>
                    {model.estimate ? (
                      <>
                        {t("ai.estimateLine", {
                          total: megabytes(model.estimate.total_mib),
                          context: model.estimate.num_ctx,
                          device: t(DEVICE_KEYS[model.estimate.device]),
                        })}
                        {model.estimate.note && (
                          <span className="block text-[11px] text-warn">{model.estimate.note}</span>
                        )}
                      </>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className={CELL}>
                    {model.loaded ? gigabytes(model.size_vram_bytes) : t("ai.notLoaded")}
                  </td>
                  <td className={CELL}>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7"
                      aria-label={t("ai.deleteModel", { model: model.name })}
                      onClick={() => {
                        setRemoving(model.name);
                        setTyped("");
                      }}
                    >
                      <Trash2 aria-hidden="true" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {removing && (
        <form
          className="mt-3 space-y-2 rounded-md border border-danger/50 p-2 text-xs"
          onSubmit={(event) => {
            event.preventDefault();
            if (typed === removing) void send("delete", removing, typed);
          }}
        >
          <p>{t("ai.deleteConfirm", { model: removing })}</p>
          <Input
            aria-label={t("ai.typeModelName")}
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            className="h-7 text-xs"
          />
          <div className="flex gap-2">
            <Button
              type="submit"
              variant="destructive"
              size="sm"
              className="h-7"
              disabled={busy || typed !== removing}
            >
              {t("ai.deleteForever")}
            </Button>
            <Button variant="outline" size="sm" className="h-7" onClick={() => setRemoving(null)}>
              {t("common.cancel")}
            </Button>
          </div>
        </form>
      )}
      <form
        className="mt-3 flex flex-wrap items-end gap-2 text-xs"
        onSubmit={(event: FormEvent) => {
          event.preventDefault();
          if (pullName && pullConfirm === pullName) void send("pull", pullName, pullConfirm);
        }}
      >
        <label className="flex flex-col gap-1">
          {t("ai.pullModel")}
          <Input
            value={pullName}
            onChange={(event) => setPullName(event.target.value.trim())}
            placeholder="qwen3:8b"
            className="h-7 w-44 text-xs"
          />
        </label>
        <label className="flex flex-col gap-1">
          {t("ai.typeModelName")}
          <Input
            value={pullConfirm}
            onChange={(event) => setPullConfirm(event.target.value.trim())}
            className="h-7 w-44 text-xs"
          />
        </label>
        <Button
          type="submit"
          size="sm"
          className="h-7"
          disabled={
            busy || !status.downloads_allowed || pullName === "" || pullConfirm !== pullName
          }
        >
          <Download aria-hidden="true" />
          {t("ai.download")}
        </Button>
        {!status.downloads_allowed && <span className="text-warn">{t("ai.downloadsOff")}</span>}
      </form>
      {change && (
        <p className="mt-2 text-xs" aria-live="polite">
          {t("ai.changeLine", {
            action: change.action,
            model: change.model,
            state: t(ACTIVITY_KEYS[change.state]),
            detail: change.detail ?? "",
            progress:
              change.total_bytes && change.completed_bytes !== null
                ? `${Math.round((change.completed_bytes / change.total_bytes) * 100)}%`
                : "",
          })}
        </p>
      )}
    </Section>
  );
}

function SettingsSection({
  status,
  onStatus,
}: {
  status: AiStatus;
  onStatus: (s: AiStatus) => void;
}) {
  const t = useT();
  const [profiles, setProfiles] = useState<AiProfileChange[]>(() =>
    status.profiles.map(({ profile, model, num_ctx, allow_cpu }) => ({
      profile,
      model,
      num_ctx,
      allow_cpu,
    })),
  );
  const [tasks, setTasks] = useState<AiTaskChange[]>(() =>
    status.tasks.map(({ task_type, enabled, profile }) => ({ task_type, enabled, profile })),
  );
  const [ram, setRam] = useState(status.limits.min_free_ram_mib);
  const [vram, setVram] = useState(status.limits.min_free_vram_mib);
  const [busy, setBusy] = useState(false);
  const modelNames = status.models
    .filter((model) => !model.capabilities.includes("embedding"))
    .map((model) => model.name);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      onStatus(
        await desktop.configureAi({
          profiles,
          tasks,
          min_free_ram_mib: ram,
          min_free_vram_mib: vram,
        }),
      );
      toast.success(t("ai.settingsSaved"));
    } catch (cause) {
      toast.error(t("ai.settingsFailed"), {
        description: toIpcError(cause, "configure_ai").message,
      });
    } finally {
      setBusy(false);
    }
  };

  const info = new Map(status.tasks.map((task) => [task.task_type, task]));
  return (
    <Section title={t("ai.settingsTitle")} description={t("ai.settingsDescription")}>
      <form onSubmit={(event) => void save(event)} className="space-y-3 text-xs">
        <fieldset className="space-y-1.5">
          <legend className="font-medium">{t("ai.profiles")}</legend>
          {profiles.map((profile, index) => (
            <div key={profile.profile} className="flex flex-wrap items-center gap-3">
              <span className="w-20">{t(PROFILE_KEYS[profile.profile])}</span>
              <Select
                label={t("ai.model")}
                value={profile.model ?? ""}
                options={modelNames}
                labelOf={(name) => name}
                empty={t("ai.noModel")}
                onChange={(model) =>
                  setProfiles(
                    profiles.map((p, i) => (i === index ? { ...p, model: model || null } : p)),
                  )
                }
              />
              <label className="flex items-center gap-1.5">
                {t("ai.context")}
                <Input
                  type="number"
                  min={512}
                  max={32768}
                  step={512}
                  value={profile.num_ctx}
                  onChange={(event) =>
                    setProfiles(
                      profiles.map((p, i) =>
                        i === index ? { ...p, num_ctx: Number(event.target.value) } : p,
                      ),
                    )
                  }
                  className="h-7 w-24 text-xs"
                />
              </label>
              <label className="flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={profile.allow_cpu}
                  onChange={(event) =>
                    setProfiles(
                      profiles.map((p, i) =>
                        i === index ? { ...p, allow_cpu: event.target.checked } : p,
                      ),
                    )
                  }
                />
                {t("ai.allowCpu")}
              </label>
            </div>
          ))}
        </fieldset>
        <fieldset className="space-y-1.5">
          <legend className="font-medium">{t("ai.tasks")}</legend>
          {tasks.map((task, index) => {
            const current = info.get(task.task_type);
            return (
              <div key={task.task_type} className="flex flex-wrap items-center gap-3">
                <label className="flex w-56 items-center gap-1">
                  <input
                    type="checkbox"
                    checked={task.enabled}
                    onChange={(event) =>
                      setTasks(
                        tasks.map((item, i) =>
                          i === index ? { ...item, enabled: event.target.checked } : item,
                        ),
                      )
                    }
                  />
                  {t(TASK_KEYS[task.task_type])}
                </label>
                <Select
                  label={t("ai.profile")}
                  value={task.profile}
                  options={PROFILES}
                  labelOf={(value) => t(PROFILE_KEYS[value])}
                  onChange={(value) =>
                    value &&
                    setTasks(
                      tasks.map((item, i) => (i === index ? { ...item, profile: value } : item)),
                    )
                  }
                />
                <span className={current?.available ? "text-ok" : "text-muted-foreground"}>
                  {current?.available
                    ? t("ai.available", { model: current.model ?? "" })
                    : current?.unavailable_reason
                      ? t(REASON_KEYS[current.unavailable_reason])
                      : ""}
                </span>
                {current && (
                  <span className="font-mono text-[10px] text-muted-foreground">
                    {`v${current.task_version} · ${current.prompt_template_id} ${current.prompt_template_version} · ${current.timeout_s}s · T=${current.temperature}`}
                  </span>
                )}
              </div>
            );
          })}
        </fieldset>
        <fieldset className="flex flex-wrap items-center gap-3">
          <legend className="mb-1 font-medium">{t("ai.limits")}</legend>
          <label className="flex items-center gap-1.5">
            {t("ai.minFreeRam")}
            <Input
              type="number"
              min={256}
              max={65536}
              value={ram}
              onChange={(event) => setRam(Number(event.target.value))}
              className="h-7 w-24 text-xs"
            />
          </label>
          <label className="flex items-center gap-1.5">
            {t("ai.minFreeVram")}
            <Input
              type="number"
              min={0}
              max={65536}
              value={vram}
              onChange={(event) => setVram(Number(event.target.value))}
              className="h-7 w-24 text-xs"
            />
          </label>
        </fieldset>
        <Button type="submit" size="sm" className="h-7" disabled={busy}>
          {t("ai.saveSettings")}
        </Button>
      </form>
    </Section>
  );
}

function BenchmarkSection({
  status,
  onStatus,
}: {
  status: AiStatus;
  onStatus: (s: AiStatus) => void;
}) {
  const t = useT();
  const language = useLanguage();
  const [models, setModels] = useState<string[]>([]);
  const [tasks, setTasks] = useState<AiTaskType[]>(["business_classification"]);
  const [busy, setBusy] = useState(false);
  const { benchmark } = status;
  const running = benchmark.state === "running";
  const candidates = status.models.filter((model) => model.capabilities.includes("completion"));

  const send = async (action: "start" | "cancel") => {
    setBusy(true);
    try {
      onStatus(
        await desktop.runAiBenchmark({
          action,
          models: action === "start" ? models : [],
          task_types: action === "start" ? tasks : [],
        }),
      );
    } catch (cause) {
      toast.error(t("ai.benchmarkFailed"), {
        description: toIpcError(cause, "run_ai_benchmark").message,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section title={t("ai.benchmarkTitle")} description={t("ai.benchmarkDescription")}>
      <div className="space-y-3 text-xs">
        <div className="space-y-1">
          <h3 className="font-medium">{t("ai.recommendations")}</h3>
          <InlineNotice variant="partial">{t("ai.recommendationNotice")}</InlineNotice>
          {status.recommendations.length === 0 ? (
            <p className="text-muted-foreground">{t("ai.noRecommendations")}</p>
          ) : (
            <ul className="space-y-0.5">
              {status.recommendations.map((item) => (
                <li key={item.task_type}>
                  {t("ai.recommendationLine", {
                    task: t(TASK_KEYS[item.task_type]),
                    model: item.model,
                    accuracy: Math.round(item.accuracy * 100),
                    latency: item.latency_p50_ms ?? "—",
                    time: formatDateTime(item.benchmarked_at, language),
                  })}
                </li>
              ))}
            </ul>
          )}
        </div>
        <fieldset className="flex flex-wrap gap-x-3 gap-y-1">
          <legend className="mb-1 font-medium">{t("ai.benchmarkModels")}</legend>
          {candidates.map((model) => (
            <label key={model.name} className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={models.includes(model.name)}
                disabled={!models.includes(model.name) && models.length >= 4}
                onChange={() =>
                  setModels(
                    models.includes(model.name)
                      ? models.filter((name) => name !== model.name)
                      : [...models, model.name],
                  )
                }
              />
              <span className="font-mono">{model.name}</span>
            </label>
          ))}
        </fieldset>
        <fieldset className="flex flex-wrap gap-x-3 gap-y-1">
          <legend className="mb-1 font-medium">{t("ai.benchmarkTasks")}</legend>
          {BENCHMARKED.map((task) => (
            <label key={task} className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={tasks.includes(task)}
                onChange={() =>
                  setTasks(
                    tasks.includes(task) ? tasks.filter((v) => v !== task) : [...tasks, task],
                  )
                }
              />
              {t(TASK_KEYS[task])}
            </label>
          ))}
        </fieldset>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            className="h-7"
            disabled={busy || running || models.length === 0 || tasks.length === 0}
            onClick={() => void send("start")}
          >
            <Gauge aria-hidden="true" />
            {t("ai.runBenchmark")}
          </Button>
          {running && (
            <Button variant="outline" size="sm" className="h-7" onClick={() => void send("cancel")}>
              {t("common.cancel")}
            </Button>
          )}
          <span aria-live="polite">
            {t("ai.benchmarkState", {
              state: t(ACTIVITY_KEYS[benchmark.state]),
              done: benchmark.done,
              total: benchmark.total,
              current: benchmark.current ?? "",
            })}
          </span>
          {benchmark.error && <span className="text-danger">{benchmark.error}</span>}
        </div>
        {status.benchmark_results.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="text-muted-foreground">
                  <th className={CELL}>{t("ai.column.model")}</th>
                  <th className={CELL}>{t("ai.column.task")}</th>
                  <th className={CELL}>{t("ai.column.accuracy")}</th>
                  <th className={CELL}>{t("ai.column.valid")}</th>
                  <th className={CELL}>{t("ai.column.latency")}</th>
                  <th className={CELL}>{t("ai.column.throughput")}</th>
                  <th className={CELL}>{t("ai.column.memory")}</th>
                  <th className={CELL}>{t("ai.column.device")}</th>
                </tr>
              </thead>
              <tbody>
                {status.benchmark_results.map((result) => (
                  <tr key={result.id}>
                    <td className={`${CELL} font-mono`}>{result.model}</td>
                    <td className={CELL}>{t(TASK_KEYS[result.task_type])}</td>
                    <td className={CELL}>{`${result.correct}/${result.cases}`}</td>
                    <td className={CELL}>{`${result.valid_outputs}/${result.cases}`}</td>
                    <td className={CELL}>
                      {`${result.latency_p50_ms ?? "—"} / ${result.latency_p95_ms ?? "—"} ms`}
                    </td>
                    <td className={CELL}>
                      {result.tokens_per_second === null
                        ? "—"
                        : `${result.tokens_per_second} tok/s`}
                    </td>
                    <td className={CELL}>
                      {t("ai.memoryLine", {
                        vram: megabytes(result.peak_vram_used_mib),
                        ram: megabytes(result.min_free_ram_mib),
                      })}
                    </td>
                    <td className={CELL}>{t(DEVICE_KEYS[result.device])}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Section>
  );
}

function LlmFitSection({ report }: { report: LlmFitReport }) {
  const t = useT();
  return (
    <Section title={t("ai.llmFitTitle")} description={t("ai.llmFitDescription")}>
      {report.error ? (
        <InlineNotice variant="partial">{report.error}</InlineNotice>
      ) : (
        <ul className="space-y-1 text-xs">
          {report.models.map((model) => (
            <li key={model.name} className="rounded border p-1.5">
              <span className="font-mono">{model.ollama_name ?? model.name}</span>
              <span className="text-muted-foreground">
                {` · ${[model.fit_level, model.run_mode, model.runtime, model.quantization]
                  .filter(Boolean)
                  .join(" · ")}`}
                {model.memory_required_gb !== null ? ` · ${model.memory_required_gb} GB` : ""}
                {model.estimated_tps !== null ? ` · ~${model.estimated_tps} tok/s` : ""}
              </span>
              {model.notes.length > 0 && (
                <span className="block text-[11px] text-muted-foreground">
                  {model.notes.join(" · ")}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function HistorySection() {
  const t = useT();
  const language = useLanguage();
  const [task, setTask] = useState<AiTaskType | "">("");
  const [state, setState] = useState<InferenceStatus | "">("");
  const [review, setReview] = useState<AiReviewStatus | "">("");
  const [opened, setOpened] = useState<string | null>(null);

  const fetchPage = useCallback(
    () =>
      desktop.listAiInferences({
        task_type: task || null,
        status: state || null,
        business_id: null,
        review_status: review || null,
        limit: 100,
        cursor: null,
      }),
    [task, state, review],
  );
  const { data: page, error, reload: load } = useLoaded(fetchPage, "list_ai_inferences");

  return (
    <Section
      title={t("ai.historyTitle")}
      description={t("ai.historyDescription")}
      actions={
        <Button variant="ghost" size="sm" className="h-7" onClick={() => void load()}>
          <RefreshCw aria-hidden="true" />
          {t("ai.refresh")}
        </Button>
      }
    >
      <div className="space-y-2 text-xs">
        <div className="flex flex-wrap gap-3">
          <Select
            label={t("ai.column.task")}
            value={task}
            options={TASKS}
            labelOf={(v) => t(TASK_KEYS[v])}
            onChange={setTask}
            empty={t("ai.any")}
          />
          <Select
            label={t("ai.column.status")}
            value={state}
            options={INFERENCE_STATUSES}
            labelOf={(v) => t(INFERENCE_STATUS_KEYS[v])}
            onChange={setState}
            empty={t("ai.any")}
          />
          <Select
            label={t("ai.column.review")}
            value={review}
            options={REVIEW_STATUSES}
            labelOf={(v) => t(REVIEW_KEYS[v])}
            onChange={setReview}
            empty={t("ai.any")}
          />
        </div>
        {error ? (
          <StateBlock variant="error" title={t("ai.historyErrorTitle")} body={error.message} />
        ) : page === null ? (
          <StateBlock variant="loading" title={t("state.loading")} />
        ) : page.items.length === 0 ? (
          <StateBlock
            variant="empty"
            title={t("ai.historyEmptyTitle")}
            body={t("ai.historyEmptyBody")}
          />
        ) : (
          <div className="overflow-x-auto">
            <p className="mb-1 text-muted-foreground">{t("ai.historyTotal", { n: page.total })}</p>
            <table className="w-full border-collapse">
              <thead>
                <tr className="text-muted-foreground">
                  <th className={CELL}>{t("ai.column.task")}</th>
                  <th className={CELL}>{t("ai.column.status")}</th>
                  <th className={CELL}>{t("ai.column.review")}</th>
                  <th className={CELL}>{t("ai.column.business")}</th>
                  <th className={CELL}>{t("ai.column.model")}</th>
                  <th className={CELL}>{t("ai.column.duration")}</th>
                  <th className={CELL}>{t("ai.column.when")}</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((item) => (
                  <tr key={item.id} className={opened === item.id ? "bg-muted" : undefined}>
                    <td className={CELL}>
                      <button
                        type="button"
                        className="text-left underline-offset-2 hover:underline"
                        aria-expanded={opened === item.id}
                        onClick={() => setOpened(opened === item.id ? null : item.id)}
                      >
                        {t(TASK_KEYS[item.task_type])}
                      </button>
                    </td>
                    <td className={CELL}>
                      <InferenceStatusBadge status={item.status} />
                      {item.error_code && (
                        <span className="block text-[11px] text-danger">{item.error_code}</span>
                      )}
                    </td>
                    <td className={CELL}>{t(REVIEW_KEYS[item.review_status])}</td>
                    <td className={CELL}>{item.business_name ?? "—"}</td>
                    <td className={`${CELL} font-mono`}>{item.model}</td>
                    <td className={CELL}>
                      {item.duration_ms === null ? "—" : `${item.duration_ms} ms`}
                    </td>
                    <td className={CELL}>{formatDateTime(item.created_at, language)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {opened && (
          <div className="rounded-md border p-2">
            <InferenceView key={opened} inferenceId={opened} />
          </div>
        )}
      </div>
    </Section>
  );
}

/**
 * AI Models (phase 10): the local runtime and GPU, installed models with their VRAM estimates,
 * which model each profile and task uses, live memory and queue state, benchmarks and the
 * recommendations they support, the external LLM Fit opinion and the inference history. Nothing is
 * downloaded or deleted without typing the model's name; without a runtime every task says why.
 */
export function AiModelsScreen() {
  const t = useT();
  const fetchStatus = useCallback(() => desktop.getAiStatus({ include_llm_fit: false }), []);
  const { data: polled, error, reload: refresh } = useLoaded(fetchStatus, "get_ai_status");
  // an action answers with a fresher status than the last poll: show whichever is newest
  const [fresh, setStatus] = useState<AiStatus | null>(null);
  const status = fresh && (!polled || fresh.checked_at > polled.checked_at) ? fresh : polled;
  const [llmFit, setLlmFit] = useState<LlmFitReport | null>(null);
  const [asking, setAsking] = useState(false);
  const [version, setVersion] = useState(0);
  const busy = status?.benchmark.state === "running" || status?.model_change?.state === "running";
  useEffect(() => {
    const timer = setInterval(() => void refresh(), busy ? BUSY_POLL_MS : POLL_MS);
    return () => clearInterval(timer);
  }, [busy, refresh]);

  const askLlmFit = async () => {
    setAsking(true);
    try {
      const answered = await desktop.getAiStatus({ include_llm_fit: true });
      setStatus(answered);
      setLlmFit(answered.llm_fit);
    } catch (cause) {
      toast.error(t("ai.llmFitFailed"), {
        description: toIpcError(cause, "get_ai_status").message,
      });
    } finally {
      setAsking(false);
    }
  };

  const saved = (next: AiStatus) => {
    setStatus(next);
    setVersion((value) => value + 1);
  };

  return (
    <PageShell>
      <PageActions>
        <Button variant="ghost" size="sm" className="h-7" onClick={() => void refresh()}>
          <RefreshCw aria-hidden="true" />
          {t("ai.refresh")}
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-7"
          disabled={asking}
          onClick={() => void askLlmFit()}
        >
          {t("ai.askLlmFit")}
        </Button>
      </PageActions>
      <PageScroll>
        {error ? (
          <StateBlock
            variant={error.code === "desktop_unavailable" ? "offline" : "error"}
            title={t("ai.errorTitle")}
            body={error.message}
            action={
              <Button variant="outline" size="sm" onClick={() => void refresh()}>
                {t("common.retry")}
              </Button>
            }
          />
        ) : status === null ? (
          <StateBlock variant="loading" title={t("state.loading")} />
        ) : (
          <>
            {!status.runtime.running && (
              <InlineNotice variant="offline">{t("ai.worksWithoutAi")}</InlineNotice>
            )}
            <RuntimeSection status={status} />
            <ModelsSection status={status} onStatus={saved} />
            <SettingsSection key={version} status={status} onStatus={saved} />
            <BenchmarkSection status={status} onStatus={setStatus} />
            {llmFit && <LlmFitSection report={llmFit} />}
            <HistorySection />
          </>
        )}
      </PageScroll>
    </PageShell>
  );
}
