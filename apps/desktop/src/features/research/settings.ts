import type { CrawlSettings } from "@lead/shared-types";
import type { TranslationKey, Translator } from "@/lib/i18n";

/** Mirrors `default_crawl_settings()` in domain.py; `settings.test.ts` compares it to the fixture. */
export const DEFAULT_CRAWL_SETTINGS: CrawlSettings = {
  max_sites: 100,
  max_pages_per_site: 12,
  max_depth: 2,
  max_bytes_per_site: 25_000_000,
  max_seconds_per_site: 180,
  max_requests: 2_000,
  requests_per_minute_per_host: 30,
  per_host_concurrency: 2,
  include_subdomains: false,
  include_paths: [],
  exclude_paths: [],
  browser: "auto",
  screenshots: false,
};

type NumericSetting = {
  [Key in keyof CrawlSettings]: CrawlSettings[Key] extends number ? Key : never;
}[keyof CrawlSettings];

/** The numbers an operator may change, with the contract's own bounds. */
export const NUMERIC_SETTINGS: ReadonlyArray<{
  key: NumericSetting;
  label: TranslationKey;
  min: number;
  max: number;
  /** Shown and typed in this unit; stored in the contract's. */
  scale?: number;
}> = [
  { key: "max_pages_per_site", label: "research.setting.pages", min: 1, max: 100 },
  { key: "max_depth", label: "research.setting.depth", min: 0, max: 4 },
  {
    key: "max_bytes_per_site",
    label: "research.setting.megabytes",
    min: 1,
    max: 100,
    scale: 1_000_000,
  },
  { key: "max_seconds_per_site", label: "research.setting.seconds", min: 10, max: 1_800 },
  { key: "max_requests", label: "research.setting.requests", min: 1, max: 40_000 },
  { key: "requests_per_minute_per_host", label: "research.setting.rate", min: 1, max: 120 },
  { key: "max_sites", label: "research.setting.sites", min: 1, max: 2_000 },
];

export function lines(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

/** Reason codes the worker sends. An unknown code is shown as the code itself, never hidden. */
const REASONS = new Set([
  "robots",
  "robots_unreachable",
  "page_budget",
  "byte_budget",
  "time_budget",
  "request_budget",
  "private_area",
  "trap",
  "binary_resource",
  "excluded",
  "not_included",
  "redirect_loop",
  "too_many_redirects",
  "redirect_invalid",
  "redirect_out_of_scope",
  "unsafe_target",
  "dns_error",
  "blocked",
  "rate_limited",
  "server_error",
  "timeout",
  "connection_failed",
  "network_error",
  "decoding_error",
  "not_found",
  "http_error",
  "too_large",
  "unsupported_type",
  "empty_response",
  "not_modified",
  "browser_unavailable",
  "browser_failed",
  "internal_error",
  "empty_shell",
  "js_required",
  "http_functional_block",
  "configured",
]);

export function reasonLabel(t: Translator, code: string | null): string {
  if (code === null) return "—";
  return REASONS.has(code) ? t(`crawlReason.${code}` as TranslationKey) : code;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1_000) return `${bytes} B`;
  if (bytes < 1_000_000) return `${(bytes / 1_000).toFixed(1)} kB`;
  return `${(bytes / 1_000_000).toFixed(1)} MB`;
}
