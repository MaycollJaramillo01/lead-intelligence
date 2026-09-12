import { useEffect, useState, type ReactNode } from "react";
import type { BusinessDetail, MatchAction, MatchDetail, Provenance } from "@lead/shared-types";
import { toast } from "sonner";
import { TestDataBadge, Text } from "@/components/data/cells";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ProvenanceLine } from "@/features/businesses/BusinessInspector";
import {
  ACTION_KEYS,
  AGREEMENT_KEYS,
  MATCH_ACTIONS,
  OUTCOME_KEYS,
  RISK_KEYS,
  STATUS_KEYS,
  decisionLabel,
  fieldLabel,
  reasonLabel,
} from "@/features/resolution/labels";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop, toIpcError, type IpcError } from "@/lib/ipc";
import { cn } from "@/lib/utils";

type DetailState = { detail: MatchDetail; error: null } | { detail: null; error: IpcError };

const AGREEMENT_TONE = {
  match: "text-ok",
  partial: "text-warn",
  conflict: "text-danger",
  missing: "text-muted-foreground",
} as const;

/** What each decision means for a pair in its current state (the worker checks it again). */
function allowed(detail: MatchDetail, action: MatchAction): boolean {
  const { status } = detail.match;
  switch (action) {
    case "merge":
      return status !== "merged";
    case "keep_separate":
      return status !== "separate";
    case "defer":
      return status === "pending";
    case "undo":
      return detail.decisions.length > 0 || status !== "pending";
  }
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-1">
      <h3 className="text-[10px] tracking-wider text-muted-foreground uppercase">{title}</h3>
      {children}
    </section>
  );
}

interface EvidenceRow {
  id: string;
  field: string;
  value: string;
  confidence: number;
  provenance: Provenance;
}

/** Every value one record holds, each with the source it came from. */
function Evidence({ detail, label }: { detail: BusinessDetail; label: string }) {
  const t = useT();
  const rows: EvidenceRow[] = [
    ...detail.names.map(({ value, provenance }) => ({
      id: value.id,
      field: fieldLabel(t, "name"),
      value: value.name_raw,
      confidence: value.confidence,
      provenance,
    })),
    ...detail.locations.map(({ value, provenance }) => ({
      id: value.id,
      field: fieldLabel(t, "address"),
      value:
        value.address_raw ??
        [value.street, value.locality, value.region, value.postal_code].filter(Boolean).join(", "),
      confidence: value.confidence,
      provenance,
    })),
    ...detail.websites.map(({ value, provenance }) => ({
      id: value.id,
      field: fieldLabel(t, "website"),
      value: value.url_raw,
      confidence: value.confidence,
      provenance,
    })),
    ...detail.contacts.map(({ value, provenance }) => ({
      id: value.id,
      field: fieldLabel(t, value.contact_type === "phone" ? "phone" : "contact"),
      value: value.value_raw,
      confidence: value.confidence,
      provenance,
    })),
    ...detail.classifications.map(({ value, provenance }) => ({
      id: value.id,
      field: fieldLabel(t, "category"),
      value: value.label_raw,
      confidence: value.confidence,
      provenance,
    })),
  ];
  return (
    <section className="min-w-0 space-y-1.5">
      <h4 className="flex items-center gap-1.5 text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
        {label}
        {detail.is_synthetic && <TestDataBadge />}
      </h4>
      <p className="font-medium break-words">{detail.business.display_name}</p>
      {detail.business.merged_into_id && (
        <InlineNotice variant="partial">{t("resolution.belongsTo")}</InlineNotice>
      )}
      <ul className="space-y-1">
        {rows.map((row) => (
          <li key={row.id} className="rounded-md border p-1.5 text-[11px]">
            <span className="text-muted-foreground">{row.field}: </span>
            <span className="break-words">{row.value}</span>
            <ProvenanceLine provenance={row.provenance} confidence={row.confidence} />
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * One pair side by side: field by field with agreements and conflicts highlighted, the reasons
 * behind the score, the evidence and source of every value, the decisions, and their history.
 */
export function MatchReview({ matchId, onChanged }: { matchId: string; onChanged: () => void }) {
  const t = useT();
  const language = useLanguage();
  const [state, setState] = useState<DetailState | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let current = true;
    desktop.getMatch({ match_id: matchId }).then(
      (detail) => current && setState({ detail, error: null }),
      (cause: unknown) =>
        current && setState({ detail: null, error: toIpcError(cause, "get_match") }),
    );
    return () => {
      current = false;
    };
  }, [matchId]);

  if (state === null) return <StateBlock variant="loading" title={t("common.loading")} />;
  if (state.error !== null) {
    return (
      <StateBlock
        variant="error"
        title={t("resolution.detailErrorTitle")}
        body={state.error.message}
      />
    );
  }

  const { detail } = state;
  const pair = detail.match;
  const nameOf = (businessId: string) =>
    businessId === pair.left_business_id
      ? detail.left.business.display_name
      : businessId === pair.right_business_id
        ? detail.right.business.display_name
        : businessId.slice(-12);

  const act = async (action: MatchAction) => {
    setBusy(true);
    try {
      const updated = await desktop.actOnMatch({
        match_id: pair.id,
        action,
        reason: reason.trim() || null,
      });
      setState({ detail: updated, error: null });
      setReason("");
      toast.success(t("resolution.actionDone"));
      onChanged();
    } catch (cause) {
      toast.error(t("resolution.actionFailed"), {
        description: toIpcError(cause, "act_on_match").message,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4 text-xs">
      <header className="space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm font-medium">
            {t("resolution.scoreValue", { score: Math.round(pair.score * 100) })}
          </span>
          <span className="rounded border px-1 text-[11px]">{t(STATUS_KEYS[pair.status])}</span>
          <span
            className={cn(
              "rounded border px-1 text-[11px]",
              pair.risk === "high" && "border-danger/60 text-danger",
            )}
          >
            {t("resolution.column.risk")}: {t(RISK_KEYS[pair.risk])}
          </span>
        </div>
        <p className="text-[11px] text-muted-foreground">
          {t("resolution.recommended", { outcome: t(OUTCOME_KEYS[pair.outcome]) })} ·{" "}
          {t("resolution.rules", { version: pair.rules_version })}
        </p>
      </header>

      <Block title={t("resolution.comparison")}>
        <div className="overflow-x-auto">
          <table className="w-full table-fixed border-collapse text-[11px]">
            <thead>
              <tr className="text-left text-muted-foreground">
                <th className="w-24 py-1 font-normal">{t("resolution.field")}</th>
                <th className="py-1 font-normal">{t("resolution.left")}</th>
                <th className="w-24 py-1 font-normal">{t("resolution.agreement")}</th>
                <th className="py-1 font-normal">{t("resolution.right")}</th>
              </tr>
            </thead>
            <tbody>
              {detail.fields.map((field) => (
                <tr key={field.field} className="border-t align-top">
                  <th scope="row" className="py-1 pr-2 text-left font-normal text-muted-foreground">
                    {fieldLabel(t, field.field)}
                  </th>
                  <td className="py-1 pr-2 break-words">
                    <Text value={field.left.join(" · ") || null} />
                  </td>
                  <td className={cn("py-1 pr-2", AGREEMENT_TONE[field.agreement])}>
                    {t(AGREEMENT_KEYS[field.agreement])}
                    {field.similarity !== null && ` ${Math.round(field.similarity * 100)}%`}
                  </td>
                  <td className="py-1 break-words">
                    <Text value={field.right.join(" · ") || null} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Block>

      <Block title={t("resolution.reasonsTitle")}>
        <ul className="space-y-0.5">
          {detail.reasons.map((item, index) => (
            <li key={`${item.code}-${index}`} className="flex gap-2">
              <span
                className={cn(
                  "w-10 shrink-0 font-mono tabular-nums",
                  item.direction === "for" && "text-ok",
                  item.direction === "against" && "text-danger",
                  item.direction === "rule" && "text-muted-foreground",
                )}
              >
                {item.direction === "rule"
                  ? "·"
                  : item.weight > 0
                    ? `+${item.weight}`
                    : item.weight}
              </span>
              <span className="min-w-0">
                {reasonLabel(t, item.code)}
                {item.detail && (
                  <span className="block text-[11px] break-words text-muted-foreground">
                    {item.detail}
                  </span>
                )}
              </span>
            </li>
          ))}
        </ul>
        {detail.blocking_keys.length > 0 && (
          <p className="text-[11px] text-muted-foreground">
            {t("resolution.blockingKeys")}:{" "}
            <span className="font-mono break-all">{detail.blocking_keys.join(", ")}</span>
          </p>
        )}
      </Block>

      <section className="space-y-1.5">
        <Label htmlFor="match-reason" className="text-xs">
          {t("resolution.reason")}
        </Label>
        <Textarea
          id="match-reason"
          value={reason}
          maxLength={2000}
          placeholder={t("resolution.reasonPlaceholder")}
          onChange={(event) => setReason(event.target.value)}
          className="min-h-12 text-xs"
        />
        <div className="flex flex-wrap gap-1.5">
          {MATCH_ACTIONS.map((action) => (
            <Button
              key={action}
              size="sm"
              variant={action === "merge" ? "default" : "outline"}
              className="h-7"
              disabled={busy || !allowed(detail, action)}
              onClick={() => void act(action)}
            >
              {t(ACTION_KEYS[action])}
            </Button>
          ))}
        </div>
      </section>

      <Block title={t("resolution.evidenceTitle")}>
        <div className="grid grid-cols-2 gap-2">
          <Evidence detail={detail.left} label={t("resolution.left")} />
          <Evidence detail={detail.right} label={t("resolution.right")} />
        </div>
      </Block>

      <Block title={t("resolution.historyTitle")}>
        {detail.decisions.length === 0 && detail.merges.length === 0 ? (
          <p className="text-muted-foreground">{t("resolution.noHistory")}</p>
        ) : (
          <ul className="space-y-1">
            {detail.decisions.map((decision) => (
              <li key={decision.id}>
                <p>
                  {t("resolution.decisionLine", {
                    decision: decisionLabel(t, decision.decision),
                    actor: decision.actor,
                    time: formatDateTime(decision.decided_at, language),
                  })}
                </p>
                <p className="text-[11px] break-words text-muted-foreground">{decision.reason}</p>
              </li>
            ))}
            {detail.merges.map((merge) => (
              <li key={merge.id} className="text-[11px] text-muted-foreground">
                {t("resolution.mergeLine", {
                  name: nameOf(merge.business_id),
                  into: nameOf(merge.into_business_id),
                  how: t(merge.is_manual ? "resolution.manual" : "resolution.automatic"),
                  time: formatDateTime(merge.merged_at, language),
                })}
                {merge.unmerged_at &&
                  ` · ${t("resolution.unmergeLine", {
                    actor: merge.unmerged_by ?? "",
                    time: formatDateTime(merge.unmerged_at, language),
                  })}`}
              </li>
            ))}
          </ul>
        )}
      </Block>
    </div>
  );
}
