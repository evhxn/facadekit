import { describe, expect, it } from "vitest";
import { encodeQr } from "./qr";

function finderAt(m: boolean[][], x0: number, y0: number): boolean {
  for (let dy = 0; dy < 7; dy++) {
    for (let dx = 0; dx < 7; dx++) {
      const edge = Math.max(Math.abs(dx - 3), Math.abs(dy - 3));
      if (m[y0 + dy]![x0 + dx] !== (edge !== 2)) return false;
    }
  }
  return true;
}

describe("encodeQr", () => {
  it("encodes HELLO into a 21x21 version 1 matrix with three finder patterns", () => {
    const m = encodeQr("HELLO");
    expect(m).toHaveLength(21);
    expect(m.every((row) => row.length === 21)).toBe(true);
    expect(finderAt(m, 0, 0)).toBe(true);
    expect(finderAt(m, 14, 0)).toBe(true);
    expect(finderAt(m, 0, 14)).toBe(true);
    expect(m[13]![8]).toBe(true); // dark module
    expect(m[6]![10]).toBe(true); // timing pattern
    expect(m[6]![9]).toBe(false);
  });

  it("picks a larger version for URLs", () => {
    const m = encodeQr("http://192.168.100.200:8137/remote/my-house-lights-2026");
    expect(m.length).toBe(17 + 4 * 4); // 55 bytes -> version 4
    expect(finderAt(m, m.length - 7, 0)).toBe(true);
  });

  it("rejects payloads that are too long", () => {
    expect(() => encodeQr("x".repeat(200))).toThrow();
  });
});
