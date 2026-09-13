import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import type {
  ContactAction,
  ContactChannel,
  ContactDetail,
  ValidationStatus,
} from "@lead/shared-types";
import { ExternalLink } from "lucide-react";
import { toast } from "sonner";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  ACTION_KEYS,
  CONTACT_TYPES,
  CONTACT_TYPE_KEYS,
  FRESHNESS_KEYS,
  LEVEL_KEYS,
  ORIGIN_KEYS,
  RELATIONSHIP_KEYS,
  REVIEW_KEYS,
  ROLE_KEYS,
  STATUS_KEYS,
  STATUS_TONE,
  componentLabel,
  conflictLabel,
  decisionLabel,
  gateLabel,
  methodLabel,
  personReasonLabel,
  signalLabel,
} from "@/features/contacts/labels";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop, toIpcError, type IpcError } from "@/lib/ipc";
import { cn } from "@/lib/utils";

type DetailState = { detail: ContactDetail; error: null } | { detail: null; error: IpcError };

const ACTIONS: readonly ContactAction[] = [
  "validate",
  "confirm",
  "mark_invalid",
  "clear_review",
  "mark_primary",
  "suppress",
];
/** Actions that decide nothing about the channel's truth; every other one needs a reason. */
const WITHOUT_REASON = new Set<ContactAction>(["validate", "mark_primary"]);
const percent = (value: number) => Math.round(value * 100);

function Heading({ children }: { children: ReactNode }) {
  return (
    <h4 className="text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
      {children}
    </h4>
  );
}

/** The exact status of a channel, in words with its stable code beside it. */
export function StatusBadge({ status }: { status: ValidationStatus }) {
  const t = useT();
  return (
    <span className={cn("inline-flex items-center gap-1 rounded border px-1", STATUS_TONE[status])}>
      {t(STATUS_KEYS[status])}
      <span className="font-mono text-[10px] opacity-80">{status}</span>
    </span>
  );
}

function ChannelEntry({ channel }: { channel: ContactChannel }) {
  const t = useT();
  const language = useLanguage();
  return (
    <li
      className={cn("space-y-1 rounded-md border p-2", channel.is_primary && "border-primary/60")}
    >
      <p className="flex flex-wrap items-center gap-1.5">
        <span className="font-mono break-all">{channel.value}</span>
        {channel.is_primary && (
          <span className="rounded bg-primary/10 px-1 text-[10px] text-primary">
            {t("contacts.shown")}
          </span>
        )}
      </p>
      <p className="flex flex-wrap gap-1 text-[11px]">
        <StatusBadge status={channel.validation_status} />
        <span className="rounded border px-1">{t(FRESHNESS_KEYS[channel.freshness])}</span>
        <span className="rounded border px-1">
          {t(ROLE_KEYS[channel.role])}
          {channel.role_or_department ? ` · ${channel.role_or_department}` : ""}
        </span>
        <span className="rounded border px-1">{t(LEVEL_KEYS[channel.level])}</span>
      </p>
      <p className="text-[11px] text-muted-foreground">
        {t("contacts.channelMeta", {
          method: channel.validation_method ? methodLabel(t, channel.validation_method) : "—",
          sources: channel.source_count,
          seen: formatDateTime(channel.last_seen_at, language),
        })}
      </p>
      {channel.suppression && (
        <p className="text-[11px] text-danger">
          {t("contacts.suppressedBecause", {
            reason: channel.suppression.reason,
            time: formatDateTime(channel.suppression.created_at, language),
          })}
        </p>
      )}
      {channel.review && (
        <p className="text-[11px] text-muted-foreground">
          {t("contacts.reviewedLine", {
            decision: decisionLabel(t, channel.review.decision),
            actor: channel.review.actor,
            reason: channel.review.reason,
          })}
        </p>
      )}
      {channel.shared_with > 0 && (
        <p className="text-[11px] text-warn">
          {t("contacts.sharedWith", { n: channel.shared_with })}
        </p>
      )}
      {channel.signals.length > 0 && (
        <ul className="flex flex-wrap gap-1 text-[10px]">
          {channel.signals.map((signal) => (
            <li
              key={`${signal.code}:${signal.value ?? ""}`}
              className="rounded border px-1 text-muted-foreground"
            >
              {signalLabel(t, signal.code)}
              {signal.value ? `: ${signal.value}` : ""}
            </li>
          ))}
        </ul>
      )}
      <details className="text-[11px]">
        <summary className="cursor-pointer text-muted-foreground">
          {t("contacts.sources", { n: channel.sources.length })}
        </summary>
        <ul className="mt-1 space-y-1">
          {channel.sources.map((source) => (
            <li key={source.contact_point_id} className="break-words text-muted-foreground">
              {source.provenance.source_url ? (
                <a
                  href={source.provenance.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 underline underline-offset-2"
                >
                  {source.provenance.connector_name}
                  <ExternalLink aria-hidden="true" className="size-3 shrink-0" />
                </a>
              ) : (
                source.provenance.connector_name
              )}
              {` · ${t(ORIGIN_KEYS[source.origin])} · ${source.provenance.extractor} ${source.provenance.extractor_version} · ${formatDateTime(source.observed_at, language)}`}
              {source.published_context && (
                <span className="block">{source.published_context}</span>
              )}
              {!source.current && (
                <span className="block text-warn">{t("contacts.notCurrent")}</span>
              )}
            </li>
          ))}
        </ul>
      </details>
    </li>
  );
}

/**
 * The contacts of a business (phase 09): every channel it publishes or the operator imported, with
 * its exact status, where and when it was seen, conflicts, published people, the contactability
 * subscore and the actions a person can take. Hypotheses are listed apart and never read as a
 * contact. Nothing here calls, emails or submits anything.
 */
export function ContactPanel({ businessId }: { businessId: string }) {
  const t = useT();
  const language = useLanguage();
  const [state, setState] = useState<DetailState | null>(null);
  const [key, setKey] = useState("");
  const [action, setAction] = useState<ContactAction | "">("");
  const [reason, setReason] = useState("");
  const [lookup, setLookup] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let current = true;
    desktop.getContacts({ business_id: businessId }).then(
      (detail) => current && setState({ detail, error: null }),
      (cause: unknown) =>
        current && setState({ detail: null, error: toIpcError(cause, "get_contacts") }),
    );
    return () => {
      current = false;
    };
  }, [businessId]);

  if (state === null) return <StateBlock variant="loading" title={t("state.loading")} />;
  if (state.error !== null) {
    return (
      <StateBlock variant="error" title={t("contacts.loadErrorTitle")} body={state.error.message} />
    );
  }

  const { detail } = state;
  const merged = detail.business_id !== detail.root_business_id;
  const chosen = [...detail.channels, ...detail.hypotheses].find((channel) => channel.key === key);
  // a hypothesis is never confirmed nor shown; only a phone or an email is suppressed on its own
  const available = ACTIONS.filter(
    (value) =>
      !(chosen?.origin === "hypothesis" && (value === "confirm" || value === "mark_primary")) &&
      (value !== "suppress" ||
        chosen?.contact_type === "phone" ||
        chosen?.contact_type === "email"),
  );
  const needsReason = action !== "" && !WITHOUT_REASON.has(action);
  const canSubmit =
    !busy &&
    chosen !== undefined &&
    action !== "" &&
    available.includes(action) &&
    (!needsReason || reason.trim() !== "");
  const { score, gates } = detail.contactability;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    try {
      const updated = await desktop.actOnContact({
        business_id: detail.root_business_id,
        key,
        action,
        reason: reason.trim() || null,
        check_mail_domains: action === "validate" && lookup,
      });
      setState({ detail: updated, error: null });
      setAction("");
      setReason("");
      toast.success(t("contacts.actionDone"));
    } catch (cause) {
      toast.error(t("contacts.actionFailed"), {
        description: toIpcError(cause, "act_on_contact").message,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-label={t("contacts.title")} className="space-y-3 text-xs">
      <Heading>{t("contacts.title")}</Heading>
      {merged && <InlineNotice variant="partial">{t("contacts.belongsTo")}</InlineNotice>}
      {detail.business_suppression && (
        <InlineNotice variant="partial">
          {t("contacts.businessSuppressed", { reason: detail.business_suppression.reason })}
        </InlineNotice>
      )}

      <div className="space-y-1 rounded-md border p-2">
        <p>
          <span className="font-medium">{t("contacts.contactability")}</span>{" "}
          {score === null
            ? t("contacts.noScore", { gates: gates.map((gate) => gateLabel(t, gate)).join(", ") })
            : t("contacts.score", { score })}
        </p>
        <p className="text-[11px] text-muted-foreground">{t("contacts.notLeadScore")}</p>
        {score !== null && gates.length > 0 && (
          <p className="text-[11px] text-warn">
            {gates.map((gate) => gateLabel(t, gate)).join(", ")}
          </p>
        )}
        <ul className="space-y-0.5 text-[11px]">
          {detail.contactability.components.map((component) => (
            <li key={component.code}>
              {t("contacts.componentLine", {
                component: componentLabel(t, component.code),
                value: percent(component.value),
                weight: component.weight,
                detail: component.detail ?? "—",
              })}
            </li>
          ))}
        </ul>
        <details className="text-[11px] text-muted-foreground">
          <summary className="cursor-pointer">{t("contacts.formula")}</summary>
          <p className="mt-1 break-words">{detail.contactability.formula}</p>
        </details>
      </div>

      {detail.conflicts.length > 0 && (
        <div className="space-y-1">
          <Heading>{t("contacts.conflicts")}</Heading>
          <ul className="space-y-0.5">
            {detail.conflicts.map((conflict, index) => (
              <li
                key={`${conflict.code}-${index}`}
                className="rounded border border-warn/60 px-1.5 py-0.5 break-words text-warn"
              >
                {conflictLabel(t, conflict.code)}: {conflict.values.join(" · ")}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="space-y-1.5">
        <Heading>{t("contacts.channels", { n: detail.channels.length })}</Heading>
        {detail.channels.length === 0 && (
          <p className="text-muted-foreground">{t("contacts.noChannels")}</p>
        )}
        {CONTACT_TYPES.map((type) => {
          const entries = detail.channels.filter((channel) => channel.contact_type === type);
          return entries.length === 0 ? null : (
            <div key={type} className="space-y-1">
              <p className="font-medium">{t(CONTACT_TYPE_KEYS[type])}</p>
              <ul className="space-y-1.5">
                {entries.map((channel) => (
                  <ChannelEntry key={channel.key} channel={channel} />
                ))}
              </ul>
            </div>
          );
        })}
      </div>

      {detail.hypotheses.length > 0 && (
        <div className="space-y-1.5">
          <Heading>{t("contacts.hypotheses", { n: detail.hypotheses.length })}</Heading>
          <InlineNotice variant="partial">{t("contacts.hypothesesNotice")}</InlineNotice>
          <ul className="space-y-1.5">
            {detail.hypotheses.map((channel) => (
              <ChannelEntry key={channel.key} channel={channel} />
            ))}
          </ul>
        </div>
      )}

      {detail.decision_makers.length > 0 && (
        <div className="space-y-1.5">
          <Heading>{t("contacts.people", { n: detail.decision_makers.length })}</Heading>
          <p className="text-[11px] text-muted-foreground">{t("contacts.peopleHint")}</p>
          <ul className="space-y-1.5">
            {detail.decision_makers.map((view) => {
              const person = view.decision_maker;
              return (
                <li key={person.id} className="space-y-0.5 rounded-md border p-2">
                  <p className="font-medium">{person.full_name_raw}</p>
                  <p className="text-[11px]">
                    {[
                      person.role_raw,
                      t(RELATIONSHIP_KEYS[person.relationship]),
                      t(REVIEW_KEYS[person.review_status]),
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                  {view.reasons.length > 0 && (
                    <p className="text-[11px] text-warn">
                      {view.reasons.map((code) => personReasonLabel(t, code)).join(", ")}
                    </p>
                  )}
                  {person.published_context && (
                    <blockquote className="border-l-2 pl-2 break-words text-muted-foreground">
                      {person.published_context}
                    </blockquote>
                  )}
                  <p className="text-[11px] break-words text-muted-foreground">
                    {view.provenance.source_url ?? view.provenance.connector_name}
                    {` · ${formatDateTime(person.observed_at, language)}`}
                  </p>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {detail.decisions.length > 0 && (
        <div className="space-y-1">
          <Heading>{t("contacts.decisions")}</Heading>
          <ul className="space-y-1">
            {detail.decisions.map((decision) => (
              <li key={decision.id}>
                <p>
                  {t("contacts.decisionLine", {
                    decision: decisionLabel(t, decision.decision),
                    actor: decision.actor,
                    time: formatDateTime(decision.decided_at, language),
                  })}
                </p>
                <p className="text-[11px] break-words text-muted-foreground">
                  {decision.new_value} · {decision.reason}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {!merged && (detail.channels.length > 0 || detail.hypotheses.length > 0) && (
        <form className="space-y-1.5" onSubmit={(event) => void submit(event)}>
          <Heading>{t("contacts.act")}</Heading>
          <p className="text-[11px] text-muted-foreground">{t("contacts.actHint")}</p>
          <Label htmlFor="contact-channel" className="text-xs">
            {t("contacts.actChannel")}
          </Label>
          <select
            id="contact-channel"
            value={key}
            onChange={(event) => setKey(event.target.value)}
            className="h-7 w-full rounded-md border bg-transparent px-2 text-xs"
          >
            <option value="" disabled>
              {t("contacts.actChooseChannel")}
            </option>
            {detail.channels.map((channel) => (
              <option key={channel.key} value={channel.key}>
                {`${t(CONTACT_TYPE_KEYS[channel.contact_type])}: ${channel.value}`}
              </option>
            ))}
            {detail.hypotheses.map((channel) => (
              <option key={channel.key} value={channel.key}>
                {t("contacts.hypothesisOption", { value: channel.value })}
              </option>
            ))}
          </select>
          <Label htmlFor="contact-action" className="text-xs">
            {t("contacts.actAction")}
          </Label>
          <select
            id="contact-action"
            value={action}
            onChange={(event) => setAction(event.target.value as ContactAction | "")}
            className="h-7 w-full rounded-md border bg-transparent px-2 text-xs"
          >
            <option value="" disabled>
              {t("contacts.actChooseAction")}
            </option>
            {available.map((value) => (
              <option key={value} value={value}>
                {t(ACTION_KEYS[value])}
              </option>
            ))}
          </select>
          {action === "validate" && chosen?.contact_type === "email" && (
            <div className="flex items-center gap-1.5">
              <Checkbox
                id="contact-lookup"
                checked={lookup}
                onCheckedChange={(checked) => setLookup(checked === true)}
              />
              <Label htmlFor="contact-lookup" className="text-xs">
                {t("contacts.lookupDomain")}
              </Label>
            </div>
          )}
          <Label htmlFor="contact-reason" className="text-xs">
            {t(needsReason ? "contacts.reason" : "contacts.reasonOptional")}
          </Label>
          <Textarea
            id="contact-reason"
            value={reason}
            maxLength={2000}
            onChange={(event) => setReason(event.target.value)}
            className="min-h-12 text-xs"
          />
          <Button type="submit" size="sm" className="h-7" disabled={!canSubmit}>
            {t("contacts.actSubmit")}
          </Button>
        </form>
      )}
    </section>
  );
}
