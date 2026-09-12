import type { Ref } from "react";
import { useLocation } from "react-router";
import { ChevronRight, Keyboard, Moon, Search, Sun } from "lucide-react";
import { findNavItemByPath, NAV_SECTIONS } from "@/app/navigation";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useT } from "@/lib/i18n";
import { formatCombo, SHORTCUTS } from "@/lib/shortcuts";
import { cn } from "@/lib/utils";
import { usePreferences } from "@/stores/preferences";
import { useUiStore } from "@/stores/ui";

function Kbd({ combo }: { combo: string }) {
  return (
    <span aria-hidden="true" className="flex gap-0.5">
      {formatCombo(combo).map((key) => (
        <kbd
          key={key}
          className="rounded border bg-muted px-1 font-mono text-[10px] leading-4 text-muted-foreground"
        >
          {key}
        </kbd>
      ))}
    </span>
  );
}

function Breadcrumbs() {
  const t = useT();
  const { pathname } = useLocation();
  const item = findNavItemByPath(pathname);
  if (!item) return <span className="text-sm font-medium">{t("app.name")}</span>;
  const section = NAV_SECTIONS.find((candidate) => candidate.id === item.section);

  return (
    <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1.5">
      <ol className="flex min-w-0 items-center gap-1.5">
        {section && item.section !== "overview" && (
          <>
            <li className="text-xs text-muted-foreground">{t(section.titleKey)}</li>
            <li aria-hidden="true">
              <ChevronRight className="size-3 text-muted-foreground" />
            </li>
          </>
        )}
        <li className="flex min-w-0 items-center gap-2">
          <span aria-current="page" className="truncate text-sm font-medium">
            {t(item.titleKey)}
          </span>
          {item.status === "not_configured" && (
            <span className="rounded border px-1.5 py-px text-[10px] text-muted-foreground">
              {t("common.notConfigured")}
            </span>
          )}
        </li>
      </ol>
    </nav>
  );
}

export function TopBar({ actionsRef }: { actionsRef: Ref<HTMLDivElement> }) {
  const t = useT();
  const openCommandPalette = useUiStore((state) => state.openCommandPalette);
  const toggleShortcutsHelp = useUiStore((state) => state.toggleShortcutsHelp);
  const theme = usePreferences((state) => state.theme);
  const setTheme = usePreferences((state) => state.setTheme);
  const paletteCombo = SHORTCUTS.find((shortcut) => shortcut.id === "command-palette")?.combo ?? "";

  return (
    <header
      className="flex shrink-0 items-center gap-3 border-b px-3"
      style={{ height: "calc(var(--row-height) + 0.75rem)" }}
    >
      <Breadcrumbs />

      <div className="flex flex-1 justify-center">
        <button
          type="button"
          onClick={openCommandPalette}
          className={cn(
            "flex h-7 w-full max-w-md items-center gap-2 rounded-md border bg-muted/40 px-2",
            "text-xs text-muted-foreground hover:bg-muted",
          )}
        >
          <Search aria-hidden="true" className="size-3.5" />
          <span className="flex-1 truncate text-left">{t("common.searchPlaceholder")}</span>
          <Kbd combo={paletteCombo} />
        </button>
      </div>

      {/* screen-specific actions are portalled here */}
      <div ref={actionsRef} className="flex items-center gap-2" />

      <div className="flex items-center gap-1">
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              aria-label={t("common.theme")}
              className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground"
            >
              {theme === "dark" ? (
                <Moon aria-hidden="true" className="size-4" />
              ) : (
                <Sun aria-hidden="true" className="size-4" />
              )}
            </button>
          </TooltipTrigger>
          <TooltipContent>{t("common.theme")}</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={toggleShortcutsHelp}
              aria-label={t("common.shortcuts")}
              className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground"
            >
              <Keyboard aria-hidden="true" className="size-4" />
            </button>
          </TooltipTrigger>
          <TooltipContent>{t("common.shortcuts")}</TooltipContent>
        </Tooltip>
      </div>
    </header>
  );
}
