import type { ViewColumn } from "@lead/shared-types";
import { ArrowDown, ArrowUp, Pin, PinOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { FIELD_KEYS } from "@/features/leads/labels";
import { moveColumn, toggleColumn, togglePin } from "@/features/leads/query";
import { useT } from "@/lib/i18n";

interface ColumnsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  columns: ViewColumn[];
  onChange: (columns: ViewColumn[]) => void;
}

/** Which columns a view shows, in which order, and which stay pinned on the left. */
export function ColumnsDialog({ open, onOpenChange, columns, onChange }: ColumnsDialogProps) {
  const t = useT();
  const visible = columns.filter((column) => column.visible).length;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[80vh] overflow-auto sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("columns.title")}</DialogTitle>
          <DialogDescription>{t("columns.help")}</DialogDescription>
        </DialogHeader>
        <ul className="space-y-0.5 text-xs">
          {columns.map((column, index) => {
            const label = t(FIELD_KEYS[column.field]);
            return (
              <li
                key={column.field}
                className="flex items-center gap-1 rounded px-1 hover:bg-accent/50"
              >
                <label className="flex min-w-0 flex-1 items-center gap-2 py-1">
                  <input
                    type="checkbox"
                    checked={column.visible}
                    // the last visible column stays: a view always shows something
                    disabled={column.visible && visible === 1}
                    onChange={() => onChange(toggleColumn(columns, column.field))}
                    aria-label={t("columns.show", { field: label })}
                  />
                  <span className="truncate">{label}</span>
                </label>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-6"
                  aria-pressed={column.pinned}
                  aria-label={t("columns.pin", { field: label })}
                  onClick={() => onChange(togglePin(columns, column.field))}
                >
                  {column.pinned ? (
                    <Pin aria-hidden="true" className="size-3.5" />
                  ) : (
                    <PinOff aria-hidden="true" className="size-3.5 opacity-50" />
                  )}
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-6"
                  disabled={index === 0}
                  aria-label={t("columns.up", { field: label })}
                  onClick={() => onChange(moveColumn(columns, column.field, -1))}
                >
                  <ArrowUp aria-hidden="true" className="size-3.5" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-6"
                  disabled={index === columns.length - 1}
                  aria-label={t("columns.down", { field: label })}
                  onClick={() => onChange(moveColumn(columns, column.field, 1))}
                >
                  <ArrowDown aria-hidden="true" className="size-3.5" />
                </Button>
              </li>
            );
          })}
        </ul>
      </DialogContent>
    </Dialog>
  );
}
