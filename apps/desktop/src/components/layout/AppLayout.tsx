import { useMemo, useState } from "react";
import { Outlet, useNavigate } from "react-router";
import { findNavItem } from "@/app/navigation";
import { CommandPalette } from "@/components/layout/CommandPalette";
import { PageActionsProvider } from "@/components/layout/page-chrome";
import { ShortcutsHelp } from "@/components/layout/ShortcutsHelp";
import { Sidebar } from "@/components/layout/Sidebar";
import { TopBar } from "@/components/layout/TopBar";
import { Toaster } from "@/components/ui/sonner";
import { useDiagnosticsLifecycle } from "@/features/diagnostics/useDiagnosticsLifecycle";
import { useDiagnosticsStore } from "@/features/diagnostics/store";
import { useT } from "@/lib/i18n";
import { SHORTCUTS, useShortcuts, type ShortcutHandlers } from "@/lib/shortcuts";
import { usePreferences } from "@/stores/preferences";
import { useUiStore } from "@/stores/ui";

/**
 * Application shell: sidebar, contextual top bar, routed content, palette and toasts.
 * Shortcut handlers read stores through getState so the binding stays stable.
 */
export function AppLayout() {
  const t = useT();
  const navigate = useNavigate();
  const [actionsHost, setActionsHost] = useState<HTMLDivElement | null>(null);
  useDiagnosticsLifecycle();

  const handlers = useMemo<ShortcutHandlers>(() => {
    const navigation: ShortcutHandlers = {};
    for (const shortcut of SHORTCUTS) {
      const targetId = shortcut.target;
      if (shortcut.group !== "navigation" || !targetId) continue;
      navigation[shortcut.id] = () => {
        const item = findNavItem(targetId);
        if (item) navigate(item.path);
      };
    }
    return {
      ...navigation,
      "command-palette": () => useUiStore.getState().toggleCommandPalette(),
      "shortcuts-help": () => useUiStore.getState().toggleShortcutsHelp(),
      "toggle-inspector": () => useUiStore.getState().toggleInspector(),
      "toggle-sidebar": () => usePreferences.getState().toggleSidebar(),
      "toggle-theme": () => {
        const { theme, setTheme } = usePreferences.getState();
        setTheme(theme === "dark" ? "light" : "dark");
      },
      "toggle-density": () => {
        const { density, setDensity } = usePreferences.getState();
        setDensity(density === "compact" ? "comfortable" : "compact");
      },
      settings: () => navigate("/settings"),
      "refresh-diagnostics": () => void useDiagnosticsStore.getState().refresh(),
    };
  }, [navigate]);

  useShortcuts(handlers);

  return (
    <div className="flex h-full w-full overflow-hidden">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-[var(--z-overlay)] focus:rounded-md focus:bg-popover focus:px-3 focus:py-2 focus:text-sm"
      >
        {t("app.skipToContent")}
      </a>
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col border-l">
        <TopBar actionsRef={setActionsHost} />
        <main id="main-content" tabIndex={-1} className="min-h-0 flex-1 outline-none">
          <PageActionsProvider value={actionsHost}>
            <Outlet />
          </PageActionsProvider>
        </main>
      </div>
      <CommandPalette />
      <ShortcutsHelp />
      <Toaster />
    </div>
  );
}
