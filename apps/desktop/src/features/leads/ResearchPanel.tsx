import { useEffect, useState, type FormEvent } from "react";
import type {
  CanonicalField,
  RefreshScope,
  ResearchAction,
  ResearchActionRequest,
  ResearchStatus,
  ResearchWorkspace,
} from "@lead/shared-types";
import { Check, Undo2, X } from "lucide-react";
import { toast } from "sonner";
import { TestDataBadge } from "@/components/data/cells";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { EvidenceChip } from "@/features/leads/EvidenceChip";
import {
  CORRECTABLE_FIELDS,
  CORRECTABLE_FIELD_KEYS,
  CRITERION_KEYS,
  EVIDENCE_KIND_KEYS,
  REFRESH_SCOPES,
  REFRESH_SCOPE_KEYS,
  RESEARCH_STATUSES,
  RESEARCH_STATUS_KEYS,
  TASK_STATUS_KEYS,
  codeText,
} from "@/features/leads/labels";
import { researchRequest } from "@/features/leads/research";
import { STAGE_KEYS } from "@/features/scoring/labels";
import { Heading } from "@/features/scoring/parts";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop, toIpcError, type IpcError } from "@/lib/ipc";
import { cn } from "@/lib/utils";

type Loaded = { workspace: ResearchWorkspace; error: null } | { workspace: null; error: IpcError };

const SELECT = "h-7 rounded-md border bg-transparent px-2 text-xs";

const isStatus = (value: string): value is ResearchStatus => value in RESEARCH_STATUS_KEYS;

function Passed({ passed }: { passed: boolean }) {
  const t = useT();
  return (
    <>
      {passed ? (
        <Check aria-hidden="true" className="mt-0.5 size-3 shrink-0 text-ok" />
      ) : (
        <X aria-hidden="true" className="mt-0.5 size-3 shrink-0 text-danger" />
      )}
      <span className="sr-only">{t(passed ? "common.yes" : "common.no")}</span>
    </>
  );
}

/**
 * A lead's research workspace (phase 12): its status with the reason and who set it, the exact
 * criteria sales-ready needs, the checklist, tasks, pinned evidence, notes, tags, assignee,
 * corrections that override a value without touching the source, refresh requests and the history.
 * Everything that removes or overrides something needs a reason, and every change is recorded.
 */
export function ResearchPanel({ businessId }: { businessId: string }) {
  const t = useT();
  const language = useLanguage();
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("");
  const [status, setStatus] = useState<ResearchStatus>("needs_research");
  const [assignee, setAssignee] = useState("");
  const [tag, setTag] = useState("");
  const [note, setNote] = useState("");
  const [taskTitle, setTaskTitle] = useState("");
  const [field, setField] = useState<CanonicalField>("phone");
  const [value, setValue] = useState("");
  const [scopes, setScopes] = useState<RefreshScope[]>([]);

  useEffect(() => {
    let current = true;
    desktop.getResearchWorkspace({ business_id: businessId }).then(
      (workspace) => current && setLoaded({ workspace, error: null }),
      (cause: unknown) =>
        current &&
        setLoaded({ workspace: null, error: toIpcError(cause, "get_research_workspace") }),
    );
    return () => {
      current = false;
    };
  }, [businessId]);

  if (loaded === null) return <StateBlock variant="loading" title={t("state.loading")} />;
  if (loaded.workspace === null) {
    return (
      <StateBlock
        variant={loaded.error.code === "desktop_unavailable" ? "offline" : "error"}
        title={t("research.loadError")}
        body={loaded.error.message}
      />
    );
  }

  const workspace = loaded.workspace;
  const research = workspace.research;
  const why = reason.trim();
  const idPrefix = `research-${businessId}`;
  const pins = workspace.pins.filter(({ pin }) => pin.unpinned_at === null);
  const notes = workspace.notes.filter((item) => item.deleted_at === null);
  const statusText = (text: string | null) =>
    text === null ? "—" : isStatus(text) ? t(RESEARCH_STATUS_KEYS[text]) : text;

  /** Sends one action; true when it was saved, so the caller can clear what it typed. */
  const act = async (
    action: ResearchAction,
    fields: Partial<Omit<ResearchActionRequest, "business_id" | "action">> = {},
  ) => {
    setBusy(true);
    try {
      const updated = await desktop.actOnResearch(researchRequest(businessId, action, fields));
      setLoaded({ workspace: updated, error: null });
      toast.success(t("research.saved"));
      return true;
    } catch (cause) {
      toast.error(t("research.failed"), {
        description: toIpcError(cause, "act_on_research").message,
      });
      return false;
    } finally {
      setBusy(false);
    }
  };

  const withReason = async (
    action: ResearchAction,
    fields: Partial<Omit<ResearchActionRequest, "business_id" | "action">> = {},
  ) => {
    if (why && (await act(action, { ...fields, reason: why }))) setReason("");
  };

  const submitStatus = (event: FormEvent) => {
    event.preventDefault();
    void withReason("set_status", { status });
  };

  return (
    <section aria-label={t("research.panelTitle")} className="space-y-3 text-xs">
      <div className="flex items-center justify-between gap-2">
        <Heading>{t("research.panelTitle")}</Heading>
        {workspace.is_synthetic && <TestDataBadge />}
      </div>
      {workspace.suppressed && (
        <InlineNotice variant="blocked">{t("research.suppressed")}</InlineNotice>
      )}

      <div className="space-y-1 rounded-md border p-2">
        <p className="font-medium">{t("research.status")}</p>
        {research?.status ? (
          <>
            <p>
              {t("research.statusLine", {
                status: t(RESEARCH_STATUS_KEYS[research.status]),
                actor: research.status_by ?? "—",
                time: research.status_at ? formatDateTime(research.status_at, language) : "—",
              })}
            </p>
            {research.status_reason && (
              <p className="text-[11px] break-words text-muted-foreground">
                {research.status_reason}
              </p>
            )}
          </>
        ) : (
          <p className="text-muted-foreground">{t("research.noStatus")}</p>
        )}
        <p className="text-[11px] text-muted-foreground">
          {`${t("explorer.field.lead_stage")}: ${t(STAGE_KEYS[workspace.lead_stage])}`}
        </p>
      </div>

      <div className="space-y-1">
        <Heading>{t("research.criteria")}</Heading>
        <ul className="space-y-0.5">
          {workspace.sales_ready.map((check) => (
            <li key={check.criterion} className="flex items-start gap-1.5">
              <Passed passed={check.passed} />
              <span className="min-w-0 break-words">
                <span className="font-medium">{t(CRITERION_KEYS[check.criterion])}</span>
                {" · "}
                <span className="text-muted-foreground">{check.detail}</span>
              </span>
            </li>
          ))}
        </ul>
        {!workspace.sales_ready_allowed && (
          <InlineNotice variant="blocked">{t("research.salesReadyBlocked")}</InlineNotice>
        )}
      </div>

      <form className="space-y-1.5" onSubmit={submitStatus}>
        <Label htmlFor={`${idPrefix}-reason`} className="text-xs">
          {t("research.reason")}
        </Label>
        <Textarea
          id={`${idPrefix}-reason`}
          value={reason}
          maxLength={2000}
          onChange={(event) => setReason(event.target.value)}
          className="min-h-12 text-xs"
        />
        <p className="text-[11px] text-muted-foreground">{t("research.reasonHelp")}</p>
        <div className="flex flex-wrap items-center gap-1.5">
          <select
            aria-label={t("research.status")}
            value={status}
            onChange={(event) => {
              if (isStatus(event.target.value)) setStatus(event.target.value);
            }}
            className={SELECT}
          >
            {RESEARCH_STATUSES.map((option) => (
              <option
                key={option}
                value={option}
                disabled={option === "sales_ready" && !workspace.sales_ready_allowed}
              >
                {t(RESEARCH_STATUS_KEYS[option])}
              </option>
            ))}
          </select>
          <Button
            type="submit"
            size="sm"
            className="h-7"
            disabled={busy || !why || (status === "sales_ready" && !workspace.sales_ready_allowed)}
          >
            {t("research.setStatus")}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7"
            disabled={busy || !why || !workspace.can_undo}
            onClick={() => void withReason("undo_status")}
          >
            <Undo2 aria-hidden="true" />
            {t("research.undo")}
          </Button>
        </div>
      </form>

      <div className="grid gap-2 sm:grid-cols-2">
        <div className="space-y-1">
          <Heading>{t("research.assignee")}</Heading>
          <p>{research?.assignee ?? <span className="text-muted-foreground">—</span>}</p>
          <div className="flex gap-1">
            <Input
              aria-label={t("research.assignee")}
              value={assignee}
              maxLength={120}
              onChange={(event) => setAssignee(event.target.value)}
              className="h-7 text-xs"
            />
            <Button
              variant="outline"
              size="sm"
              className="h-7"
              disabled={busy || !assignee.trim()}
              onClick={async () => {
                if (await act("assign", { assignee: assignee.trim() })) setAssignee("");
              }}
            >
              {t("research.assign")}
            </Button>
            {research?.assignee && (
              <Button
                variant="ghost"
                size="sm"
                className="h-7"
                disabled={busy}
                onClick={() => void act("unassign", { reason: why || null })}
              >
                {t("research.unassign")}
              </Button>
            )}
          </div>
        </div>
        <div className="space-y-1">
          <Heading>{t("research.tags")}</Heading>
          {workspace.tags.length > 0 && (
            <ul className="flex flex-wrap gap-1">
              {workspace.tags.map((item) => (
                <li key={item} className="inline-flex items-center gap-0.5 rounded border pl-1">
                  {item}
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-5"
                    aria-label={t("research.removeTag", { tag: item })}
                    disabled={busy}
                    onClick={() => void act("remove_tag", { tag: item })}
                  >
                    <X aria-hidden="true" className="size-3" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
          <div className="flex gap-1">
            <Input
              aria-label={t("research.addTag")}
              value={tag}
              maxLength={64}
              onChange={(event) => setTag(event.target.value)}
              className="h-7 text-xs"
            />
            <Button
              variant="outline"
              size="sm"
              className="h-7"
              disabled={busy || !tag.trim()}
              onClick={async () => {
                if (await act("add_tag", { tag: tag.trim() })) setTag("");
              }}
            >
              {t("research.addTag")}
            </Button>
          </div>
        </div>
      </div>

      <div className="space-y-1">
        <Heading>
          {t("research.checklist", {
            name: workspace.checklist.name,
            version: workspace.checklist.version,
          })}
        </Heading>
        <p className="text-[11px] text-muted-foreground">
          {codeText(t, "research.matchedBy", workspace.checklist.matched_by)}
        </p>
        <ul className="space-y-1">
          {workspace.checklist.items.map((state) => (
            <li
              key={state.item.key}
              className={cn(
                "space-y-0.5 rounded border p-1.5",
                !state.done && state.item.required && "border-warn/60",
              )}
            >
              <p className="flex flex-wrap items-start gap-1.5">
                <Passed passed={state.done} />
                <span className="font-medium break-words">{state.item.label}</span>
                {state.item.kind === "automatic" && (
                  <span className="rounded border px-1 text-[10px] text-muted-foreground">
                    {t("research.automatic")}
                  </span>
                )}
                {state.item.required && (
                  <span className="rounded border px-1 text-[10px]">{t("research.required")}</span>
                )}
              </p>
              <p className="text-[11px] break-words text-muted-foreground">
                {[
                  state.detail,
                  state.decided_by,
                  state.decided_at && formatDateTime(state.decided_at, language),
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
              {state.item.kind === "manual" &&
                (state.done ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6"
                    disabled={busy || !why}
                    onClick={() => void withReason("uncheck_item", { item_key: state.item.key })}
                  >
                    {t("research.uncheck")}
                  </Button>
                ) : (
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-6"
                    disabled={busy}
                    onClick={() => void act("check_item", { item_key: state.item.key })}
                  >
                    {t("research.check")}
                  </Button>
                ))}
            </li>
          ))}
        </ul>
      </div>

      <div className="space-y-1">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Heading>{t("research.tasks")}</Heading>
          <Button
            variant="ghost"
            size="sm"
            className="h-6"
            disabled={busy}
            onClick={() => void act("sync_tasks")}
          >
            {t("research.syncTasks")}
          </Button>
        </div>
        {workspace.tasks.length === 0 ? (
          <p className="text-muted-foreground">{t("business.noClaims")}</p>
        ) : (
          <ul className="space-y-1">
            {workspace.tasks.map((task) => (
              <li
                key={task.id}
                className={cn(
                  "space-y-0.5 rounded border p-1.5",
                  task.status !== "open" && "opacity-75",
                )}
              >
                <p className="flex flex-wrap items-center gap-1.5">
                  <span className="break-words">{task.title}</span>
                  <span className="rounded border px-1 text-[10px] text-muted-foreground">
                    {t(TASK_STATUS_KEYS[task.status])}
                  </span>
                </p>
                {task.close_reason && (
                  <p className="text-[11px] break-words text-muted-foreground">
                    {task.close_reason}
                  </p>
                )}
                <div className="flex gap-1">
                  {task.status === "open" ? (
                    <>
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-6"
                        disabled={busy}
                        onClick={() => void act("complete_task", { task_id: task.id })}
                      >
                        {t("research.completeTask")}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6"
                        disabled={busy || !why}
                        onClick={() => void withReason("dismiss_task", { task_id: task.id })}
                      >
                        {t("research.dismissTask")}
                      </Button>
                    </>
                  ) : (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6"
                      disabled={busy}
                      onClick={() => void act("reopen_task", { task_id: task.id })}
                    >
                      {t("research.reopenTask")}
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
        <form
          className="flex gap-1"
          onSubmit={async (event) => {
            event.preventDefault();
            if (taskTitle.trim() && (await act("add_task", { title: taskTitle.trim() }))) {
              setTaskTitle("");
            }
          }}
        >
          <Input
            aria-label={t("research.taskTitle")}
            value={taskTitle}
            maxLength={300}
            onChange={(event) => setTaskTitle(event.target.value)}
            className="h-7 text-xs"
          />
          <Button
            type="submit"
            variant="outline"
            size="sm"
            className="h-7"
            disabled={busy || !taskTitle.trim()}
          >
            {t("research.addTask")}
          </Button>
        </form>
      </div>

      <div className="space-y-1">
        <Heading>{t("research.pins")}</Heading>
        {pins.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">{t("business.pinnedHelp")}</p>
        ) : (
          <ul className="space-y-1">
            {pins.map(({ pin, available }) => (
              <li key={pin.id} className="space-y-0.5 rounded border p-1.5">
                <p className="flex flex-wrap items-center gap-1.5">
                  <span className="text-[10px] text-muted-foreground">
                    {t(EVIDENCE_KIND_KEYS[pin.ref_kind])}
                  </span>
                  {!available && (
                    <span className="rounded border border-warn/60 px-1 text-[10px] text-warn">
                      {t("research.pinUnavailable")}
                    </span>
                  )}
                </p>
                <EvidenceChip
                  businessId={businessId}
                  evidence={{ kind: pin.ref_kind, ref: pin.ref, label: pin.label, url: null }}
                />
                {pin.note && <p className="text-[11px] break-words">{pin.note}</p>}
                <p className="text-[11px] text-muted-foreground">
                  {`${pin.pinned_by} · ${formatDateTime(pin.pinned_at, language)}`}
                </p>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6"
                  disabled={busy || !why}
                  onClick={() => void withReason("unpin", { ref_kind: pin.ref_kind, ref: pin.ref })}
                >
                  {t("research.unpin")}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-1">
        <Heading>{t("research.notes")}</Heading>
        {notes.map((item) => (
          <div key={item.id} className="space-y-0.5 rounded border p-1.5">
            <p className="break-words whitespace-pre-wrap">{item.body}</p>
            <p className="text-[11px] text-muted-foreground">
              {`${item.author} · ${formatDateTime(item.created_at, language)}`}
            </p>
            <Button
              variant="ghost"
              size="sm"
              className="h-6"
              disabled={busy || !why}
              onClick={() => void withReason("delete_note", { note_id: item.id })}
            >
              {t("research.deleteNote")}
            </Button>
          </div>
        ))}
        <form
          className="space-y-1"
          onSubmit={async (event) => {
            event.preventDefault();
            if (note.trim() && (await act("add_note", { note: note.trim() }))) setNote("");
          }}
        >
          <Textarea
            aria-label={t("research.addNote")}
            value={note}
            maxLength={4000}
            onChange={(event) => setNote(event.target.value)}
            className="min-h-10 text-xs"
          />
          <Button
            type="submit"
            variant="outline"
            size="sm"
            className="h-7"
            disabled={busy || !note.trim()}
          >
            {t("research.addNote")}
          </Button>
        </form>
      </div>

      <div className="space-y-1">
        <Heading>{t("research.corrections")}</Heading>
        {workspace.corrections.map((correction, index) => (
          <div key={`${correction.field}:${correction.observation_id}:${index}`}>
            <p className="break-words">
              {t("research.correctionLine", {
                field: t(CORRECTABLE_FIELD_KEYS[correction.field]),
                value: correction.value,
                actor: correction.actor,
                time: formatDateTime(correction.corrected_at, language),
              })}
              {correction.shown && (
                <span className="ml-1 rounded bg-primary/10 px-1 text-[10px] text-primary">
                  {t("research.correctionShown")}
                </span>
              )}
            </p>
            <p className="text-[11px] break-words text-muted-foreground">{correction.reason}</p>
          </div>
        ))}
        <form
          className="flex flex-wrap gap-1"
          onSubmit={async (event) => {
            event.preventDefault();
            if (!value.trim() || !why) return;
            if (await act("correct_value", { field, value: value.trim(), reason: why })) {
              setValue("");
              setReason("");
            }
          }}
        >
          <select
            aria-label={t("research.correctField")}
            value={field}
            onChange={(event) => {
              const next = CORRECTABLE_FIELDS.find((option) => option === event.target.value);
              if (next) setField(next);
            }}
            className={SELECT}
          >
            {CORRECTABLE_FIELDS.map((option) => (
              <option key={option} value={option}>
                {t(CORRECTABLE_FIELD_KEYS[option])}
              </option>
            ))}
          </select>
          <Input
            aria-label={t("research.correctValue")}
            value={value}
            maxLength={500}
            onChange={(event) => setValue(event.target.value)}
            className="h-7 w-48 text-xs"
          />
          <Button
            type="submit"
            variant="outline"
            size="sm"
            className="h-7"
            disabled={busy || !value.trim() || !why}
          >
            {t("research.correct")}
          </Button>
        </form>
      </div>

      <form
        className="space-y-1"
        onSubmit={async (event) => {
          event.preventDefault();
          if (scopes.length > 0 && (await act("request_refresh", { scopes }))) setScopes([]);
        }}
      >
        <Heading>{t("research.refresh")}</Heading>
        <p className="text-[11px] text-muted-foreground">{t("research.refreshHelp")}</p>
        <fieldset className="flex flex-wrap gap-x-3 gap-y-1">
          <legend className="sr-only">{t("research.refresh")}</legend>
          {REFRESH_SCOPES.map((scope) => (
            <label key={scope} className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={scopes.includes(scope)}
                onChange={() =>
                  setScopes((current) =>
                    current.includes(scope)
                      ? current.filter((item) => item !== scope)
                      : [...current, scope],
                  )
                }
              />
              {t(REFRESH_SCOPE_KEYS[scope])}
            </label>
          ))}
        </fieldset>
        <Button
          type="submit"
          variant="outline"
          size="sm"
          className="h-7"
          disabled={busy || scopes.length === 0}
        >
          {t("research.requestRefresh")}
        </Button>
      </form>

      {workspace.decisions.length > 0 && (
        <div className="space-y-1">
          <Heading>{t("research.history")}</Heading>
          <ul className="space-y-1 text-[11px]">
            {workspace.decisions.map((decision) => {
              const statusChange = decision.decision === "research_status";
              return (
                <li key={decision.id}>
                  <p>
                    {t("research.decisionLine", {
                      decision: statusChange
                        ? t("research.status")
                        : decision.decision === "research_assignee"
                          ? t("research.assignee")
                          : decision.decision,
                      from: statusChange
                        ? statusText(decision.previous_value)
                        : (decision.previous_value ?? "—"),
                      to: statusChange
                        ? statusText(decision.new_value)
                        : (decision.new_value ?? "—"),
                      actor: decision.actor,
                      time: formatDateTime(decision.decided_at, language),
                    })}
                  </p>
                  <p className="break-words text-muted-foreground">{decision.reason}</p>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </section>
  );
}
