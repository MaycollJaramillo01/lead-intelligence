import { useEffect, useState, type ReactNode } from "react";
import type { CandidateDetail } from "@lead/shared-types";
import { ExternalLink } from "lucide-react";
import { StateBlock } from "@/components/states/StateBlock";
import { Text } from "@/components/data/cells";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop, toIpcError, type IpcError } from "@/lib/ipc";

type DetailState = { detail: CandidateDetail; error: null } | { detail: null; error: IpcError };

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </>
  );
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-1">
      <h3 className="text-[10px] tracking-wider text-muted-foreground uppercase">{title}</h3>
      {children}
    </section>
  );
}

/**
 * One candidate with the whole chain behind it. It is deliberately not a business card: no score,
 * no verdict, no contact quality - only what a source said and where it said it.
 */
export function CandidateInspector({ candidateId }: { candidateId: string }) {
  const t = useT();
  const language = useLanguage();
  const [state, setState] = useState<DetailState | null>(null);

  useEffect(() => {
    let current = true;
    desktop.getCandidate({ candidate_id: candidateId }).then(
      (detail) => current && setState({ detail, error: null }),
      (cause: unknown) =>
        current && setState({ detail: null, error: toIpcError(cause, "get_candidate") }),
    );
    return () => {
      current = false;
    };
  }, [candidateId]);

  if (state === null) return <StateBlock variant="loading" title={t("common.loading")} />;
  if (state.error !== null) {
    return (
      <StateBlock
        variant="error"
        title={t("discovery.candidateErrorTitle")}
        body={state.error.message}
      />
    );
  }

  const {
    candidate,
    connector,
    source_run,
    source_query,
    acquired_page,
    raw_record,
    duplicate_of,
  } = state.detail;

  return (
    <div className="space-y-4 text-xs">
      <header className="space-y-1">
        <h2 className="text-sm font-medium break-words">{candidate.name_raw}</h2>
        <p className="text-[11px] text-muted-foreground">{t("discovery.notALead")}</p>
      </header>

      <Block title={t("discovery.whatTheSourceSaid")}>
        <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-0.5">
          <Row label={t("discovery.column.locality")}>
            <Text value={candidate.locality} />
          </Row>
          <Row label={t("discovery.column.region")}>
            <Text value={candidate.region} />
          </Row>
          <Row label={t("discovery.column.postalCode")}>
            <Text value={candidate.postal_code} />
          </Row>
          <Row label={t("discovery.column.domain")}>
            <Text value={candidate.domain} />
          </Row>
          <Row label={t("discovery.column.phone")}>
            <Text value={candidate.phone_raw} />
          </Row>
          <Row label={t("discovery.column.category")}>
            <Text value={candidate.category_raw} />
          </Row>
        </dl>
      </Block>

      <Block title={t("discovery.provenanceTitle")}>
        <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-0.5">
          <Row label={t("discovery.source")}>
            {connector.name} <span className="font-mono text-[10px]">({connector.key})</span>
          </Row>
          <Row label={t("discovery.method")}>{t(`acquisitionMethod.${raw_record.method}`)}</Row>
          <Row label={t("discovery.acquiredAt")}>
            {formatDateTime(raw_record.acquired_at, language)}
          </Row>
          <Row label={t("discovery.externalId")}>
            <span className="font-mono">
              <Text value={candidate.external_id} />
            </span>
          </Row>
          <Row label={t("discovery.runStatus")}>{t(`sourceRunStatus.${source_run.status}`)}</Row>
          {source_query && (
            <Row label={t("discovery.cursor")}>
              <span className="font-mono">
                <Text value={source_query.cursor} />
              </span>
            </Row>
          )}
          {acquired_page && (
            <Row label={t("discovery.acquiredFrom")}>
              <span className="font-mono break-all">{acquired_page.url}</span>
            </Row>
          )}
          {acquired_page?.artifact_path && (
            <Row label={t("discovery.artifact")}>
              <span className="font-mono break-all">{acquired_page.artifact_path}</span>
            </Row>
          )}
        </dl>
        {candidate.source_url && (
          <a
            href={candidate.source_url}
            target="_blank"
            rel="noreferrer noopener"
            className="mt-1 inline-flex items-center gap-1 text-primary underline-offset-2 hover:underline"
          >
            <ExternalLink aria-hidden="true" className="size-3" />
            {t("discovery.openSource")}
          </a>
        )}
      </Block>

      {duplicate_of && (
        <Block title={t("discovery.duplicateTitle")}>
          <p className="text-muted-foreground">
            {t("discovery.duplicateBody", { name: duplicate_of.name_raw })}
          </p>
          <p className="font-mono text-[10px] break-all">{candidate.dedup_key}</p>
        </Block>
      )}

      <Block title={t("discovery.rawRecordTitle")}>
        <p className="text-[11px] text-muted-foreground">{t("discovery.rawRecordHelp")}</p>
        <pre className="max-h-80 overflow-auto rounded border bg-muted/40 p-2 font-mono text-[10px] leading-relaxed">
          {JSON.stringify(raw_record.payload_json, null, 2)}
        </pre>
        <p className="font-mono text-[10px] break-all text-muted-foreground">
          sha256 {raw_record.payload_sha256}
        </p>
      </Block>
    </div>
  );
}
