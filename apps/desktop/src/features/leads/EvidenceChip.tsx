import { useId, useState } from "react";
import type { BriefRef, EvidenceDetail } from "@lead/shared-types";
import { ExternalLink, Pin } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { EVIDENCE_KIND_KEYS } from "@/features/leads/labels";
import { researchRequest } from "@/features/leads/research";
import { percent } from "@/features/scoring/labels";
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop, toIpcError, type IpcError } from "@/lib/ipc";
import { cn } from "@/lib/utils";

type Loaded = { detail: EvidenceDetail; error: null } | { detail: null; error: IpcError };

interface EvidenceChipProps {
  businessId: string;
  evidence: BriefRef;
  /** When given, the opened row can be pinned to the lead's research. */
  onPinned?: () => void;
}

/**
 * The stored row behind a claim (phase 12): its label, and on demand the row itself, looked up only
 * within the business it belongs to. Nothing is read until the analyst opens it.
 */
export function EvidenceChip({ businessId, evidence, onPinned }: EvidenceChipProps) {
  const t = useT();
  const language = useLanguage();
  const region = useId();
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [busy, setBusy] = useState(false);

  const toggle = () => {
    setOpen(!open);
    if (open || loaded !== null) return;
    desktop
      .getEvidenceDetail({ business_id: businessId, kind: evidence.kind, ref: evidence.ref })
      .then(
        (detail) => setLoaded({ detail, error: null }),
        (cause: unknown) =>
          setLoaded({ detail: null, error: toIpcError(cause, "get_evidence_detail") }),
      );
  };

  const pin = async () => {
    setBusy(true);
    try {
      await desktop.actOnResearch(
        researchRequest(businessId, "pin", { ref_kind: evidence.kind, ref: evidence.ref }),
      );
      toast.success(t("research.saved"));
      onPinned?.();
    } catch (cause) {
      toast.error(t("research.failed"), {
        description: toIpcError(cause, "act_on_research").message,
      });
    } finally {
      setBusy(false);
    }
  };

  const detail = loaded?.detail ?? null;
  const facts = detail
    ? [
        detail.status && t("evidence.status", { status: detail.status }),
        detail.confidence !== null && `${t("common.confidence")} ${percent(detail.confidence)}`,
        detail.observed_at &&
          t("evidence.observed", { time: formatDateTime(detail.observed_at, language) }),
        detail.source_name && t("evidence.from", { source: detail.source_name }),
      ].filter(Boolean)
    : [];

  return (
    <div className="max-w-full">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={region}
        title={t(EVIDENCE_KIND_KEYS[evidence.kind])}
        onClick={toggle}
        className={cn(
          "max-w-full truncate rounded border px-1 text-left font-mono text-[10px] text-muted-foreground hover:text-foreground",
          open && "border-primary/60 text-foreground",
        )}
      >
        <span className="sr-only">{t("evidence.open", { label: evidence.label })}</span>
        <span aria-hidden="true">{evidence.label}</span>
      </button>
      {open && (
        <div id={region} className="mt-1 space-y-0.5 rounded-md border bg-card p-1.5 text-[11px]">
          {loaded === null ? (
            <p className="text-muted-foreground">{t("state.loading")}</p>
          ) : detail === null ? (
            <p role="alert" className="text-danger">
              {`${t("evidence.loadError")}: ${loaded.error?.message ?? ""}`}
            </p>
          ) : (
            <>
              <p className="break-words">
                <span className="font-medium">{detail.label}</span>
                {` · ${t(EVIDENCE_KIND_KEYS[detail.kind])}`}
              </p>
              {detail.excerpt && (
                <blockquote className="border-l-2 pl-2 break-words">{detail.excerpt}</blockquote>
              )}
              {facts.length > 0 && <p className="text-muted-foreground">{facts.join(" · ")}</p>}
              {detail.provenance && (
                <p className="font-mono text-[10px] break-all text-muted-foreground">
                  {`${detail.provenance.connector_key} · ${detail.provenance.extractor} ${detail.provenance.extractor_version} · ${formatDateTime(detail.provenance.acquired_at, language)}`}
                </p>
              )}
              <div className="flex flex-wrap items-center gap-2">
                {detail.source_url && (
                  <a
                    href={detail.source_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-primary underline-offset-2 hover:underline"
                  >
                    <ExternalLink aria-hidden="true" className="size-3" />
                    {t("evidence.openSource")}
                  </a>
                )}
                {onPinned && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-6"
                    disabled={busy}
                    onClick={() => void pin()}
                  >
                    <Pin aria-hidden="true" />
                    {t("research.pin")}
                  </Button>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
