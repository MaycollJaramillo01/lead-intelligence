import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { contrastRatio, readTokens } from "@/test/color";

// The stylesheet is the source of truth for the tokens; vitest runs from the app directory.
const css = readFileSync(resolve(process.cwd(), "src/index.css"), "utf8");

const light = readTokens(css, ":root");
const dark = readTokens(css, ".dark");

/** Text pairs that must clear WCAG AA for normal text. */
const TEXT_PAIRS: Array<[string, string]> = [
  ["--foreground", "--background"],
  ["--foreground", "--card"],
  ["--foreground", "--muted"],
  ["--muted-foreground", "--background"],
  ["--muted-foreground", "--card"],
  ["--sidebar-foreground", "--sidebar"],
  ["--primary-foreground", "--primary"],
  ["--destructive-foreground", "--destructive"],
  ["--ok", "--background"],
  ["--warn", "--background"],
  ["--danger", "--background"],
  ["--info", "--background"],
  ["--neutral", "--background"],
  ["--ok", "--card"],
  ["--warn", "--card"],
  ["--danger", "--card"],
];

/**
 * Non-text pairs at the 3:1 UI-component threshold: the focus ring and the border that
 * identifies a form control. Row separators are decorative and deliberately subtle.
 */
const UI_PAIRS: Array<[string, string]> = [
  ["--ring", "--background"],
  ["--input", "--background"],
  ["--input", "--card"],
];

describe.each([
  ["light", light],
  ["dark", dark],
])("%s theme tokens", (_theme, tokens) => {
  it.each(TEXT_PAIRS)("%s on %s clears 4.5:1", (foreground, background) => {
    const foregroundValue = tokens[foreground];
    const backgroundValue = tokens[background];
    expect([foreground, foregroundValue]).not.toContain(undefined);
    expect([background, backgroundValue]).not.toContain(undefined);
    const ratio = contrastRatio(foregroundValue ?? "", backgroundValue ?? "");
    expect(Number(ratio.toFixed(2))).toBeGreaterThanOrEqual(4.5);
  });

  it.each(UI_PAIRS)("%s on %s clears 3:1", (foreground, background) => {
    const ratio = contrastRatio(tokens[foreground] ?? "", tokens[background] ?? "");
    expect(Number(ratio.toFixed(2))).toBeGreaterThanOrEqual(3);
  });
});
