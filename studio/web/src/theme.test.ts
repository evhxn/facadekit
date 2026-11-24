import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// WCAG 2.1 contrast of the theme's colour pairs, in both themes. Text pairs need 4.5:1
// (AA, normal text); UI parts such as borders of controls and the focus ring need 3:1.
const css = readFileSync(join(__dirname, "theme.css"), "utf8");

function tokens(block: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of block.matchAll(/--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)) out[m[1]!] = m[2]!;
  return out;
}

const dark = tokens(css.slice(css.indexOf(":root"), css.indexOf("@media (prefers-color-scheme")));
const lightBlock = css.slice(css.indexOf("@media (prefers-color-scheme: light)"));
const light = { ...dark, ...tokens(lightBlock.slice(0, lightBlock.indexOf("\n}\n") + 3)) };

function luminance(hex: string): number {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const l = c.map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * l[0]! + 0.7152 * l[1]! + 0.0722 * l[2]!;
}

function contrast(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p) as [number, number];
  return (x + 0.05) / (y + 0.05);
}

const TEXT: [string, string][] = [
  ["text", "bg"],
  ["text", "surface"],
  ["text", "surface-2"],
  ["muted", "bg"],
  ["muted", "surface"],
  ["muted", "surface-2"],
  ["accent", "bg"],
  ["accent", "surface"],
  ["accent", "surface-2"],
  ["on-accent", "accent"],
  ["danger", "bg"],
  ["danger", "surface"],
  ["danger", "surface-2"],
  ["on-danger", "danger-solid"],
  ["text", "danger-bg"],
  ["danger", "danger-bg"],
  ["text", "warn-bg"],
  ["warn", "bg"],
  ["warn", "surface"],
  ["text", "info-bg"],
  ["info", "surface"],
  ["ok", "surface"],
  ["ok", "bg"],
];
const UI: [string, string][] = [
  ["focus", "bg"],
  ["focus", "surface"],
  ["accent", "surface-2"],
];

describe.each([
  ["dark", dark],
  ["light", light],
])("%s theme contrast", (_name, t) => {
  it("has every token", () => {
    for (const [a, b] of [...TEXT, ...UI]) {
      expect(t[a], a).toMatch(/^#/);
      expect(t[b], b).toMatch(/^#/);
    }
  });
  it.each(TEXT)("text %s on %s is at least 4.5:1", (a, b) => {
    expect(contrast(t[a]!, t[b]!)).toBeGreaterThanOrEqual(4.5);
  });
  it.each(UI)("%s on %s is at least 3:1", (a, b) => {
    expect(contrast(t[a]!, t[b]!)).toBeGreaterThanOrEqual(3);
  });
});
