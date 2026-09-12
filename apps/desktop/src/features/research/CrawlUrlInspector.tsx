import { useEffect, useState, type ReactNode } from "react";
import type { CrawlUrlDetail } from "@lead/shared-types";
import { StateBlock } from "@/components/states/StateBlock";
import { Mono, Text } from "@/components/data/cells";
import { formatBytes, reasonLabel } from "@/features/research/settings";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop, toIpcError, type IpcError } from "@/lib/ipc";

type DetailState = { detail: CrawlUrlDetail; error: null } | { detail: null; error: IpcError };

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

function values(payload: Record<string, unknown> | undefined, key: string): string[] {
  const found = payload?.[key];
  if (!Array.isArray(found)) return [];
  return found.map((item) =>
    typeof item === "object" && item !== null && "value" in item
      ? String((item as { value: unknown }).value)
      : String(item),
  );
}

/**
 * One URL of a crawl and everything behind it: how it was fetched or why it was not, the bytes
 * that were stored and the raw findings parsed from them. Findings are not validated here.
 */
export function CrawlUrlInspector({ crawlUrlId }: { crawlUrlId: string }) {
  const t = useT();
  const language = useLanguage();
  const [state, setState] = useState<DetailState | null>(null);

  useEffect(() => {
    let current = true;
    desktop.getCrawlUrl({ crawl_url_id: crawlUrlId }).then(
      (detail) => current && setState({ detail, error: null }),
      (cause: unknown) =>
        current && setState({ detail: null, error: toIpcError(cause, "get_crawl_url") }),
    );
    return () => {
      current = false;
    };
  }, [crawlUrlId]);

  if (state === null) return <StateBlock variant="loading" title={t("common.loading")} />;
  if (state.error !== null) {
    return (
      <StateBlock variant="error" title={t("research.urlErrorTitle")} body={state.error.message} />
    );
  }

  const { crawl_url: url, acquired_page: page, raw_record: record } = state.detail;
  const payload = record?.payload_json as Record<string, unknown> | undefined;
  const hops = Array.isArray(url.redirects_json?.hops) ? url.redirects_json.hops : [];
  const emails = values(payload, "emails");
  const phones = values(payload, "phones");
  const jsonLd = Array.isArray(payload?.json_ld) ? payload.json_ld.length : 0;

  return (
    <div className="space-y-4 text-xs">
      <header className="space-y-1">
        <h2 className="font-mono text-[12px] break-all">{url.url}</h2>
        <p className="text-[11px] text-muted-foreground">
          {t(`crawlStatus.${url.status}`)} · {t(`crawlTarget.${url.target}`)} ·{" "}
          {t(`pageKind.${url.page_kind}`)}
          {url.reason && ` · ${reasonLabel(t, url.reason)}`}
        </p>
      </header>

      <Block title={t("research.howFetched")}>
        <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-0.5">
          <Row label={t("discovery.method")}>
            {url.method ? t(`acquisitionMethod.${url.method}`) : "—"}
          </Row>
          {url.fallback_reason && (
            <Row label={t("research.fallbackReason")}>
              {t("research.fallbackValue", {
                reason: reasonLabel(t, url.fallback_reason),
                ms: url.browser_ms ?? 0,
              })}
            </Row>
          )}
          <Row label={t("research.httpStatus")}>
            <Mono value={url.http_status} />
          </Row>
          <Row label={t("research.finalUrl")}>
            <span className="font-mono break-all">
              <Text value={url.final_url} />
            </span>
          </Row>
          <Row label={t("research.contentType")}>
            <Text value={url.content_type} />
          </Row>
          <Row label={t("research.bytes")}>
            {url.byte_size === null ? "—" : formatBytes(url.byte_size)}
          </Row>
          <Row label={t("research.timing")}>
            {t("research.timingValue", {
              ms: url.fetch_ms ?? 0,
              requests: url.request_count,
              retries: url.retry_count,
            })}
          </Row>
          <Row label={t("research.depth")}>{url.depth}</Row>
          <Row label={t("research.updated")}>{formatDateTime(url.updated_at, language)}</Row>
          {url.error_message && (
            <Row label={t("research.message")}>
              <span className="text-danger">{url.error_message}</span>
            </Row>
          )}
        </dl>
        {hops.length > 0 && (
          <ol className="mt-1 space-y-0.5">
            {hops.map((hop, index) => {
              const { status, location } = hop as { status?: number; location?: string | null };
              return (
                <li key={index} className="font-mono text-[10px] break-all">
                  {status} → {location ?? "—"}
                </li>
              );
            })}
          </ol>
        )}
      </Block>

      {state.detail.duplicate_of && (
        <Block title={t("research.duplicateTitle")}>
          <p className="text-muted-foreground">
            {t("research.duplicateBody", { url: state.detail.duplicate_of.url })}
          </p>
        </Block>
      )}

      {payload && (
        <Block title={t("research.findings")}>
          <p className="text-[11px] text-muted-foreground">{t("research.findingsHelp")}</p>
          <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-0.5">
            <Row label={t("research.title")}>
              <Text value={typeof payload.title === "string" ? payload.title : null} />
            </Row>
            <Row label={t("research.emails")}>{emails.length ? emails.join(", ") : "—"}</Row>
            <Row label={t("research.phones")}>{phones.length ? phones.join(", ") : "—"}</Row>
            <Row label={t("research.jsonLd")}>{jsonLd}</Row>
          </dl>
        </Block>
      )}

      <Block title={t("discovery.provenanceTitle")}>
        <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-0.5">
          <Row label={t("discovery.artifact")}>
            <span className="font-mono break-all">
              <Text value={page?.artifact_path ?? null} />
            </span>
          </Row>
          <Row label="sha256">
            <span className="font-mono text-[10px] break-all">
              <Text value={url.body_sha256} />
            </span>
          </Row>
          <Row label={t("research.contentHash")}>
            <span className="font-mono text-[10px] break-all">
              <Text value={url.content_sha256} />
            </span>
          </Row>
          {url.screenshot_path && (
            <Row label={t("research.screenshot")}>
              <span className="font-mono break-all">{url.screenshot_path}</span>
            </Row>
          )}
          {state.detail.discovered_from && (
            <Row label={t("research.discoveredFrom")}>
              <span className="font-mono break-all">{state.detail.discovered_from.url}</span>
            </Row>
          )}
        </dl>
      </Block>

      {record && (
        <Block title={t("discovery.rawRecordTitle")}>
          <p className="text-[11px] text-muted-foreground">{t("discovery.rawRecordHelp")}</p>
          <pre className="max-h-80 overflow-auto rounded border bg-muted/40 p-2 font-mono text-[10px] leading-relaxed">
            {JSON.stringify(record.payload_json, null, 2)}
          </pre>
          <p className="font-mono text-[10px] break-all text-muted-foreground">
            sha256 {record.payload_sha256}
          </p>
        </Block>
      )}
    </div>
  );
}
