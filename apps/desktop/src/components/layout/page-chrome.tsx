import { createContext, use, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";

/** Host element for screen actions rendered in the top bar. */
const PageActionsContext = createContext<HTMLElement | null>(null);
export const PageActionsProvider = PageActionsContext.Provider;

/** Renders screen-specific actions into the contextual top bar. */
export function PageActions({ children }: { children: ReactNode }) {
  const host = use(PageActionsContext);
  return host ? createPortal(children, host) : null;
}

/** Full-height screen frame: the shell scrolls nothing, panes scroll themselves. */
export function PageShell({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("flex h-full min-h-0 flex-col", className)}>{children}</div>;
}

/** Scrollable padded body for screens that are not a full-bleed table. */
export function PageScroll({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("min-h-0 flex-1 overflow-auto p-4", className)}>
      <div className="mx-auto w-full max-w-5xl space-y-[var(--section-gap)]">{children}</div>
    </div>
  );
}

interface SectionProps {
  title: string;
  description?: string;
  children: ReactNode;
  actions?: ReactNode;
  className?: string;
}

/** Titled block used by the non-table screens. Sober: one border, no nested cards. */
export function Section({ title, description, children, actions, className }: SectionProps) {
  return (
    <section className={cn("rounded-md border bg-card", className)}>
      <header className="flex items-start justify-between gap-4 border-b px-4 py-2.5">
        <div className="min-w-0">
          <h2 className="text-sm font-medium">{title}</h2>
          {description && <p className="text-xs text-muted-foreground">{description}</p>}
        </div>
        {actions}
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}
