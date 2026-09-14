import { useCallback, useEffect, useState, type FormEvent } from "react";
import type { SavedViewAction, SavedViewItem, ViewDefinition } from "@lead/shared-types";
import { toast } from "sonner";
import { StateBlock } from "@/components/states/StateBlock";
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
import { formatDateTime, useLanguage, useT } from "@/lib/i18n";
import { desktop, toIpcError, type IpcError } from "@/lib/ipc";
import { cn } from "@/lib/utils";

interface ViewsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** What the explorer shows now, saved as a view or as a new version of the active one. */
  definition: ViewDefinition;
  activeViewId: string | null;
  onApply: (item: SavedViewItem) => void;
  onSaved: (item: SavedViewItem) => void;
}

type Listed = { items: SavedViewItem[]; error: IpcError | null } | null;

/**
 * Saved views (phase 12): apply one, save what the explorer shows as a new view or as a new version
 * of the active one, and archive or restore a view with a reason. A view saved with an older schema
 * says it was upgraded when read.
 */
export function ViewsDialog({
  open,
  onOpenChange,
  definition,
  activeViewId,
  onApply,
  onSaved,
}: ViewsDialogProps) {
  const t = useT();
  const language = useLanguage();
  const [archived, setArchived] = useState(false);
  const [listed, setListed] = useState<Listed>(null);
  const [name, setName] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(
    () =>
      desktop.listSavedViews({ scope: "lead_explorer", include_archived: archived }).then(
        (list) => setListed({ items: list.items, error: null }),
        (cause: unknown) => setListed({ items: [], error: toIpcError(cause, "list_saved_views") }),
      ),
    [archived],
  );

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const active = listed?.items.find((item) => item.view.id === activeViewId) ?? null;

  const save = async (viewId: string | null, viewName: string, description: string | null) => {
    setBusy(true);
    try {
      const item = await desktop.saveSavedView({
        view_id: viewId,
        scope: "lead_explorer",
        name: viewName.trim(),
        description,
        definition,
      });
      toast.success(t("views.saved"));
      setName("");
      onSaved(item);
      await load();
    } catch (cause) {
      toast.error(t("views.failed"), {
        description: toIpcError(cause, "save_saved_view").message,
      });
    } finally {
      setBusy(false);
    }
  };

  const change = async (item: SavedViewItem, action: SavedViewAction) => {
    if (!reason.trim()) return;
    setBusy(true);
    try {
      await desktop.changeSavedView({ view_id: item.view.id, action, reason: reason.trim() });
      toast.success(t("views.changed"));
      setReason("");
      await load();
    } catch (cause) {
      toast.error(t("views.failed"), {
        description: toIpcError(cause, "change_saved_view").message,
      });
    } finally {
      setBusy(false);
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (name.trim()) void save(null, name, null);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("views.title")}</DialogTitle>
          <DialogDescription>{t("views.help")}</DialogDescription>
        </DialogHeader>
        <div className="space-y-2 text-xs">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={archived}
              onChange={(event) => setArchived(event.target.checked)}
            />
            {t("views.showArchived")}
          </label>
          {listed === null ? (
            <StateBlock variant="loading" title={t("state.loading")} />
          ) : listed.error ? (
            <StateBlock
              variant={listed.error.code === "desktop_unavailable" ? "offline" : "error"}
              title={t("views.loadError")}
              body={listed.error.message}
            />
          ) : listed.items.length === 0 ? (
            <p className="text-muted-foreground">{t("views.empty")}</p>
          ) : (
            <ul className="space-y-1">
              {listed.items.map((item) => (
                <li
                  key={item.view.id}
                  className={cn(
                    "space-y-1 rounded border p-2",
                    item.view.id === activeViewId && "border-primary/60",
                  )}
                >
                  <p className="flex flex-wrap items-center gap-2">
                    <span className="font-medium break-words">{item.view.name}</span>
                    <span className="text-muted-foreground">
                      {t("views.versions", { n: item.versions.length })}
                    </span>
                    {item.upgraded_from !== null && (
                      <span className="rounded border border-warn/60 px-1 text-[10px] text-warn">
                        {t("views.upgraded", { version: item.upgraded_from })}
                      </span>
                    )}
                    {item.view.archived_at && (
                      <span className="rounded border px-1 text-[10px] text-muted-foreground">
                        {t("views.archivedBadge")}
                      </span>
                    )}
                  </p>
                  <p className="text-[11px] text-muted-foreground">
                    {formatDateTime(item.view.updated_at, language)}
                  </p>
                  <div className="flex gap-1">
                    {!item.view.archived_at && (
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-6"
                        onClick={() => onApply(item)}
                      >
                        {t("views.apply")}
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-6"
                      disabled={busy || !reason.trim()}
                      onClick={() =>
                        void change(item, item.view.archived_at ? "restore" : "archive")
                      }
                    >
                      {t(item.view.archived_at ? "views.restore" : "views.archive")}
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
          <div className="space-y-0.5">
            <Label htmlFor="view-reason" className="text-xs">
              {t("views.reason")}
            </Label>
            <Input
              id="view-reason"
              value={reason}
              maxLength={2000}
              onChange={(event) => setReason(event.target.value)}
              className="h-7 text-xs"
            />
          </div>
          <form onSubmit={submit} className="flex items-end gap-1">
            <div className="min-w-0 flex-1 space-y-0.5">
              <Label htmlFor="view-name" className="text-xs">
                {t("views.name")}
              </Label>
              <Input
                id="view-name"
                value={name}
                maxLength={120}
                onChange={(event) => setName(event.target.value)}
                className="h-7 text-xs"
              />
            </div>
            <Button type="submit" size="sm" className="h-7" disabled={busy || !name.trim()}>
              {t("views.saveNew")}
            </Button>
          </form>
          {active && !active.view.archived_at && (
            <Button
              size="sm"
              variant="outline"
              className="h-7"
              disabled={busy}
              onClick={() => void save(active.view.id, active.view.name, active.view.description)}
            >
              {t("views.saveVersion")}
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
