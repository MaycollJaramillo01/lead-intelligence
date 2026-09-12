/** Minimal oklch -> sRGB -> WCAG contrast helpers, used to validate the design tokens. */

interface Rgb {
  r: number;
  g: number;
  b: number;
}

export function parseOklch(value: string): { l: number; c: number; h: number } | null {
  const match = /oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\)/.exec(value);
  if (!match) return null;
  return { l: Number(match[1]), c: Number(match[2]), h: Number(match[3]) };
}

/** oklch -> linear sRGB (Björn Ottosson's conversion). */
export function oklchToLinearRgb(l: number, c: number, h: number): Rgb {
  const hRad = (h * Math.PI) / 180;
  const a = c * Math.cos(hRad);
  const b = c * Math.sin(hRad);

  const lCube = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const mCube = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const sCube = (l - 0.0894841775 * a - 1.291485548 * b) ** 3;

  return {
    r: 4.0767416621 * lCube - 3.3077115913 * mCube + 0.2309699292 * sCube,
    g: -1.2684380046 * lCube + 2.6097574011 * mCube - 0.3413193965 * sCube,
    b: -0.0041960863 * lCube - 0.7034186147 * mCube + 1.707614701 * sCube,
  };
}

const clamp01 = (value: number) => Math.min(1, Math.max(0, value));

/** WCAG relative luminance from linear sRGB. */
export function relativeLuminance(rgb: Rgb): number {
  return 0.2126 * clamp01(rgb.r) + 0.7152 * clamp01(rgb.g) + 0.0722 * clamp01(rgb.b);
}

export function contrastRatio(foreground: string, background: string): number {
  const first = parseOklch(foreground);
  const second = parseOklch(background);
  if (!first || !second) throw new Error(`not an oklch colour: ${foreground} / ${background}`);
  const lighter = relativeLuminance(oklchToLinearRgb(first.l, first.c, first.h));
  const darker = relativeLuminance(oklchToLinearRgb(second.l, second.c, second.h));
  const [high, low] = lighter > darker ? [lighter, darker] : [darker, lighter];
  return (high + 0.05) / (low + 0.05);
}

/** Extracts `--token: value;` declarations from one CSS block. */
export function readTokens(css: string, selector: string): Record<string, string> {
  const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const block = new RegExp(`${escaped}\\s*\\{([^}]*)\\}`).exec(withoutComments);
  if (!block?.[1]) throw new Error(`selector not found: ${selector}`);
  const tokens: Record<string, string> = {};
  for (const line of block[1].split(";")) {
    const [name, ...rest] = line.split(":");
    if (!name?.trim().startsWith("--")) continue;
    tokens[name.trim()] = rest.join(":").trim();
  }
  return tokens;
}
