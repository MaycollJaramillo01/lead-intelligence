import { useCallback, useEffect, useState, type ReactNode } from "react";
import type {
  BriefSection,
  BusinessDetail,
  CanonicalField,
  CanonicalValue,
  Provenance,
  SalesBrief,
} from "@lead/shared-types";
import { ChevronRight } from "lucide-react";
import { Link } from "react-router";
import { toast } from "sonner";
import { EvidenceItem } from "@/components/domain/EvidenceItem";
import { InlineNotice, StateBlock } from "@/components/states/StateBlock";
import { Text, TestDataBadge } from "@/components/data/cells";
import { Button } from "@/components/ui/button";
import { BUSINESS_STATUS_KEYS } from "@/features/businesses/columns";
import { loadBusiness } from "@/features/businesses/prefetch";
import { AiPanel } from "@/features/ai/AiPanel";
import { ContactPanel } from "@/features/contacts/ContactPanel";
import { LatinoPanel } from "@/features/latino/LatinoPanel";
import { BriefClaims, BriefDraftPanel, BriefSummary } from "@/features/leads/BriefPanel";
import { CORRECTABLE_FIELD_KEYS } from "@/features/leads/labels";
import { ResearchPanel } from "@/features/leads/ResearchPanel";
import { TimelinePanel } from "@/features/leads/TimelinePanel";
import { ScorecardPanel } from "@/features/scoring/ScorecardPanel";
import { WebsiteAuditPanel } from "@/features/website/WebsiteAuditPanel";
import { formatDateTime, useLanguage, useT, type TranslationKey } from "@/lib/i18n";
import { desktop, toIpcError, type IpcError } from "@/lib/ipc";
import { sourcePath } from "@/lib/links";
import { useLoaded } from "@/lib/useLoaded";
import { cn } from "@/lib/utils";

type DetailState = { detail: BusinessDetail; error: null } | { detail: null; error: IpcError };
type Layout = "panel" | "page";

/** The inspector's sections, in order: the side panel and the full view show the same ones. */
export const BUSINESS_SECTIONS = [
  { id: "summary", key: "business.section.summary" },
  { id: "scorecard", key: "business.section.scorecard" },
  { id: "why", key: "business.section.why" },
  { id: "latino", key: "business.section.latino" },
  { id: "website", key: "business.section.website" },
  { id: "contacts", key: "business.section.contacts" },
  { id: "timeline", key: "business.section.timeline" },
  { id: "evidence", key: "business.section.evidence" },
  { id: "conflicts", key: "business.section.conflicts" },
  { id: "offer", key: "business.section.offer" },
  { id: "research", key: "business.section.research" },
] as const satisfies ReadonlyArray<{ id: string; key: TranslationKey }>;

type SectionId = (typeof BUSINESS_SECTIONS)[number]["id"];

/** Open when the side panel shows a business; the others are read only once opened. */
const OPEN_IN_PANEL: readonly SectionId[] = [
  "summary",
  "scorecard",
  "latino",
  "contacts",
  "evidence",
];
/** Sections that read the sales brief. */
const BRIEF_READERS: readonly SectionId[] = ["why", "conflicts", "offer"];
const WHY: readonly BriefSection[] = [
  "business_snapshot",
  "icp_match",
  "verified_opportunity",
  "contact_route",
  "decision_maker",
  "conversation_hooks",
  "last_verified",
];

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

/** The site a website audit is filed under: host and port without `www.`, as the worker has it. */
function siteOf(url: string | null): string | null {
  if (!url) return null;
  try {
    const host = new URL(url.includes("://") ? url : `https://${url}`).host;
    return host.replace(/^www\./, "") || null;
  } catch {
    return null;
  }
}

/** The newest audit of the business's website, with its findings and snapshots. */
function WebsiteSection({ projectId, url }: { projectId: string; url: string | null }) {
  const t = useT();
  const site = siteOf(url);
  const [opened, setOpened] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fetchAudits = useCallback(
    () =>
      site === null
        ? Promise.resolve(null)
        : desktop.listWebsiteAudits({ project_id: projectId, site, limit: 1, cursor: null }),
    [projectId, site],
  );
  const { data, error } = useLoaded(fetchAudits, "list_website_audits");

  if (site === null) {
    return <p className="text-xs text-muted-foreground">{t("business.noWebsite")}</p>;
  }
  if (error) {
    return (
      <StateBlock
        variant={error.code === "desktop_unavailable" ? "offline" : "error"}
        title={t("inspector.errorTitle")}
        body={error.message}
      />
    );
  }
  if (data === null) return <StateBlock variant="loading" title={t("state.loading")} />;
  const [latest] = data.items;
  if (!latest) return <p className="text-xs text-muted-foreground">{t("business.noAudit")}</p>;

  const auditAgain = async (project: string, target: string) => {
    setBusy(true);
    try {
      await desktop.startWebsiteAudit({ project_id: project, sites: [target] });
      toast.success(t("business.auditRequested"));
    } catch (cause) {
      toast.error(t("business.auditFailed"), {
        description: toIpcError(cause, "start_website_audit").message,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <WebsiteAuditPanel
      auditId={opened ?? latest.id}
      busy={busy}
      onOpen={setOpened}
      onAuditAgain={(project, target) => void auditAgain(project, target)}
    />
  );
}

/** Fields whose records disagree, and what the brief says is still unknown. */
function Conflicts({
  canonical,
  brief,
  onPinned,
}: {
  canonical: CanonicalValue[];
  brief: SalesBrief | null;
  onPinned: () => void;
}) {
  const t = useT();
  const contested = canonical.filter(
    (value) => value.superseded_at === null && value.alternative_count > 1,
  );
  return (
    <div className="space-y-2 text-xs">
      {contested.length === 0 ? (
        <p className="text-muted-foreground">{t("business.noConflicts")}</p>
      ) : (
        <ul className="space-y-0.5">
          {contested.map((value) => (
            <li key={value.id} className="rounded border border-warn/60 px-1.5 py-0.5 break-words">
              {t("business.conflictLine", {
                field: t(CORRECTABLE_FIELD_KEYS[value.field]),
                n: value.alternative_count,
                value: value.value,
              })}
            </li>
          ))}
        </ul>
      )}
      {brief && <BriefClaims brief={brief} sections={["risks_unknowns"]} onPinned={onPinned} />}
    </div>
  );
}

function Part({
  id,
  title,
  layout,
  open,
  onToggle,
  children,
}: {
  id: SectionId;
  title: string;
  layout: Layout;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  const titleId = `business-${id}-title`;
  if (layout === "page") {
    return (
      <section
        id={`business-${id}`}
        aria-labelledby={titleId}
        className="scroll-mt-4 space-y-2 rounded-md border bg-card p-3"
      >
        <h2 id={titleId} tabIndex={-1} className="text-sm font-medium outline-none">
          {title}
        </h2>
        {children}
      </section>
    );
  }
  return (
    <section aria-labelledby={titleId} className="space-y-2 border-t pt-2">
      <h3 id={titleId} className="text-xs font-medium">
        <button
          type="button"
          aria-expanded={open}
          onClick={onToggle}
          className="flex w-full items-center gap-1 text-left"
        >
          <ChevronRight
            aria-hidden="true"
            className={cn("size-3.5 shrink-0 transition-transform", open && "rotate-90")}
          />
          {title}
        </button>
      </h3>
      {open && children}
    </section>
  );
}

/**
 * A business with everything known about it (phases 03–12), in eleven sections: summary and
 * sources, scorecard, why this lead (facts apart from inferences), Latino evidence, website,
 * contacts and suppressions, timeline, evidence, conflicts and unknowns, offer, and the research
 * workspace. In the side panel a folded section reads nothing until it opens.
 */
export function BusinessInspector({
  businessId,
  layout = "panel",
}: {
  businessId: string;
  layout?: Layout;
}) {
  const t = useT();
  const language = useLanguage();
  const [state, setState] = useState<DetailState | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<ReadonlySet<SectionId>>(
    () =>
      new Set<SectionId>(
        layout === "page" ? BUSINESS_SECTIONS.map((section) => section.id) : OPEN_IN_PANEL,
      ),
  );
  const [researchKey, setResearchKey] = useState(0);

  // the panel remounts per selection, so a late answer is simply dropped
  useEffect(() => {
    let current = true;
    loadBusiness(businessId).then(
      (detail) => current && setState({ detail, error: null }),
      (cause: unknown) =>
        current && setState({ detail: null, error: toIpcError(cause, "get_business") }),
    );
    return () => {
      current = false;
    };
  }, [businessId]);

  const readsBrief = layout === "page" || BRIEF_READERS.some((id) => open.has(id));
  const fetchBrief = useCallback(
    () =>
      readsBrief
        ? desktop.getSalesBrief({ business_id: businessId, score_run_id: null })
        : Promise.resolve(null),
    [readsBrief, businessId],
  );
  const {
    data: brief,
    error: briefError,
    reload: reloadBrief,
  } = useLoaded(fetchBrief, "get_sales_brief");

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
  // Latino evidence is shown with its tracks, factors and history by its own panel
  const evidence = detail.evidence.filter(
    ({ provenance }) => !provenance.extractor.startsWith("latino."),
  );
  const sources = [
    ...new Set(
      [
        ...detail.names,
        ...detail.locations,
        ...detail.websites,
        ...detail.contacts,
        ...detail.classifications,
        ...detail.evidence,
      ].map(({ provenance }) => provenance.connector_key),
    ),
  ];
  const website =
    detail.canonical.find((value) => value.field === "website" && value.superseded_at === null)
      ?.value ??
    detail.websites[0]?.value.url_normalized ??
    null;
  const pinned = () => setResearchKey((key) => key + 1);
  const toggle = (id: SectionId) =>
    setOpen((current) => {
      const next = new Set(current);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  const briefPart = (render: (value: SalesBrief) => ReactNode) =>
    briefError ? (
      <StateBlock
        variant={briefError.code === "desktop_unavailable" ? "offline" : "error"}
        title={t("brief.loadError")}
        body={briefError.message}
        action={
          <Button variant="outline" size="sm" onClick={() => void reloadBrief()}>
            {t("common.retry")}
          </Button>
        }
      />
    ) : brief ? (
      render(brief)
    ) : (
      <StateBlock variant="loading" title={t("state.loading")} />
    );

  const content: Record<SectionId, ReactNode> = {
    summary: (
      <div className="space-y-3">
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
          render={(site) => (
            <RawAndNormalized raw={site.url_raw} normalized={site.url_normalized} />
          )}
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
        {sources.length > 0 && (
          <p className="flex flex-wrap items-center gap-x-2 text-[11px]">
            <span className="text-muted-foreground">{t("business.link.sources")}</span>
            {sources.map((key) => (
              <Link
                key={key}
                to={sourcePath(key)}
                className="font-mono text-primary hover:underline"
              >
                {key}
              </Link>
            ))}
          </p>
        )}
      </div>
    ),
    scorecard: <ScorecardPanel businessId={business.id} />,
    why: briefPart((value) => (
      <div className="space-y-2">
        <BriefSummary brief={value} />
        <BriefClaims brief={value} sections={WHY} split onPinned={pinned} />
        <BriefDraftPanel brief={value} onChanged={() => void reloadBrief()} />
      </div>
    )),
    latino: <LatinoPanel businessId={business.id} />,
    website: <WebsiteSection projectId={business.project_id} url={website} />,
    contacts: <ContactPanel businessId={business.id} />,
    timeline: <TimelinePanel businessId={business.id} />,
    evidence: (
      <div className="space-y-2">
        {brief && <BriefClaims brief={brief} sections={["evidence"]} onPinned={pinned} />}
        {evidence.length > 0 ? (
          <section className="space-y-1.5">
            <Heading>
              {t("inspector.evidence")} ({evidence.length})
            </Heading>
            {evidence.map(({ value, provenance }) => (
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
        ) : (
          !brief && <p className="text-xs text-muted-foreground">{t("business.noClaims")}</p>
        )}
      </div>
    ),
    conflicts: <Conflicts canonical={detail.canonical} brief={brief} onPinned={pinned} />,
    offer: briefPart((value) => (
      <BriefClaims brief={value} sections={["recommended_offer"]} onPinned={pinned} />
    )),
    research: (
      <div className="space-y-3">
        <ResearchPanel key={researchKey} businessId={business.id} />
        <AiPanel businessId={business.id} />
      </div>
    ),
  };
  const Title = layout === "page" ? "h1" : "h3";

  return (
    <div className="space-y-3">
      <header className="space-y-1">
        <div className="flex items-start justify-between gap-2">
          <Title
            className={cn("font-medium break-words", layout === "page" ? "text-lg" : "text-sm")}
          >
            {business.display_name}
          </Title>
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
      <div className={layout === "page" ? "space-y-3" : "space-y-2"}>
        {BUSINESS_SECTIONS.map((section) => (
          <Part
            key={section.id}
            id={section.id}
            title={t(section.key)}
            layout={layout}
            open={open.has(section.id)}
            onToggle={() => toggle(section.id)}
          >
            {content[section.id]}
          </Part>
        ))}
      </div>
    </div>
  );
}
