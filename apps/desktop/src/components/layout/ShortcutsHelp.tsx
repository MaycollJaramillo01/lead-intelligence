import { findNavItem } from "@/app/navigation";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useT } from "@/lib/i18n";
import { formatCombo, SHORTCUTS, type ShortcutGroup } from "@/lib/shortcuts";
import { useUiStore } from "@/stores/ui";

const GROUPS: {
  id: ShortcutGroup;
  titleKey: "shortcuts.group.global" | "shortcuts.group.navigation" | "shortcuts.group.view";
}[] = [
  { id: "global", titleKey: "shortcuts.group.global" },
  { id: "navigation", titleKey: "shortcuts.group.navigation" },
  { id: "view", titleKey: "shortcuts.group.view" },
];

export function ShortcutsHelp() {
  const t = useT();
  const open = useUiStore((state) => state.shortcutsHelpOpen);
  const setOpen = useUiStore((state) => state.setShortcutsHelpOpen);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("shortcuts.title")}</DialogTitle>
          <DialogDescription>{t("shortcuts.description")}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          {GROUPS.map((group) => (
            <section key={group.id}>
              <h3 className="mb-1 text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
                {t(group.titleKey)}
              </h3>
              <ul className="divide-y divide-border rounded-md border">
                {SHORTCUTS.filter((shortcut) => shortcut.group === group.id).map((shortcut) => {
                  const target = shortcut.target ? findNavItem(shortcut.target) : undefined;
                  return (
                    <li
                      key={shortcut.id}
                      className="flex items-center justify-between gap-4 px-3 py-1.5 text-xs"
                    >
                      <span>
                        {t(
                          shortcut.descriptionKey,
                          target ? { target: t(target.titleKey) } : undefined,
                        )}
                      </span>
                      <span className="flex gap-0.5">
                        {formatCombo(shortcut.combo).map((key) => (
                          <kbd
                            key={key}
                            className="rounded border bg-muted px-1.5 font-mono text-[10px] leading-5"
                          >
                            {key}
                          </kbd>
                        ))}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
