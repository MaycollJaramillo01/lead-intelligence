import { useCallback, type KeyboardEvent, type PointerEvent } from "react";
import { NavLink } from "react-router";
import { PanelLeft } from "lucide-react";
import { NAV_SECTIONS, visibleSidebarItems, type NavItem } from "@/app/navigation";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useDiagnosticsStore } from "@/features/diagnostics/store";
import { workerRowState } from "@/features/diagnostics/model";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { SIDEBAR_MAX_WIDTH, SIDEBAR_MIN_WIDTH, usePreferences } from "@/stores/preferences";

const COLLAPSED_WIDTH = 48;

const INDICATOR_TONE: Record<string, string> = {
  ok: "bg-ok",
  degraded: "bg-warn",
  error: "bg-danger",
  unavailable: "bg-neutral",
  pending: "bg-info",
  unknown: "bg-neutral",
};

function StatusDot({ state, label }: { state: string; label: string }) {
  return (
    <span className="flex items-center gap-1">
      <span
        aria-hidden="true"
        className={cn("size-1.5 rounded-full", INDICATOR_TONE[state] ?? "bg-neutral")}
      />
      <span className="sr-only">{label}</span>
    </span>
  );
}

function SidebarItem({ item, collapsed }: { item: NavItem; collapsed: boolean }) {
  const t = useT();
  const workerState = useDiagnosticsStore((state) => state.worker?.state ?? null);
  const label = t(item.titleKey);
  const Icon = item.icon;
  const indicator =
    item.id === "system"
      ? workerRowState(workerState)
      : item.status === "not_configured"
        ? "unknown"
        : null;

  const link = (
    <NavLink
      to={item.path}
      className={({ isActive }) =>
        cn(
          "flex items-center gap-2 rounded-md px-2 text-sidebar-foreground/85 transition-colors",
          "hover:bg-accent hover:text-accent-foreground",
          isActive && "bg-accent font-medium text-accent-foreground",
          item.status === "not_configured" && "text-sidebar-foreground/55",
          collapsed && "justify-center px-0",
        )
      }
      style={{ height: "var(--row-height)" }}
    >
      <Icon aria-hidden="true" className="size-4 shrink-0" />
      {!collapsed && <span className="min-w-0 flex-1 truncate">{label}</span>}
      {!collapsed && indicator && (
        <StatusDot
          state={indicator}
          label={
            item.id === "system"
              ? `${label}: ${indicator}`
              : `${label}: ${t("common.notConfigured")}`
          }
        />
      )}
      {collapsed && <span className="sr-only">{label}</span>}
    </NavLink>
  );

  if (!collapsed) return link;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{link}</TooltipTrigger>
      <TooltipContent side="right">
        {label}
        {item.status === "not_configured" ? ` · ${t("common.notConfigured")}` : ""}
      </TooltipContent>
    </Tooltip>
  );
}

function ResizeHandle() {
  const t = useT();
  const width = usePreferences((state) => state.sidebarWidth);
  const setSidebarWidth = usePreferences((state) => state.setSidebarWidth);

  const onPointerDown = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = width;
      const move = (moveEvent: globalThis.PointerEvent) =>
        setSidebarWidth(startWidth + (moveEvent.clientX - startX));
      const stop = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", stop);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", stop);
    },
    [setSidebarWidth, width],
  );

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
      if (event.key === "ArrowLeft") setSidebarWidth(width - 16);
      else if (event.key === "ArrowRight") setSidebarWidth(width + 16);
      else return;
      event.preventDefault();
    },
    [setSidebarWidth, width],
  );

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={t("common.resizeSidebar")}
      aria-valuenow={width}
      aria-valuemin={SIDEBAR_MIN_WIDTH}
      aria-valuemax={SIDEBAR_MAX_WIDTH}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      className="w-1 shrink-0 cursor-col-resize bg-transparent transition-colors hover:bg-primary/50 focus-visible:bg-primary/70"
    />
  );
}

export function Sidebar() {
  const t = useT();
  const collapsed = usePreferences((state) => state.sidebarCollapsed);
  const width = usePreferences((state) => state.sidebarWidth);
  const toggleSidebar = usePreferences((state) => state.toggleSidebar);
  const showAdvancedTools = usePreferences((state) => state.showAdvancedTools);
  const visibleItems = visibleSidebarItems(showAdvancedTools);

  return (
    <div className="flex h-full shrink-0">
      <nav
        aria-label={t("app.name")}
        className="flex h-full flex-col overflow-hidden bg-sidebar text-sidebar-foreground"
        style={{ width: collapsed ? COLLAPSED_WIDTH : width }}
      >
        <div
          className={cn("flex items-center gap-2 px-2", collapsed && "justify-center px-0")}
          style={{ height: "calc(var(--row-height) + 0.75rem)" }}
        >
          {!collapsed && (
            <span className="min-w-0 flex-1 truncate px-1 text-xs font-semibold tracking-wide">
              {t("app.name")}
            </span>
          )}
          <button
            type="button"
            onClick={toggleSidebar}
            aria-label={collapsed ? t("common.expandSidebar") : t("common.collapseSidebar")}
            aria-expanded={!collapsed}
            className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground"
          >
            <PanelLeft aria-hidden="true" className="size-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
          {NAV_SECTIONS.map((section) => {
            const items = visibleItems.filter((item) => item.section === section.id);
            if (items.length === 0) return null;
            return (
              <div key={section.id} className="mb-2">
                {!collapsed && (
                  <h2 className="px-2 py-1 text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
                    {t(section.titleKey)}
                  </h2>
                )}
                {collapsed && <div className="mx-auto my-2 h-px w-6 bg-border" />}
                <ul className="space-y-0.5">
                  {items.map((item) => (
                    <li key={item.id}>
                      <SidebarItem item={item} collapsed={collapsed} />
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      </nav>
      {!collapsed && <ResizeHandle />}
    </div>
  );
}
