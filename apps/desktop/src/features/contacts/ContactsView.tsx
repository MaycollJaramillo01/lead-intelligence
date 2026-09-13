import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  ContactFreshness,
  ContactListItem,
  ContactRole,
  ContactRun,
  ContactType,
  DecisionMakerDecision,
  DecisionMakerItem,
  RunControlAction,
  SuppressionExport,
  SuppressionImportResult,
  SuppressionKind,
  ValidationStatus,
} from "@lead/shared-types";
import type { ColumnDef } from "@tanstack/react-table";
import { Ban, CirclePause, CirclePlay, Download, Play, Upload } from "lucide-react";
import { toast } from "sonner";
import { DataTable } from "@/components/data/DataTable";
import { DateTime, Mono, TestDataBadge, Text } from "@/components/data/cells";
import { filterFieldsFrom, type FilterCondition } from "@/components/data/filters";
import { PagedFooter } from "@/components/data/PagedFooter";
import { JobProgress, type JobState } from "@/components/domain/JobProgress";
import { StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { Textarea } from "@/components/ui/textarea";
import { ContactPanel, StatusBadge } from "@/features/contacts/ContactPanel";
import {
  CONTACT_TYPES,
  CONTACT_TYPE_KEYS,
  FRESHNESS,
  FRESHNESS_KEYS,
  RELATIONSHIP_KEYS,
  ROLES,
  ROLE_KEYS,
  STATUS_KEYS,
  SUPPRESSION_KINDS,
  SUPPRESSION_KIND_KEYS,
  VALIDATION_STATUSES,
  personReasonLabel,
} from "@/features/contacts/labels";
import { formatDateTime, useLanguage, useT, type Translator } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";
import { useLoaded } from "@/lib/useLoaded";
import { usePreferences } from "@/stores/preferences";

const POLL_MS = 1000;
const LISTED = 500;
const ACTIVE: ContactRun["status"][] = ["queued", "running"];
const CONTROLS: Record<RunControlAction, { icon: typeof CirclePause; states: JobState[] }> = {
  pause: { icon: CirclePause, states: ["running"] },
  resume: { icon: CirclePlay, states: ["paused"] },
  cancel: { icon: Ban, states: ["queued", "running", "paused"] },
};
const TABS = ["channels", "review", "suppressions"] as const;
type Tab = (typeof TABS)[number];

const rowId = (row: ContactListItem) => `${row.business_id}|${row.key}`;

function buildColumns(t: Translator): ColumnDef<ContactListItem>[] {
  return [
    {
      id: "business",
      accessorKey: "business_name",
      header: t("contacts.column.business"),
      size: 200,
      cell: ({ getValue }) => <Text value={getValue<string>()} />,
    },
    {
      id: "type",
      accessorFn: (row) => t(CONTACT_TYPE_KEYS[row.contact_type]),
      header: t("contacts.column.type"),
      size: 130,
      cell: ({ getValue }) => <Text value={getValue<string>()} />,
    },
    {
      id: "value",
      accessorKey: "value",
      header: t("contacts.column.value"),
      size: 240,
      cell: ({ getValue }) => <Mono value={getValue<string>()} />,
    },
    {
      id: "status",
      accessorFn: (row) => t(STATUS_KEYS[row.validation_status]),
      header: t("contacts.column.status"),
      size: 190,
      cell: ({ row }) => <StatusBadge status={row.original.validation_status} />,
    },
    {
      id: "role",
      accessorFn: (row) => t(ROLE_KEYS[row.role]),
      header: t("contacts.column.role"),
      size: 140,
      cell: ({ getValue }) => <Text value={getValue<string>()} />,
    },
    {
      id: "freshness",
      accessorFn: (row) => t(FRESHNESS_KEYS[row.freshness]),
      header: t("contacts.column.freshness"),
      size: 100,
      cell: ({ getValue }) => <Text value={getValue<string>()} />,
    },
    {
      id: "sources",
      accessorKey: "source_count",
      header: t("contacts.column.sources"),
      size: 80,
      cell: ({ getValue }) => <Mono value={getValue<number>()} />,
    },
    {
      id: "seen",
      accessorKey: "last_seen_at",
      header: t("contacts.column.seen"),
      size: 170,
      cell: ({ getValue }) => <DateTime value={getValue<string>()} />,
    },
    {
      id: "shown",
      accessorFn: (row) => (row.is_primary ? t("contacts.shown") : ""),
      header: t("contacts.column.shown"),
      size: 80,
      cell: ({ getValue }) => <Text value={getValue<string>() || null} />,
    },
  ];
}

function FilterSelect<T extends string>({
  label,
  value,
  options,
  labelOf,
  onChange,
}: {
  label: string;
  value: T | "";
  options: readonly T[];
  labelOf: (value: T) => string;
  onChange: (value: T | "") => void;
}) {
  const t = useT();
  return (
    <label className="flex items-center gap-1.5 text-xs">
      {label}
      <select
        value={value}
        onChange={(event) => onChange(event.target.value as T | "")}
        className="h-7 rounded-md border bg-transparent px-2 text-xs"
      >
        <option value="">{t("contacts.any")}</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {labelOf(option)}
          </option>
        ))}
      </select>
    </label>
  );
}

function ChannelsTab() {
  const t = useT();
  const inspectorSize = usePreferences((state) => state.inspectorSize);
  const columns = useMemo(() => buildColumns(t), [t]);
  const filterFields = useMemo(() => filterFieldsFrom(columns), [columns]);
  const [conditions, setConditions] = useState<FilterCondition[]>([]);
  const [contactType, setContactType] = useState<ContactType | "">("");
  const [status, setStatus] = useState<ValidationStatus | "">("");
  const [role, setRole] = useState<ContactRole | "">("");
  const [freshness, setFreshness] = useState<ContactFreshness | "">("");
  const [hypotheses, setHypotheses] = useState(false);
  const [suppressed, setSuppressed] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  const fetchContacts = useCallback(
    () =>
      desktop.listContacts({
        project_id: null,
        contact_type: contactType || null,
        validation_status: status || null,
        role: role || null,
        freshness: freshness || null,
        include_hypotheses: hypotheses,
        include_suppressed: suppressed,
        limit: LISTED,
        cursor: null,
      }),
    [contactType, status, role, freshness, hypotheses, suppressed],
  );
  const list = useLoaded(fetchContacts, "list_contacts");
  const items = list.data?.items ?? [];
  const chosen = items.find((item) => rowId(item) === selected) ?? null;
  const { reload } = list;

  const emptyState = list.error ? (
    <StateBlock
      variant={list.error.code === "desktop_unavailable" ? "offline" : "error"}
      title={t("contacts.errorTitle")}
      body={list.error.message}
      action={
        <Button variant="outline" size="sm" onClick={() => void reload()}>
          {t("common.retry")}
        </Button>
      }
    />
  ) : (
    <StateBlock variant="empty" title={t("contacts.emptyTitle")} body={t("contacts.emptyBody")} />
  );

  return (
    <ResizablePanelGroup orientation="horizontal" className="min-h-0 flex-1">
      <ResizablePanel defaultSize={`${100 - inspectorSize}%`} minSize="40%">
        <div className="flex h-full min-h-0 flex-col">
          <div className="flex shrink-0 flex-wrap items-center gap-2 border-b px-2 py-1.5">
            <FilterSelect
              label={t("contacts.filterChannel")}
              value={contactType}
              options={CONTACT_TYPES}
              labelOf={(value) => t(CONTACT_TYPE_KEYS[value])}
              onChange={setContactType}
            />
            <FilterSelect
              label={t("contacts.filterStatus")}
              value={status}
              options={VALIDATION_STATUSES}
              labelOf={(value) => t(STATUS_KEYS[value])}
              onChange={setStatus}
            />
            <FilterSelect
              label={t("contacts.filterRole")}
              value={role}
              options={ROLES}
              labelOf={(value) => t(ROLE_KEYS[value])}
              onChange={setRole}
            />
            <FilterSelect
              label={t("contacts.filterFreshness")}
              value={freshness}
              options={FRESHNESS}
              labelOf={(value) => t(FRESHNESS_KEYS[value])}
              onChange={setFreshness}
            />
            {(
              [
                ["contacts-hypotheses", hypotheses, setHypotheses, "contacts.includeHypotheses"],
                ["contacts-suppressed", suppressed, setSuppressed, "contacts.includeSuppressed"],
              ] as const
            ).map(([id, checked, change, label]) => (
              <div key={id} className="flex items-center gap-1.5">
                <Checkbox
                  id={id}
                  checked={checked}
                  onCheckedChange={(value) => change(value === true)}
                />
                <Label htmlFor={id} className="text-xs">
                  {t(label)}
                </Label>
              </div>
            ))}
          </div>
          <div className="min-h-0 flex-1">
            <DataTable
              tableId="contacts"
              columns={columns}
              data={items}
              getRowId={rowId}
              filterFields={filterFields}
              conditions={conditions}
              onConditionsChange={setConditions}
              pinnedLeft={["business"]}
              activeRowId={selected}
              onActivateRow={setSelected}
              emptyState={emptyState}
            />
          </div>
          <PagedFooter
            loaded={items.length}
            total={list.data?.total ?? 0}
            hasMore={false}
            loading={false}
            onLoadMore={() => undefined}
          />
        </div>
      </ResizablePanel>
      <ResizableHandle withHandle />
      <ResizablePanel defaultSize={`${inspectorSize}%`} minSize="20%" maxSize="60%">
        <aside
          aria-label={t("common.inspector")}
          className="h-full min-h-0 overflow-auto border-l bg-card p-3"
        >
          {chosen === null ? (
            <StateBlock
              variant="empty"
              title={t("contacts.inspectorEmptyTitle")}
              body={t("contacts.inspectorEmptyBody")}
            />
          ) : (
            <ContactPanel key={chosen.business_id} businessId={chosen.business_id} />
          )}
        </aside>
      </ResizablePanel>
    </ResizablePanelGroup>
  );
}

function ReviewTab() {
  const t = useT();
  const language = useLanguage();
  const fetchQueue = useCallback(
    () =>
      desktop.listDecisionMakers({
        project_id: null,
        review_status: "ambiguous",
        limit: LISTED,
        cursor: null,
      }),
    [],
  );
  const queue = useLoaded(fetchQueue, "list_decision_makers");
  const { reload } = queue;
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const items = queue.data?.items ?? [];

  const decide = async (item: DecisionMakerItem, decision: DecisionMakerDecision) => {
    const reason = (reasons[item.decision_maker.id] ?? "").trim();
    if (!reason) return;
    setBusy(true);
    try {
      await desktop.reviewDecisionMaker({
        decision_maker_id: item.decision_maker.id,
        decision,
        reason,
      });
      toast.success(t("contacts.reviewDone"));
      await reload();
    } catch (cause) {
      toast.error(t("contacts.reviewFailed"), {
        description: toIpcError(cause, "review_decision_maker").message,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-0 flex-1 space-y-2 overflow-auto p-2 text-xs">
      <p className="text-[11px] text-muted-foreground">{t("contacts.reviewExplain")}</p>
      {queue.error ? (
        <StateBlock variant="error" title={t("contacts.errorTitle")} body={queue.error.message} />
      ) : queue.data === null ? (
        <StateBlock variant="loading" title={t("common.loading")} />
      ) : items.length === 0 ? (
        <StateBlock
          variant="empty"
          title={t("contacts.reviewEmptyTitle")}
          body={t("contacts.reviewEmptyBody")}
        />
      ) : (
        <ul className="space-y-2">
          {items.map((item) => {
            const person = item.decision_maker;
            const reason = reasons[person.id] ?? "";
            return (
              <li key={person.id} className="space-y-1 rounded-md border p-2">
                <p className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{person.full_name_raw}</span>
                  <span className="text-muted-foreground">{item.business_name}</span>
                  {item.is_synthetic && <TestDataBadge />}
                </p>
                <p className="text-[11px]">
                  {[person.role_raw, t(RELATIONSHIP_KEYS[person.relationship])]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
                <p className="text-[11px] text-warn">
                  {item.reasons.map((code) => personReasonLabel(t, code)).join(", ")}
                </p>
                {person.published_context && (
                  <blockquote className="border-l-2 pl-2 break-words text-muted-foreground">
                    {person.published_context}
                  </blockquote>
                )}
                <p className="text-[11px] break-words text-muted-foreground">
                  {item.provenance.source_url ?? item.provenance.connector_name}
                  {` · ${formatDateTime(person.observed_at, language)}`}
                </p>
                <Label htmlFor={`person-${person.id}`} className="text-xs">
                  {t("contacts.reason")}
                </Label>
                <Textarea
                  id={`person-${person.id}`}
                  value={reason}
                  maxLength={2000}
                  onChange={(event) =>
                    setReasons((current) => ({ ...current, [person.id]: event.target.value }))
                  }
                  className="min-h-10 text-xs"
                />
                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    className="h-7"
                    disabled={busy || !reason.trim()}
                    onClick={() => void decide(item, "confirm")}
                  >
                    {t("contacts.personConfirm")}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7"
                    disabled={busy || !reason.trim()}
                    onClick={() => void decide(item, "reject")}
                  >
                    {t("contacts.personReject")}
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function SuppressionsTab() {
  const t = useT();
  const language = useLanguage();
  const [includeLifted, setIncludeLifted] = useState(false);
  const fetchList = useCallback(
    () =>
      desktop.listSuppressions({
        kind: null,
        include_lifted: includeLifted,
        limit: LISTED,
        cursor: null,
      }),
    [includeLifted],
  );
  const list = useLoaded(fetchList, "list_suppressions");
  const { reload } = list;
  const [kind, setKind] = useState<SuppressionKind>("email");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const [liftReasons, setLiftReasons] = useState<Record<string, string>>({});
  const [imported, setImported] = useState<SuppressionImportResult | null>(null);
  const [exported, setExported] = useState<SuppressionExport | null>(null);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const entries = list.data?.items ?? [];

  const perform = async (command: string, work: () => Promise<void>) => {
    setBusy(true);
    try {
      await work();
      await reload();
    } catch (cause) {
      toast.error(t("contacts.suppressionFailed"), {
        description: toIpcError(cause, command).message,
      });
    } finally {
      setBusy(false);
    }
  };

  const add = () =>
    perform("change_suppression", async () => {
      await desktop.changeSuppression({
        suppression_id: null,
        kind,
        value: value.trim(),
        reason: reason.trim(),
      });
      setValue("");
      setReason("");
      toast.success(t("contacts.suppressionAdded"));
    });
  const lift = (id: string) =>
    perform("change_suppression", async () => {
      await desktop.changeSuppression({
        suppression_id: id,
        kind: null,
        value: null,
        reason: (liftReasons[id] ?? "").trim(),
      });
      toast.success(t("contacts.suppressionLifted"));
    });
  const importFile = (file: File) =>
    perform("import_suppressions", async () => {
      const content = await file.text();
      setImported(await desktop.importSuppressions({ filename: file.name, content }));
    });
  const exportList = () =>
    perform("export_suppressions", async () => {
      setExported(await desktop.exportSuppressions({ include_lifted: includeLifted }));
    });

  return (
    <div className="min-h-0 flex-1 space-y-3 overflow-auto p-2 text-xs">
      <p className="text-[11px] text-muted-foreground">{t("contacts.suppressionsExplain")}</p>
      <form
        className="grid gap-2 rounded-md border p-2 sm:grid-cols-[max-content_minmax(0,1fr)]"
        onSubmit={(event) => {
          event.preventDefault();
          void add();
        }}
      >
        <Label htmlFor="suppression-kind" className="text-xs">
          {t("contacts.suppressionKind")}
        </Label>
        <select
          id="suppression-kind"
          value={kind}
          onChange={(event) => setKind(event.target.value as SuppressionKind)}
          className="h-7 rounded-md border bg-transparent px-2 text-xs"
        >
          {SUPPRESSION_KINDS.map((option) => (
            <option key={option} value={option}>
              {t(SUPPRESSION_KIND_KEYS[option])}
            </option>
          ))}
        </select>
        <Label htmlFor="suppression-value" className="text-xs">
          {t("contacts.suppressionValue")}
        </Label>
        <input
          id="suppression-value"
          value={value}
          maxLength={320}
          onChange={(event) => setValue(event.target.value)}
          className="h-7 rounded-md border bg-transparent px-2 font-mono text-xs"
        />
        <Label htmlFor="suppression-reason" className="text-xs">
          {t("contacts.reason")}
        </Label>
        <Textarea
          id="suppression-reason"
          value={reason}
          maxLength={2000}
          onChange={(event) => setReason(event.target.value)}
          className="min-h-10 text-xs"
        />
        <div className="flex flex-wrap items-center gap-2 sm:col-span-2">
          <Button
            type="submit"
            size="sm"
            className="h-7"
            disabled={busy || !value.trim() || !reason.trim()}
          >
            {t("contacts.suppressionAdd")}
          </Button>
          <input
            ref={fileInput}
            type="file"
            accept=".csv,text/csv"
            className="sr-only"
            aria-label={t("contacts.import")}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void importFile(file);
            }}
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7"
            disabled={busy}
            onClick={() => fileInput.current?.click()}
          >
            <Upload aria-hidden="true" />
            {t("contacts.import")}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7"
            disabled={busy}
            onClick={() => void exportList()}
          >
            <Download aria-hidden="true" />
            {t("contacts.export")}
          </Button>
          <div className="flex items-center gap-1.5">
            <Checkbox
              id="suppressions-lifted"
              checked={includeLifted}
              onCheckedChange={(checked) => setIncludeLifted(checked === true)}
            />
            <Label htmlFor="suppressions-lifted" className="text-xs">
              {t("contacts.includeLifted")}
            </Label>
          </div>
        </div>
      </form>

      {imported && (
        <div className="space-y-1 rounded-md border p-2">
          <p>
            {t("contacts.importResult", {
              rows: imported.rows,
              added: imported.added,
              already: imported.already_suppressed,
              problems: imported.problems.length,
            })}
          </p>
          <ul className="space-y-0.5 text-[11px] text-warn">
            {imported.problems.map((problem) => (
              <li key={`${problem.row}-${problem.code}`}>
                {t("contacts.importProblem", { row: problem.row, message: problem.message })}
              </li>
            ))}
          </ul>
        </div>
      )}
      {exported && (
        <div className="space-y-1 rounded-md border p-2">
          <p>{t("contacts.exported", { n: exported.rows, file: exported.filename })}</p>
          <a
            href={`data:text/csv;charset=utf-8,${encodeURIComponent(exported.content)}`}
            download={exported.filename}
            className="inline-flex items-center gap-1 text-primary underline underline-offset-2"
          >
            <Download aria-hidden="true" className="size-3" />
            {t("contacts.saveFile")}
          </a>
          <Textarea
            readOnly
            value={exported.content}
            aria-label={t("contacts.exportContent")}
            className="min-h-24 font-mono text-[11px]"
          />
        </div>
      )}

      {list.error ? (
        <StateBlock variant="error" title={t("contacts.errorTitle")} body={list.error.message} />
      ) : list.data === null ? (
        <StateBlock variant="loading" title={t("common.loading")} />
      ) : entries.length === 0 ? (
        <StateBlock
          variant="empty"
          title={t("contacts.suppressionsEmptyTitle")}
          body={t("contacts.suppressionsEmptyBody")}
        />
      ) : (
        <ul className="space-y-1.5">
          {entries.map((entry) => {
            const liftReason = liftReasons[entry.id] ?? "";
            return (
              <li key={entry.id} className="space-y-1 rounded-md border p-2">
                <p className="flex flex-wrap items-center gap-2">
                  <span className="rounded border px-1">
                    {t(SUPPRESSION_KIND_KEYS[entry.kind])}
                  </span>
                  <span className="font-mono break-all">{entry.value}</span>
                </p>
                <p className="text-[11px] break-words text-muted-foreground">
                  {t("contacts.suppressionLine", {
                    reason: entry.reason,
                    source: entry.source,
                    actor: entry.actor,
                    time: formatDateTime(entry.created_at, language),
                  })}
                </p>
                {entry.lifted_at ? (
                  <p className="text-[11px] text-muted-foreground">
                    {t("contacts.liftedLine", {
                      actor: entry.lifted_by ?? "",
                      reason: entry.lift_reason ?? "",
                      time: formatDateTime(entry.lifted_at, language),
                    })}
                  </p>
                ) : (
                  <div className="flex flex-wrap items-end gap-2">
                    <div className="min-w-48 flex-1">
                      <Label htmlFor={`lift-${entry.id}`} className="text-[11px]">
                        {t("contacts.reason")}
                      </Label>
                      <input
                        id={`lift-${entry.id}`}
                        value={liftReason}
                        maxLength={2000}
                        onChange={(event) =>
                          setLiftReasons((current) => ({
                            ...current,
                            [entry.id]: event.target.value,
                          }))
                        }
                        className="h-7 w-full rounded-md border bg-transparent px-2 text-xs"
                      />
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7"
                      disabled={busy || !liftReason.trim()}
                      onClick={() => void lift(entry.id)}
                    >
                      {t("contacts.suppressionLift")}
                    </Button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/**
 * Contact intelligence (phase 09): the channels of every business with their exact status, the
 * queue of published people waiting for a person's review, and the local do-not-contact list.
 * Checking contacts reads what sources and crawls stored; mail domains are looked up only when
 * asked, and nothing here ever calls, emails or submits a form.
 */
export function ContactsView() {
  const t = useT();
  const language = useLanguage();
  const [tab, setTab] = useState<Tab>("channels");
  const [lookup, setLookup] = useState(false);
  const [busy, setBusy] = useState(false);

  const fetchRuns = useCallback(
    () => desktop.listContactRuns({ project_id: null, limit: 5, cursor: null }),
    [],
  );
  const runList = useLoaded(fetchRuns, "list_contact_runs");
  const runs = runList.data?.items ?? [];
  const latest = runs[0] ?? null;
  const active = runs.some((run) => ACTIVE.includes(run.status));
  const { reload: reloadRuns } = runList;

  // polled only while a check runs: the worker never pushes, and an idle screen asks nothing
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => void reloadRuns(), POLL_MS);
    return () => clearInterval(timer);
  }, [active, reloadRuns]);

  const start = async () => {
    setBusy(true);
    try {
      const page = await desktop.startContacts({ project_id: null, check_mail_domains: lookup });
      toast.success(t("contacts.started", { n: page.items.length }));
      await reloadRuns();
    } catch (cause) {
      toast.error(t("contacts.startFailed"), {
        description: toIpcError(cause, "start_contacts").message,
      });
    } finally {
      setBusy(false);
    }
  };

  const control = async (jobId: string, action: RunControlAction) => {
    setBusy(true);
    try {
      await desktop.controlContacts({ job_id: jobId, action });
      await reloadRuns();
    } catch (cause) {
      toast.error(t("discovery.controlFailed"), {
        description: toIpcError(cause, "control_contacts").message,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 space-y-2 border-b p-2">
        <div className="flex flex-wrap items-center gap-2">
          <div
            role="group"
            aria-label={t("contacts.tabs")}
            className="flex items-center gap-0.5 rounded-md border p-0.5"
          >
            {TABS.map((value) => (
              <Button
                key={value}
                variant={tab === value ? "outline" : "ghost"}
                size="sm"
                className="h-6"
                aria-pressed={tab === value}
                onClick={() => setTab(value)}
              >
                {t(`contacts.tab.${value}`)}
              </Button>
            ))}
          </div>
          <Button size="sm" className="h-7" disabled={busy || active} onClick={() => void start()}>
            <Play aria-hidden="true" />
            {t("contacts.check")}
          </Button>
          <div className="flex items-center gap-1.5">
            <Checkbox
              id="contacts-lookup"
              checked={lookup}
              onCheckedChange={(checked) => setLookup(checked === true)}
            />
            <Label htmlFor="contacts-lookup" className="text-xs">
              {t("contacts.lookupDomains")}
            </Label>
          </div>
          <p className="min-w-0 flex-1 text-[11px] text-muted-foreground">
            {t("contacts.explain")}
          </p>
        </div>
        {latest && (
          <section
            aria-label={t("contacts.runLabel")}
            className="space-y-1.5 rounded-md border p-2"
          >
            <JobProgress
              job={{
                id: latest.job_id,
                name: t("contacts.runName", {
                  project: latest.project_name,
                  time: formatDateTime(latest.requested_at, language),
                }),
                state: latest.status,
                done: latest.progress_done,
                total: latest.progress_total,
                errors: latest.events.filter((event) => event.level === "error").length,
              }}
            />
            <div className="flex flex-wrap items-center gap-2">
              {(Object.keys(CONTROLS) as RunControlAction[]).map((action) => {
                const { icon: Icon, states } = CONTROLS[action];
                if (!states.includes(latest.status)) return null;
                return (
                  <Button
                    key={action}
                    variant="outline"
                    size="sm"
                    className="h-7"
                    disabled={busy}
                    onClick={() => void control(latest.job_id, action)}
                  >
                    <Icon aria-hidden="true" />
                    {t(`discovery.control.${action}`)}
                  </Button>
                );
              })}
              <span className="text-[11px] text-muted-foreground">
                {t("jobs.contactsDetail", {
                  channels: latest.stats.channels,
                  likely: latest.stats.likely_valid,
                  invalid: latest.stats.invalid,
                  suppressed: latest.stats.suppressed,
                  hypotheses: latest.stats.hypotheses,
                })}
              </span>
            </div>
          </section>
        )}
      </div>
      {tab === "channels" ? (
        <ChannelsTab />
      ) : tab === "review" ? (
        <ReviewTab />
      ) : (
        <SuppressionsTab />
      )}
    </div>
  );
}
