import { useState } from "react";
import type {
  BulkAction,
  BulkMode,
  BulkRequest,
  BulkResult,
  ExplorerQuery,
} from "@lead/shared-types";
import { toast } from "sonner";
import { InlineNotice } from "@/components/states/StateBlock";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { BULK_ACTIONS, BULK_ACTION_KEYS, codeText } from "@/features/leads/labels";
import { useT } from "@/lib/i18n";
import { desktop, toIpcError } from "@/lib/ipc";

/** The leads a bulk action touches: the ones selected by id, or every lead a query matches. */
export type BulkTarget =
  { kind: "ids"; ids: string[] } | { kind: "query"; query: ExplorerQuery; total: number };

interface BulkDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  target: BulkTarget;
  onApplied: () => void;
}

const SELECT = "h-7 w-full rounded-md border bg-transparent px-2 text-xs";

/**
 * A safe action over many leads (phase 12): previewed first, then applied with a reason, one audited
 * change per lead. Over a query, the apply names the count the preview showed, so it is refused if
 * the leads changed in between. Sales-ready is never offered here.
 */
export function BulkDialog({ open, onOpenChange, target, onApplied }: BulkDialogProps) {
  const t = useT();
  const [action, setAction] = useState<BulkAction>("add_tag");
  const [tag, setTag] = useState("");
  const [assignee, setAssignee] = useState("");
  const [reason, setReason] = useState("");
  const [result, setResult] = useState<BulkResult | null>(null);
  const [busy, setBusy] = useState(false);

  const needsTag = action === "add_tag" || action === "remove_tag";
  const needsAssignee = action === "assign";
  const complete = (!needsTag || tag.trim() !== "") && (!needsAssignee || assignee.trim() !== "");
  const previewed = result?.mode === "preview";

  const request = (mode: BulkMode): BulkRequest => ({
    mode,
    action,
    business_ids: target.kind === "ids" ? target.ids : [],
    query: target.kind === "query" ? target.query : null,
    expected: mode === "apply" && target.kind === "query" ? (result?.targeted ?? null) : null,
    tag: needsTag ? tag.trim() : null,
    assignee: needsAssignee ? assignee.trim() : null,
    reason: mode === "apply" ? reason.trim() : null,
  });

  const run = async (mode: BulkMode) => {
    setBusy(true);
    try {
      const outcome = await desktop.bulkLeads(request(mode));
      setResult(outcome);
      if (mode === "apply") {
        toast.success(t("bulk.done"));
        onApplied();
      }
    } catch (cause) {
      toast.error(t("bulk.failed"), { description: toIpcError(cause, "bulk_leads").message });
    } finally {
      setBusy(false);
    }
  };

  // any change of what the action does asks for a new preview
  const edit = (apply: () => void) => {
    apply();
    setResult(null);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-auto sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("bulk.title")}</DialogTitle>
          <DialogDescription>{t("bulk.help")}</DialogDescription>
        </DialogHeader>
        <div className="space-y-2 text-xs">
          <p className="font-medium">
            {target.kind === "ids"
              ? t("bulk.byIds", { n: target.ids.length })
              : t("bulk.byQuery", { total: target.total })}
          </p>
          <InlineNotice variant="blocked">{t("bulk.noSalesReady")}</InlineNotice>
          <div className="space-y-0.5">
            <Label htmlFor="bulk-action" className="text-xs">
              {t("bulk.action")}
            </Label>
            <select
              id="bulk-action"
              value={action}
              onChange={(event) => edit(() => setAction(event.target.value as BulkAction))}
              className={SELECT}
            >
              {BULK_ACTIONS.map((value) => (
                <option key={value} value={value}>
                  {t(BULK_ACTION_KEYS[value])}
                </option>
              ))}
            </select>
          </div>
          {needsTag && (
            <div className="space-y-0.5">
              <Label htmlFor="bulk-tag" className="text-xs">
                {t("bulk.tag")}
              </Label>
              <Input
                id="bulk-tag"
                value={tag}
                maxLength={64}
                onChange={(event) => edit(() => setTag(event.target.value))}
                className="h-7 text-xs"
              />
            </div>
          )}
          {needsAssignee && (
            <div className="space-y-0.5">
              <Label htmlFor="bulk-assignee" className="text-xs">
                {t("bulk.assignee")}
              </Label>
              <Input
                id="bulk-assignee"
                value={assignee}
                maxLength={120}
                onChange={(event) => edit(() => setAssignee(event.target.value))}
                className="h-7 text-xs"
              />
            </div>
          )}
          <div className="space-y-0.5">
            <Label htmlFor="bulk-reason" className="text-xs">
              {t("bulk.reason")}
            </Label>
            <Textarea
              id="bulk-reason"
              value={reason}
              maxLength={2000}
              onChange={(event) => setReason(event.target.value)}
              className="min-h-12 text-xs"
            />
          </div>
          {result && (
            <div className="space-y-1 rounded border p-2" role="status">
              <p className="font-medium">
                {t(result.mode === "preview" ? "bulk.previewLine" : "bulk.appliedLine", {
                  changed: result.changed,
                  skipped: result.skipped,
                  targeted: result.targeted,
                })}
              </p>
              {result.skips.length > 0 && (
                <ul className="max-h-32 space-y-0.5 overflow-auto text-[11px] text-muted-foreground">
                  {result.skips.map((skip) => (
                    <li key={skip.business_id}>
                      {`${skip.display_name} · ${codeText(t, "bulk.skip", skip.reason)}`}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          <div className="flex justify-end gap-1">
            <Button
              variant="outline"
              size="sm"
              className="h-7"
              disabled={busy || !complete}
              onClick={() => void run("preview")}
            >
              {t("bulk.preview")}
            </Button>
            <Button
              size="sm"
              className="h-7"
              disabled={busy || !complete || !previewed || !reason.trim() || result.changed === 0}
              onClick={() => void run("apply")}
            >
              {t("bulk.apply")}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
