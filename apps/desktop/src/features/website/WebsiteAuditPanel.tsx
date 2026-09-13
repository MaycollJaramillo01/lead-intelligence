import { useEffect, useMemo, useState, type ReactNode } from "react";
import type {
  WebsiteAuditDetail,
  WebsiteCategory,
  WebsiteEvidence,
  WebsiteFindingResult,
  WebsiteFindingSeverity,
  WebsiteFindingView,
} from "@lead/shared-types";
import { RefreshCw } from "lucide-react";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import { formatBytes } from "@/features/research/settings";
import {
  ACTIONABLE,
  AUDIT_STATUS_KEYS,
  CATEGORIES,
  CATEGORY_KEYS,
  ELEMENT_KEYS,
  RESULTS,
  RESULT_KEYS,
  RESULT_TONE,
  SEVERITIES,
  SEVERITY_KEYS,
  deviceLabel,
  gapLabel,
  noteLabel,
  technologyCategoryLabel,
} from "@/features/website/labels";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop, toIpcError, type IpcError } from "@/lib/ipc";
import { cn } from "@/lib/utils";

type DetailState = { detail: WebsiteAuditDetail; error: null } | { detail: null; error: IpcError };
/** `actionable` shows failures and warnings; an empty value shows every result. */
type ResultFilter = "actionable" | "" | WebsiteFindingResult;

const percent = (value: number) => Math.round(value * 100);

function Heading({ children }: { children: ReactNode }) {
  return (
    <h4 className="text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
      {children}
    </h4>
  );
}

function EvidenceList({ refs }: { refs: WebsiteEvidence[] }) {
  const t = useT();
  return (
    <div className="space-y-0.5">
      <p className="text-[10px] tracking-wider text-muted-foreground uppercase">
        {t("website.evidence")}
      </p>
      <ul className="space-y-1">
        {refs.map((ref, index) => (
          <li key={`${ref.crawl_url_id ?? ""}-${ref.field}-${index}`} className="space-y-0.5">
            <p className="flex min-w-0 flex-wrap gap-x-2 text-[11px] text-muted-foreground">
              <span className="font-mono">{ref.field}</span>
              {ref.url && <span className="truncate font-mono">{ref.url}</span>}
            </p>
            {ref.snippet && (
              <blockquote className="border-l-2 pl-2 font-mono text-[11px] break-words">
                {ref.snippet}
              </blockquote>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Finding({ finding }: { finding: WebsiteFindingView }) {
  const t = useT();
  const actionable = ACTIONABLE.has(finding.result);
  return (
    <li
      className={cn(
        "space-y-1 rounded-md border p-2",
        finding.result === "unknown" && "border-dashed",
      )}
    >
      <p className="flex flex-wrap items-center gap-1.5">
        <span className={cn("rounded border px-1 text-[11px]", RESULT_TONE[finding.result])}>
          {t(RESULT_KEYS[finding.result])}
        </span>
        <span className="font-medium">{finding.title}</span>
      </p>
      <p className="text-[11px] text-muted-foreground">
        {t("website.findingMeta", {
          severity: t(SEVERITY_KEYS[finding.severity]),
          category: t(CATEGORY_KEYS[finding.category]),
          confidence: percent(finding.confidence),
          rule: finding.rule_id,
          version: finding.rule_version,
        })}
      </p>
      <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-0.5">
        {finding.observed_value && (
          <>
            <dt className="text-muted-foreground">{t("website.observed")}</dt>
            <dd className="break-words">{finding.observed_value}</dd>
          </>
        )}
        <dt className="text-muted-foreground">{t("website.expected")}</dt>
        <dd className="break-words">{finding.expected_condition}</dd>
        {actionable && finding.remediation && (
          <>
            <dt className="text-muted-foreground">{t("website.remediation")}</dt>
            <dd className="break-words">{finding.remediation}</dd>
          </>
        )}
        {actionable && finding.commercial_relevance && (
          <>
            <dt className="text-muted-foreground">{t("website.relevance")}</dt>
            <dd className="break-words">{finding.commercial_relevance}</dd>
          </>
        )}
      </dl>
      {finding.evidence.length > 0 && <EvidenceList refs={finding.evidence} />}
    </li>
  );
}

interface WebsiteAuditPanelProps {
  auditId: string;
  busy: boolean;
  /** Opens another audit of the same site, from its history. */
  onOpen: (auditId: string) => void;
  onAuditAgain: (projectId: string, site: string) => void;
}

/**
 * One website audit (phase 08): actionable findings first, each with what was observed, what was
 * expected, what to do and the evidence that shows it; then coverage, subscores and their formula,
 * technologies, conversion elements, the pages read with their lab measurements, and what changed
 * since the previous audit. An unknown result is never shown as a failure.
 */
export function WebsiteAuditPanel({ auditId, busy, onOpen, onAuditAgain }: WebsiteAuditPanelProps) {
  const t = useT();
  const language = useLanguage();
  const [state, setState] = useState<DetailState | null>(null);
  const [category, setCategory] = useState<WebsiteCategory | "">("");
  const [severity, setSeverity] = useState<WebsiteFindingSeverity | "">("");
  const [result, setResult] = useState<ResultFilter>("actionable");

  useEffect(() => {
    let current = true;
    desktop.getWebsiteAudit({ audit_id: auditId }).then(
      (detail) => current && setState({ detail, error: null }),
      (cause: unknown) =>
        current && setState({ detail: null, error: toIpcError(cause, "get_website_audit") }),
    );
    return () => {
      current = false;
    };
  }, [auditId]);

  const findings = useMemo(
    () =>
      (state?.detail?.findings ?? []).filter(
        (finding) =>
          (category === "" || finding.category === category) &&
          (severity === "" || finding.severity === severity) &&
          (result === "" ||
            (result === "actionable" ? ACTIONABLE.has(finding.result) : finding.result === result)),
      ),
    [state, category, severity, result],
  );

  if (state === null) return <StateBlock variant="loading" title={t("state.loading")} />;
  if (state.error !== null) {
    return (
      <StateBlock variant="error" title={t("website.loadErrorTitle")} body={state.error.message} />
    );
  }

  const { detail } = state;
  const { audit, summary, diff } = detail;
  const { coverage } = summary;
  const when = (value: string) => formatDateTime(value, language);
  const score = (value: number | null) => (value === null ? t("website.notScored") : String(value));
  const resultOf = (value: WebsiteFindingResult | null) =>
    value === null ? "—" : t(RESULT_KEYS[value]);
  const changes = diff
    ? [
        { label: t("website.technologiesAdded"), items: diff.technologies_added },
        { label: t("website.technologiesRemoved"), items: diff.technologies_removed },
        { label: t("website.pagesAdded"), items: diff.pages_added },
        { label: t("website.pagesRemoved"), items: diff.pages_removed },
      ].filter((change) => change.items.length > 0)
    : [];

  return (
    <section aria-label={t("website.title")} className="space-y-3 text-xs">
      <div className="space-y-1">
        <Heading>{t("website.title")}</Heading>
        <p className="font-mono text-sm break-all">{audit.site}</p>
        <p>
          <span className="font-medium">{t(AUDIT_STATUS_KEYS[audit.status])}</span>{" "}
          <span className="font-mono text-[11px] text-muted-foreground">{audit.status}</span>
        </p>
        <p className="text-[11px] text-muted-foreground">
          {t("website.meta", {
            audited: when(audit.created_at),
            crawled: when(audit.crawled_at),
            rules: audit.ruleset_version,
            extractor: audit.extractor_version,
          })}
        </p>
        {audit.final_url && (
          <p className="text-[11px] break-all text-muted-foreground">
            {t("website.finalUrl")}: <span className="font-mono">{audit.final_url}</span>
          </p>
        )}
        <Button
          variant="outline"
          size="sm"
          className="h-7"
          disabled={busy}
          onClick={() => onAuditAgain(audit.project_id, audit.site)}
        >
          <RefreshCw aria-hidden="true" />
          {t("website.auditAgain")}
        </Button>
        <p className="text-[11px] text-muted-foreground">{t("website.auditAgainHint")}</p>
      </div>

      {audit.status !== "complete" && (
        <InlineNotice variant="partial">
          {t(audit.status === "empty" ? "website.emptyNotice" : "website.partialNotice")}
        </InlineNotice>
      )}

      <div className="space-y-1.5">
        <Heading>{t("website.findings", { n: findings.length })}</Heading>
        <div className="grid grid-cols-3 gap-1.5">
          <label className="flex flex-col gap-0.5 text-[11px]">
            {t("website.filterResult")}
            <select
              value={result}
              onChange={(event) => setResult(event.target.value as ResultFilter)}
              className="h-7 rounded-md border bg-transparent px-1 text-xs"
            >
              <option value="actionable">{t("website.actionable")}</option>
              <option value="">{t("website.any")}</option>
              {RESULTS.map((value) => (
                <option key={value} value={value}>
                  {t(RESULT_KEYS[value])}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-0.5 text-[11px]">
            {t("website.filterCategory")}
            <select
              value={category}
              onChange={(event) => setCategory(event.target.value as WebsiteCategory | "")}
              className="h-7 rounded-md border bg-transparent px-1 text-xs"
            >
              <option value="">{t("website.any")}</option>
              {CATEGORIES.map((value) => (
                <option key={value} value={value}>
                  {t(CATEGORY_KEYS[value])}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-0.5 text-[11px]">
            {t("website.filterSeverity")}
            <select
              value={severity}
              onChange={(event) => setSeverity(event.target.value as WebsiteFindingSeverity | "")}
              className="h-7 rounded-md border bg-transparent px-1 text-xs"
            >
              <option value="">{t("website.any")}</option>
              {SEVERITIES.map((value) => (
                <option key={value} value={value}>
                  {t(SEVERITY_KEYS[value])}
                </option>
              ))}
            </select>
          </label>
        </div>
        {findings.length === 0 ? (
          <p className="text-muted-foreground">{t("website.noFindings")}</p>
        ) : (
          <ul className="space-y-1.5">
            {findings.map((finding) => (
              <Finding key={finding.rule_id} finding={finding} />
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-1">
        <Heading>{t("website.coverage")}</Heading>
        <p>
          {t("website.coverageLine", {
            analyzed: coverage.pages_analyzed,
            expected: coverage.pages_expected,
            failed: coverage.pages_failed,
            evaluated: coverage.rules_evaluated,
            rules: coverage.rules,
            unknown: coverage.rules_unknown,
            na: coverage.rules_not_applicable,
          })}
        </p>
        {coverage.gaps.length > 0 && (
          <p className="text-[11px] text-muted-foreground">
            {t("website.gaps")}: {coverage.gaps.map((code) => gapLabel(t, code)).join(" · ")}
          </p>
        )}
        {coverage.limits_hit.length > 0 && (
          <p className="text-[11px] text-muted-foreground">
            {t("website.limits")}:{" "}
            <span className="font-mono">{coverage.limits_hit.join(", ")}</span>
          </p>
        )}
        <ul className="list-disc space-y-0.5 pl-4 text-[11px] text-muted-foreground">
          {summary.notes.map((code) => (
            <li key={code}>{noteLabel(t, code)}</li>
          ))}
        </ul>
      </div>

      <div className="space-y-1">
        <Heading>{t("website.subscores")}</Heading>
        <table className="w-full text-left">
          <tbody>
            {summary.subscores.map((item) => (
              <tr key={item.category} className="border-b align-top last:border-0">
                <th scope="row" className="py-1 pr-2 font-normal">
                  {t(CATEGORY_KEYS[item.category])}
                </th>
                <td className="py-1 pr-2 font-mono tabular-nums">{score(item.score)}</td>
                <td className="py-1 text-[11px] text-muted-foreground">
                  {t("website.subscoreLine", {
                    coverage: percent(item.coverage),
                    confidence: percent(item.confidence),
                  })}
                  <br />
                  {t("website.counts", {
                    pass: item.passed,
                    warning: item.warnings,
                    fail: item.failed,
                    unknown: item.unknown,
                    na: item.not_applicable,
                  })}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <details className="text-[11px] text-muted-foreground">
          <summary className="cursor-pointer">{t("website.formula")}</summary>
          <p className="mt-1 break-words">{summary.formula}</p>
          <p className="mt-1">{t("website.noOverall")}</p>
        </details>
      </div>

      <div className="space-y-1">
        <Heading>{t("website.technologies", { n: detail.technologies.length })}</Heading>
        {detail.technologies.length === 0 ? (
          <p className="text-muted-foreground">{t("website.noTechnologies")}</p>
        ) : (
          <ul className="space-y-1">
            {detail.technologies.map((technology) => (
              <li key={technology.key} className="space-y-0.5 rounded-md border p-2">
                <p>
                  <span className="font-medium">{technology.name}</span>
                  {technology.version && <span className="font-mono"> {technology.version}</span>}
                </p>
                <p className="text-[11px] text-muted-foreground">
                  {t("website.technologyLine", {
                    category: technologyCategoryLabel(t, technology.category),
                    confidence: percent(technology.confidence),
                  })}
                </p>
                <EvidenceList refs={technology.evidence.slice(0, 1)} />
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-1">
        <Heading>{t("website.conversion", { n: detail.conversion.length })}</Heading>
        {detail.conversion.length === 0 ? (
          <p className="text-muted-foreground">{t("website.noConversion")}</p>
        ) : (
          <ul className="space-y-0.5">
            {detail.conversion.map((element, index) => (
              <li key={`${element.kind}-${index}`} className="flex min-w-0 flex-wrap gap-x-2">
                <span className="shrink-0 font-medium">{t(ELEMENT_KEYS[element.kind])}</span>
                {element.label && <span className="break-words">{element.label}</span>}
                <span className="truncate font-mono text-[11px] text-muted-foreground">
                  {element.page_url}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-1">
        <Heading>{t("website.pages", { n: summary.pages.length })}</Heading>
        <p className="text-[11px] text-muted-foreground">
          {t("website.labTool", { tool: summary.lab_tool })}
        </p>
        <ul className="space-y-1">
          {summary.pages.map((page) => (
            <li
              key={page.crawl_url_id}
              className={cn(
                "space-y-0.5 rounded-md border p-1.5",
                !page.analyzed && "border-dashed",
              )}
            >
              <p className="truncate font-mono text-[11px]">{page.final_url ?? page.url}</p>
              <p className="text-[11px] text-muted-foreground">
                {[
                  t(`crawlStatus.${page.status}`),
                  page.http_status === null ? null : `HTTP ${page.http_status}`,
                  deviceLabel(t, page.device_profile),
                  page.response_ms === null ? null : `${page.response_ms} ms`,
                  page.render_ms === null ? null : t("website.renderMs", { ms: page.render_ms }),
                  page.html_bytes === null ? null : formatBytes(page.html_bytes),
                  page.words === null ? null : t("website.words", { n: page.words }),
                ]
                  .filter((part) => part !== null)
                  .join(" · ")}
              </p>
              {!page.analyzed && page.gap && (
                <p className="text-[11px] text-warn">
                  {t("website.pageGap", { gap: gapLabel(t, page.gap) })}
                </p>
              )}
            </li>
          ))}
        </ul>
      </div>

      <div className="space-y-1">
        <Heading>{t("website.comparison")}</Heading>
        {diff === null ? (
          <p className="text-muted-foreground">{t("website.noPrevious")}</p>
        ) : (
          <div className="space-y-1">
            <p className="text-[11px] text-muted-foreground">
              {t("website.comparedWith", { time: when(diff.previous_created_at) })}
            </p>
            {diff.ruleset_changed && (
              <InlineNotice variant="partial">{t("website.rulesChanged")}</InlineNotice>
            )}
            {diff.domain_changed && (
              <InlineNotice variant="partial">
                {t("website.domainChanged", {
                  before: diff.final_url_before ?? "—",
                  after: diff.final_url_after ?? "—",
                })}
              </InlineNotice>
            )}
            {diff.subscores.length === 0 && diff.findings.length === 0 && changes.length === 0 && (
              <p>{t("website.noChanges")}</p>
            )}
            {diff.subscores.length > 0 && (
              <ul className="space-y-0.5">
                {diff.subscores.map((change) => (
                  <li key={change.category}>
                    {t("website.scoreChange", {
                      category: t(CATEGORY_KEYS[change.category]),
                      before: score(change.before),
                      after: score(change.after),
                    })}
                  </li>
                ))}
              </ul>
            )}
            {diff.findings.length > 0 && (
              <ul className="space-y-0.5">
                {diff.findings.map((change) => (
                  <li key={change.rule_id} className="flex min-w-0 flex-wrap gap-x-2">
                    <span className="font-mono">{change.rule_id}</span>
                    <span>
                      {t("website.findingChange", {
                        before: resultOf(change.before),
                        after: resultOf(change.after),
                      })}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {changes.map((change) => (
              <p key={change.label} className="break-words">
                {change.label}: <span className="font-mono">{change.items.join(", ")}</span>
              </p>
            ))}
          </div>
        )}
      </div>

      {detail.history.length > 1 && (
        <div className="space-y-1">
          <Heading>{t("website.history")}</Heading>
          <ul className="space-y-0.5">
            {detail.history.map((item) => (
              <li key={item.id} className="flex flex-wrap items-center gap-2 text-[11px]">
                <span className={cn(item.id === audit.id && "font-medium")}>
                  {t("website.historyLine", {
                    time: when(item.created_at),
                    status: t(AUDIT_STATUS_KEYS[item.status]),
                    rules: item.ruleset_version,
                    failed: item.failed,
                    warnings: item.warnings,
                  })}
                </span>
                {item.id !== audit.id && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 px-1.5"
                    onClick={() => onOpen(item.id)}
                  >
                    {t("website.open")}
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
