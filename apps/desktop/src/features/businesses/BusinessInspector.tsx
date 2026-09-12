import { useEffect, useState, type ReactNode } from "react";
import type {
  BusinessDetail,
  CanonicalField,
  CanonicalValue,
  Provenance,
} from "@lead/shared-types";
import { toast } from "sonner";
import { EvidenceItem } from "@/components/domain/EvidenceItem";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import { Text, TestDataBadge } from "@/components/data/cells";
import { Button } from "@/components/ui/button";
import { BUSINESS_STATUS_KEYS } from "@/features/businesses/columns";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop, toIpcError, type IpcError } from "@/lib/ipc";
import { cn } from "@/lib/utils";

type DetailState = { detail: BusinessDetail; error: null } | { detail: null; error: IpcError };

export function ProvenanceLine({
  provenance,
  confidence,
}: {
  provenance: Provenance;
  confidence: number;
}) {
  const t = useT();
  const language = useLanguage();
  return (
    <p className="mt-1 text-[11px] leading-snug text-muted-foreground">
      {t("inspector.provenance", {
        source: provenance.connector_name,
        extractor: provenance.extractor,
        version: provenance.extractor_version,
        time: formatDateTime(provenance.acquired_at, language),
        confidence: Math.round(confidence * 100),
      })}
    </p>
  );
}

function RawAndNormalized({ raw, normalized }: { raw: string | null; normalized: string | null }) {
  const t = useT();
  return (
    <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-0.5">
      <dt className="text-muted-foreground">{t("inspector.raw")}</dt>
      <dd className="break-words">
        <Text value={raw} />
      </dd>
      <dt className="text-muted-foreground">{t("inspector.normalized")}</dt>
      <dd className="font-mono break-words">
        <Text value={normalized} />
      </dd>
    </dl>
  );
}

function Heading({ children }: { children: ReactNode }) {
  return (
    <h4 className="flex flex-wrap items-center gap-2 text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
      {children}
    </h4>
  );
}

interface FactsProps<T extends { id: string; confidence: number }> {
  title: string;
  observed: Array<{ value: T; provenance: Provenance }>;
  render: (value: T) => ReactNode;
  /** The field an observation can represent, when the business shows one for it. */
  fieldOf?: (value: T) => CanonicalField | null;
  canonical?: CanonicalValue[];
  onChoose?: (field: CanonicalField, observationId: string) => void;
  busy?: boolean;
}

/**
 * A group of observed values, each one with where it came from. After resolution (phase 06) the
 * value the business shows is marked, a field whose records disagree says so, and any other value
 * can be chosen instead.
 */
function Facts<T extends { id: string; confidence: number }>({
  title,
  observed,
  render,
  fieldOf,
  canonical = [],
  onChoose,
  busy = false,
}: FactsProps<T>) {
  const t = useT();
  if (observed.length === 0) return null;
  const shown = new Set(canonical.map((value) => value.observation_id));
  const fields = new Set(observed.map(({ value }) => fieldOf?.(value) ?? null));
  const conflicts = Math.max(
    0,
    ...canonical.filter((value) => fields.has(value.field)).map((value) => value.alternative_count),
  );
  return (
    <section className="space-y-1.5">
      <Heading>
        {title} ({observed.length})
        {conflicts > 1 && (
          <span className="rounded border border-warn/60 px-1 tracking-normal text-warn normal-case">
            {t("inspector.conflict", { n: conflicts })}
          </span>
        )}
      </Heading>
      <ul className="space-y-1.5">
        {observed.map(({ value, provenance }) => {
          const field = fieldOf?.(value) ?? null;
          const isShown = shown.has(value.id);
          return (
            <li
              key={value.id}
              className={cn("rounded-md border p-2 text-xs", isShown && "border-primary/60")}
            >
              {render(value)}
              <ProvenanceLine provenance={provenance} confidence={value.confidence} />
              {field !== null && isShown && (
                <span className="mt-1 inline-block rounded bg-primary/10 px-1 text-[10px] text-primary">
                  {t("inspector.shown")}
                </span>
              )}
              {field !== null && !isShown && onChoose && canonical.length > 0 && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="ml-1 h-auto px-1 py-0 text-[11px] text-primary"
                  disabled={busy}
                  onClick={() => onChoose(field, value.id)}
                >
                  {t("inspector.showThis")}
                </Button>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function jsonText(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  return typeof value === "string" ? value : JSON.stringify(value);
}

export function BusinessInspector({ businessId }: { businessId: string }) {
  const t = useT();
  const language = useLanguage();
  const [state, setState] = useState<DetailState | null>(null);
  const [busy, setBusy] = useState(false);

  // the panel remounts per selection, so a late answer is simply dropped
  useEffect(() => {
    let current = true;
    desktop.getBusiness({ business_id: businessId }).then(
      (detail) => current && setState({ detail, error: null }),
      (cause: unknown) =>
        current && setState({ detail: null, error: toIpcError(cause, "get_business") }),
    );
    return () => {
      current = false;
    };
  }, [businessId]);

  if (state === null) return <StateBlock variant="loading" title={t("state.loading")} />;
  if (state.error) {
    const missing = state.error.code === "not_found";
    return (
      <StateBlock
        variant={missing ? "empty" : "error"}
        title={t(missing ? "inspector.notFoundTitle" : "inspector.errorTitle")}
        body={state.error.message}
      />
    );
  }

  const { business, ...detail } = state.detail;
  const merged = business.merged_into_id !== null;
  const choose = merged
    ? undefined
    : async (field: CanonicalField, observationId: string) => {
        setBusy(true);
        try {
          const updated = await desktop.chooseCanonicalValue({
            business_id: business.id,
            field,
            observation_id: observationId,
            reason: null,
          });
          setState({ detail: updated, error: null });
        } catch (cause) {
          toast.error(t("inspector.choiceFailed"), {
            description: toIpcError(cause, "choose_canonical_value").message,
          });
        } finally {
          setBusy(false);
        }
      };
  const choice = {
    canonical: detail.canonical,
    onChoose: choose && ((field: CanonicalField, id: string) => void choose(field, id)),
    busy,
  };
  const nameOf = (id: string) =>
    detail.members.find((member) => member.business_id === id)?.display_name ?? id.slice(-12);

  return (
    <div className="space-y-3">
      <header className="space-y-1">
        <div className="flex items-start justify-between gap-2">
          <h3 className="text-sm font-medium break-words">{business.display_name}</h3>
          {detail.is_synthetic && <TestDataBadge />}
        </div>
        <p className="text-xs text-muted-foreground">
          {t("businesses.column.status")}: {t(BUSINESS_STATUS_KEYS[business.status])}
        </p>
        <p className="font-mono text-[11px] break-all text-muted-foreground">{business.id}</p>
      </header>
      {detail.is_synthetic && (
        <InlineNotice variant="partial">{t("inspector.syntheticNotice")}</InlineNotice>
      )}
      {merged && <InlineNotice variant="partial">{t("inspector.belongsTo")}</InlineNotice>}

      {detail.members.length > 1 && (
        <section className="space-y-1.5">
          <Heading>{t("inspector.records", { n: detail.members.length })}</Heading>
          <ul className="space-y-1">
            {detail.members.map((member) => (
              <li key={member.business_id} className="rounded-md border px-2 py-1 text-xs">
                <p className="break-words">{member.display_name}</p>
                <p className="text-[11px] text-muted-foreground">
                  {[member.connector_name, formatDateTime(member.first_seen_at, language)]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              </li>
            ))}
          </ul>
        </section>
      )}

      <Facts
        title={t("inspector.names")}
        observed={detail.names}
        render={(name) => (
          <RawAndNormalized raw={name.name_raw} normalized={name.name_normalized} />
        )}
        fieldOf={() => "name"}
        {...choice}
      />
      <Facts
        title={t("inspector.locations")}
        observed={detail.locations}
        render={(location) => (
          <RawAndNormalized
            raw={location.address_raw}
            normalized={
              [
                location.street,
                location.unit,
                location.locality,
                location.region,
                location.postal_code,
              ]
                .filter(Boolean)
                .join(", ") || null
            }
          />
        )}
        fieldOf={() => "location"}
        {...choice}
      />
      <Facts
        title={t("inspector.websites")}
        observed={detail.websites}
        render={(site) => <RawAndNormalized raw={site.url_raw} normalized={site.url_normalized} />}
        fieldOf={() => "website"}
        {...choice}
      />
      <Facts
        title={t("inspector.contacts")}
        observed={detail.contacts}
        render={(contact) => (
          <RawAndNormalized raw={contact.value_raw} normalized={contact.value_normalized} />
        )}
        fieldOf={(contact) =>
          contact.contact_type === "phone" || contact.contact_type === "email"
            ? contact.contact_type
            : null
        }
        {...choice}
      />
      <Facts
        title={t("inspector.classifications")}
        observed={detail.classifications}
        render={(label) => (
          <RawAndNormalized raw={label.label_raw} normalized={label.taxonomy_key} />
        )}
        fieldOf={(label) => (label.kind === "category" ? "category" : null)}
        {...choice}
      />
      {detail.evidence.length > 0 && (
        <section className="space-y-1.5">
          <Heading>
            {t("inspector.evidence")} ({detail.evidence.length})
          </Heading>
          {detail.evidence.map(({ value, provenance }) => (
            <EvidenceItem
              key={value.id}
              evidence={{
                id: value.id,
                claim: value.claim,
                observedValue: jsonText(value.value_json),
                source: {
                  label: provenance.connector_name,
                  type: provenance.connector_kind,
                  url: provenance.source_url,
                },
                capturedAt: provenance.acquired_at,
                confidence: value.confidence,
                strength: value.strength,
                context: value.snippet,
              }}
            />
          ))}
        </section>
      )}
      {detail.merges.length > 0 && (
        <section className="space-y-1">
          <Heading>{t("inspector.merges")}</Heading>
          <ul className="space-y-0.5 text-[11px] text-muted-foreground">
            {detail.merges.map((merge) => (
              <li key={merge.id}>
                {t("inspector.mergedOn", {
                  name: nameOf(merge.business_id),
                  how: t(merge.is_manual ? "resolution.manual" : "resolution.automatic"),
                  time: formatDateTime(merge.merged_at, language),
                })}
                {merge.unmerged_at &&
                  ` · ${t("inspector.unmergedOn", {
                    time: formatDateTime(merge.unmerged_at, language),
                  })}`}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
