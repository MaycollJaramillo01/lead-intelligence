# UI system

The desktop UI is a research tool, not a dashboard: dense tables, a contextual inspector, keyboard
navigation and honest states. Conceptual references are Linear, Raycast, VS Code and Attio; no
interface is copied.

## Principles

1. **Dark first, light complete.** Both themes ship the same token set and are contrast-checked.
2. **Density is a setting.** `comfortable` and `compact` change row height, cell padding and UI
   font size through CSS variables, not component code.
3. **Status is never colour alone.** Every state carries an icon and a text label.
4. **Nothing is invented.** Numbers come from a live check; modules that do not exist yet say so
   and name the phase that delivers them. Empty is empty.
5. **Sober surface.** One border level, no gradients, no glass, no oversized cards.

## Design tokens

All tokens live in `apps/desktop/src/index.css` and are exposed to Tailwind through `@theme inline`.
Components use semantic names (`bg-card`, `text-muted-foreground`, `text-ok`), never raw colours.

| Group    | Tokens                                                                                   |
| -------- | ---------------------------------------------------------------------------------------- |
| Surfaces | `--background`, `--card`, `--popover`, `--muted`, `--sidebar`                            |
| Text     | `--foreground`, `--muted-foreground`, `--card-foreground`, `--sidebar-foreground`        |
| Action   | `--primary`, `--secondary`, `--accent`, `--destructive`, `--border`, `--input`, `--ring` |
| Status   | `--ok`, `--warn`, `--danger`, `--info`, `--neutral`                                      |
| Shape    | `--radius` (+ `--radius-sm/md/lg`), `--shadow-panel`                                     |
| Motion   | `--motion-fast`, `--motion-normal`, `--motion-ease`                                      |
| Layering | `--z-sidebar`, `--z-topbar`, `--z-panel`, `--z-overlay`, `--z-toast`                     |
| Density  | `--row-height`, `--cell-padding-x`, `--ui-font-size`, `--section-gap`                    |

`src/test/contrast.test.ts` converts every token from oklch to sRGB and fails the suite if a text
pair drops below 4.5:1 or a control boundary below 3:1, in either theme. `prefers-reduced-motion`
is honoured globally in the base layer.

## Navigation map

Six sections, sixteen modules. Status is what the module actually is today.

| Section      | Module          | Path               | Status    |
| ------------ | --------------- | ------------------ | --------- |
| Overview     | Overview        | `/overview`        | available |
| Acquisition  | Discovery       | `/discovery`       | phase 4   |
| Acquisition  | Campaigns       | `/campaigns`       | phase 4   |
| Acquisition  | Sources         | `/sources`         | phase 4   |
| Research     | Businesses      | `/businesses`      | available |
| Research     | Lead Explorer   | `/lead-explorer`   | available |
| Research     | Research Center | `/research-center` | phase 5   |
| Intelligence | ICP Builder     | `/icp-builder`     | available |
| Intelligence | Scoring Models  | `/scoring-models`  | available |
| Intelligence | AI Models       | `/ai-models`       | available |
| Operations   | Jobs            | `/jobs`            | available |
| Operations   | Datasets        | `/datasets`        | available |
| Operations   | Exports         | `/exports`         | phase 13  |
| System       | System          | `/system`          | available |
| System       | Logs            | `/logs`            | phase 13  |
| System       | Settings        | `/settings`        | available |

The registry (`src/app/navigation.ts`) is the single source: sidebar, breadcrumbs, command palette,
shortcuts and the "not configured" pages all read from it.

## Keyboard

Shortcuts live in `src/lib/shortcuts.ts`; a test fails if two shortcuts claim the same combo, and
the help panel (`?`) is generated from the same registry. Shortcuts are ignored while typing in a
field, except the command palette.

| Combo      | Action                                                      |
| ---------- | ----------------------------------------------------------- |
| `Ctrl K`   | Command palette                                             |
| `?`        | Keyboard shortcuts help                                     |
| `Ctrl ,`   | Settings                                                    |
| `Alt R`    | Re-run the system check                                     |
| `Ctrl B`   | Collapse / expand the sidebar                               |
| `Ctrl I`   | Show / hide the inspector                                   |
| `Alt T`    | Switch light / dark                                         |
| `Alt D`    | Switch density                                              |
| `Alt 1..6` | Overview, Businesses, Lead Explorer, Jobs, System, Settings |

Every interactive element is reachable by Tab, the focus ring is one treatment defined in the base
layer, and a skip link jumps to the main region.

## Reusable components

| Component                               | Purpose                                                                      |
| --------------------------------------- | ---------------------------------------------------------------------------- |
| `components/layout/AppLayout`           | Shell: sidebar, top bar, routed content, palette, toasts, shortcuts          |
| `components/layout/Sidebar`             | Collapsible, resizable navigation with status indicators                     |
| `components/layout/TopBar`              | Breadcrumbs, global search entry, screen actions slot, theme + help          |
| `components/layout/CommandPalette`      | Navigation and real actions only                                             |
| `components/layout/ShortcutsHelp`       | Help panel generated from the registry                                       |
| `components/layout/page-chrome`         | `PageShell`, `PageScroll`, `Section`, `PageActions` (portal to top bar)      |
| `components/data/DataTable`             | Dense table: sort, selection, resize, pinning, visibility, virtualization    |
| `components/data/PagedFooter`           | "Showing N of M" and **Load more** for keyset-paginated lists                |
| `components/data/cells`                 | `Text`, `Mono`, `DateTime` (muted dash when missing) and `TestDataBadge`     |
| `components/data/FilterBar` + `filters` | Editable filter chips and the readable query summary                         |
| `components/states/StateBlock`          | empty, loading, processing, error, offline, partial, stale, success, blocked |
| `components/states/TableSkeleton`       | Row-shaped skeleton at the current density                                   |
| `components/domain/EvidenceItem`        | A claim with source, date, confidence, strength and freshness                |
| `components/domain/ScoreValue`          | Value, model version, confidence and factor breakdown                        |
| `components/domain/JobProgress`         | Determinate or indeterminate progress, rate, errors, cancel                  |
| `components/ErrorBoundary`              | Global boundary plus one per routed module                                   |

Tables use a fixed layout: columns keep their declared size and long values truncate, so a long
cell never slides under a pinned column. Synthetic test data is always labelled with
`TestDataBadge`, in the table and in the inspector.

Primitives under `components/ui/` are vendored shadcn/ui components (Radix based); they are edited
only when the app needs it (the toaster follows our theme store instead of next-themes).

## State

| Kind                  | Where                                  | Rule                                                    |
| --------------------- | -------------------------------------- | ------------------------------------------------------- |
| Remote / IPC          | `features/diagnostics/store.ts`        | Only through the typed adapter in `lib/ipc.ts`          |
| Remote lists          | `lib/useKeysetPages.ts`                | Keyset pages; a new query restarts, stale answers drop  |
| Persisted preferences | `stores/preferences.ts` (localStorage) | Theme, density, language, sidebar size, column layouts  |
| Ephemeral UI          | `stores/ui.ts`                         | Palette, help, inspector, selected row; never persisted |

Components never call `invoke` directly and never run SQL. Stores are small and selected field by
field so a change in one screen does not re-render the others.

## Screens today

| Screen         | What it shows                                                                                                  |
| -------------- | -------------------------------------------------------------------------------------------------------------- |
| Overview       | Live subsystem states, the screens that work, and the pipeline status                                          |
| Businesses     | Real store query: server search, keyset paging, inspector with every value's provenance; empty until phase 04  |
| Lead Explorer  | Ranked leads of the newest finished score run, filtered in the store; inspector with the lead score (phase 11) |
| ICP Builder    | Versioned profiles: criteria, Latino requirement, exclusions, offers, preview on real data (phase 11)          |
| Scoring Models | Formula in plain text, weights and versions; runs, comparison and calibration (phase 11)                       |
| Datasets       | Datasets with their latest snapshot (version, rows, capture time)                                              |
| Jobs           | The runs of every engine with progress and cancel                                                              |
| System         | Full diagnostics from phase 01, with refresh and worker restart                                                |
| Settings       | Theme, density, language, workspace paths, stored preferences                                                  |
| Exports, Logs  | Title, purpose, "not configured" and the phase that delivers them                                              |
