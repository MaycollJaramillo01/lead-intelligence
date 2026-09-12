import { useNavigate } from "react-router";
import { Monitor, Moon, Rows3, Sun, type LucideIcon } from "lucide-react";
import { toast } from "sonner";
import { NAV_ITEMS, NAV_SECTIONS } from "@/app/navigation";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from "@/components/ui/command";
import { useDiagnosticsStore } from "@/features/diagnostics/store";
import { useT } from "@/lib/i18n";
import { formatCombo, SHORTCUTS } from "@/lib/shortcuts";
import { usePreferences } from "@/stores/preferences";
import { useUiStore } from "@/stores/ui";

function comboFor(id: string): string | undefined {
  return SHORTCUTS.find((shortcut) => shortcut.id === id)?.combo;
}

function Shortcut({ id }: { id: string }) {
  const combo = comboFor(id);
  if (!combo) return null;
  return <CommandShortcut>{formatCombo(combo).join(" ")}</CommandShortcut>;
}

/** Only real, existing actions live here: navigation, diagnostics and appearance. */
export function CommandPalette() {
  const t = useT();
  const navigate = useNavigate();
  const open = useUiStore((state) => state.commandPaletteOpen);
  const openPalette = useUiStore((state) => state.openCommandPalette);
  const closePalette = useUiStore((state) => state.closeCommandPalette);
  const toggleInspector = useUiStore((state) => state.toggleInspector);
  const toggleShortcutsHelp = useUiStore((state) => state.toggleShortcutsHelp);
  const toggleSidebar = usePreferences((state) => state.toggleSidebar);
  const setTheme = usePreferences((state) => state.setTheme);
  const setDensity = usePreferences((state) => state.setDensity);
  const setLanguage = usePreferences((state) => state.setLanguage);
  const refresh = useDiagnosticsStore((state) => state.refresh);
  const restartWorker = useDiagnosticsStore((state) => state.restartWorker);

  const run = (action: () => void | Promise<void>) => {
    closePalette();
    void action();
  };

  const refreshDiagnostics = async () => {
    await refresh();
    const { error } = useDiagnosticsStore.getState();
    if (error)
      toast.error(t("toast.actionFailed", { action: t("common.refresh") }), {
        description: error.message,
      });
    else toast.success(t("toast.systemRefreshed"));
  };

  const restart = async () => {
    await restartWorker();
    const { error } = useDiagnosticsStore.getState();
    if (error)
      toast.error(t("toast.actionFailed", { action: t("nav.system") }), {
        description: error.message,
      });
    else toast.success(t("toast.workerRestarted"));
  };

  const appearance: { id: string; label: string; icon: LucideIcon; action: () => void }[] = [
    {
      id: "theme-system",
      label: `${t("common.theme")}: ${t("theme.system")}`,
      icon: Monitor,
      action: () => setTheme("system"),
    },
    {
      id: "theme-light",
      label: `${t("common.theme")}: ${t("theme.light")}`,
      icon: Sun,
      action: () => setTheme("light"),
    },
    {
      id: "theme-dark",
      label: `${t("common.theme")}: ${t("theme.dark")}`,
      icon: Moon,
      action: () => setTheme("dark"),
    },
    {
      id: "density-comfortable",
      label: `${t("common.density")}: ${t("density.comfortable")}`,
      icon: Rows3,
      action: () => setDensity("comfortable"),
    },
    {
      id: "density-compact",
      label: `${t("common.density")}: ${t("density.compact")}`,
      icon: Rows3,
      action: () => setDensity("compact"),
    },
  ];

  return (
    <CommandDialog
      open={open}
      onOpenChange={(next) => (next ? openPalette() : closePalette())}
      title={t("common.commandPalette")}
      description={t("command.placeholder")}
    >
      <CommandInput placeholder={t("command.placeholder")} />
      <CommandList>
        <CommandEmpty>{t("command.empty")}</CommandEmpty>

        <CommandGroup heading={t("command.group.navigation")}>
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const section = NAV_SECTIONS.find((candidate) => candidate.id === item.section);
            return (
              <CommandItem
                key={item.id}
                value={`${t(item.titleKey)} ${section ? t(section.titleKey) : ""} ${item.id}`}
                onSelect={() => run(() => navigate(item.path))}
              >
                <Icon aria-hidden="true" />
                <span>{t(item.titleKey)}</span>
                {item.status === "not_configured" && (
                  <span className="text-[10px] text-muted-foreground">
                    {t("common.notConfigured")}
                  </span>
                )}
                <Shortcut id={`goto-${item.id}`} />
              </CommandItem>
            );
          })}
        </CommandGroup>

        <CommandSeparator />

        <CommandGroup heading={t("command.group.actions")}>
          <CommandItem value={t("common.refresh")} onSelect={() => run(refreshDiagnostics)}>
            <span>{t("shortcuts.refresh")}</span>
            <Shortcut id="refresh-diagnostics" />
          </CommandItem>
          <CommandItem value={t("common.restartWorker")} onSelect={() => run(restart)}>
            <span>{t("common.restartWorker")}</span>
          </CommandItem>
          <CommandItem value={t("common.shortcuts")} onSelect={() => run(toggleShortcutsHelp)}>
            <span>{t("common.shortcuts")}</span>
            <Shortcut id="shortcuts-help" />
          </CommandItem>
          <CommandItem value={t("common.collapseSidebar")} onSelect={() => run(toggleSidebar)}>
            <span>{t("shortcuts.toggleSidebar")}</span>
            <Shortcut id="toggle-sidebar" />
          </CommandItem>
          <CommandItem value={t("common.inspector")} onSelect={() => run(toggleInspector)}>
            <span>{t("shortcuts.toggleInspector")}</span>
            <Shortcut id="toggle-inspector" />
          </CommandItem>
        </CommandGroup>

        <CommandSeparator />

        <CommandGroup heading={t("command.group.appearance")}>
          {appearance.map((entry) => {
            const Icon = entry.icon;
            return (
              <CommandItem key={entry.id} value={entry.label} onSelect={() => run(entry.action)}>
                <Icon aria-hidden="true" />
                <span>{entry.label}</span>
              </CommandItem>
            );
          })}
          <CommandItem value={t("language.en")} onSelect={() => run(() => setLanguage("en"))}>
            <span>
              {t("common.language")}: {t("language.en")}
            </span>
          </CommandItem>
          <CommandItem value={t("language.es")} onSelect={() => run(() => setLanguage("es"))}>
            <span>
              {t("common.language")}: {t("language.es")}
            </span>
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}
