import { useEffect, useState, type ReactNode } from "react";
import type { BusinessDetail, Provenance } from "@lead/shared-types";
import { EvidenceItem } from "@/components/domain/EvidenceItem";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import { Text, TestDataBadge } from "@/components/data/cells";
import { BUSINESS_STATUS_KEYS } from "@/features/businesses/columns";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop, toIpcError, type IpcError } from "@/lib/ipc";

type DetailState = { detail: BusinessDetail; error: null } | { detail: null; error: IpcError };

/** Loads one business. The panel remounts per selection, so a late answer is simply dropped. */
function useBusinessDetail(businessId: string): DetailState | null {
  const [state, setState] = useState<DetailState | null>(null);
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
  return state;
}

function ProvenanceLine({
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

interface FactsProps<T extends { id: string; confidence: number }> {
  title: string;
  observed: Array<{ value: T; provenance: Provenance }>;
  render: (value: T) => ReactNode;
}

/** A group of observed values, each one with where it came from. */
function Facts<T extends { id: string; confidence: number }>({
  title,
  observed,
  render,
}: FactsProps<T>) {
  if (observed.length === 0) return null;
  return (
    <section className="space-y-1.5">
      <h4 className="text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
        {title} ({observed.length})
      </h4>
      <ul className="space-y-1.5">
        {observed.map(({ value, provenance }) => (
          <li key={value.id} className="rounded-md border p-2 text-xs">
            {render(value)}
            <ProvenanceLine provenance={provenance} confidence={value.confidence} />
          </li>
        ))}
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
  const state = useBusinessDetail(businessId);

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

      <Facts
        title={t("inspector.names")}
        observed={detail.names}
        render={(name) => (
          <RawAndNormalized raw={name.name_raw} normalized={name.name_normalized} />
        )}
      />
      <Facts
        title={t("inspector.locations")}
        observed={detail.locations}
        render={(location) => (
          <RawAndNormalized
            raw={location.address_raw}
            normalized={
              [location.street, location.locality, location.region, location.postal_code]
                .filter(Boolean)
                .join(", ") || null
            }
          />
        )}
      />
      <Facts
        title={t("inspector.websites")}
        observed={detail.websites}
        render={(site) => <RawAndNormalized raw={site.url_raw} normalized={site.domain} />}
      />
      <Facts
        title={t("inspector.contacts")}
        observed={detail.contacts}
        render={(contact) => (
          <RawAndNormalized raw={contact.value_raw} normalized={contact.value_normalized} />
        )}
      />
      <Facts
        title={t("inspector.classifications")}
        observed={detail.classifications}
        render={(label) => (
          <RawAndNormalized raw={label.label_raw} normalized={label.taxonomy_key} />
        )}
      />
      {detail.evidence.length > 0 && (
        <section className="space-y-1.5">
          <h4 className="text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
            {t("inspector.evidence")} ({detail.evidence.length})
          </h4>
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
    </div>
  );
}
