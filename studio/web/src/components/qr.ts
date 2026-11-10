/**
 * Tiny dependency-free QR code generator: byte mode, error correction level L, versions 1-6
 * (up to 134 bytes), which is plenty for the phone-remote URLs.
 */

interface VersionInfo {
  /** Total data codewords. */
  data: number;
  /** Error correction codewords per block. */
  ec: number;
  blocks: number;
  /** Centre of the single alignment pattern (versions 2-6), or null. */
  align: number | null;
}

// Index = version - 1. Level L only.
const VERSIONS: VersionInfo[] = [
  { data: 19, ec: 7, blocks: 1, align: null },
  { data: 34, ec: 10, blocks: 1, align: 18 },
  { data: 55, ec: 15, blocks: 1, align: 22 },
  { data: 80, ec: 20, blocks: 1, align: 26 },
  { data: 108, ec: 26, blocks: 1, align: 30 },
  { data: 136, ec: 18, blocks: 2, align: 34 },
];

export const QR_MAX_BYTES = 134;

// ---- Reed-Solomon over GF(256), polynomial 0x11D ----

const EXP = new Uint8Array(512);
const LOG = new Uint8Array(256);
{
  let x = 1;
  for (let i = 0; i < 255; i++) {
    EXP[i] = x;
    LOG[x] = i;
    x <<= 1;
    if (x & 0x100) x ^= 0x11d;
  }
  for (let i = 255; i < 512; i++) EXP[i] = EXP[i - 255]!;
}

function gfMul(a: number, b: number): number {
  return a === 0 || b === 0 ? 0 : EXP[LOG[a]! + LOG[b]!]!;
}

function rsGenerator(degree: number): number[] {
  let poly = [1];
  for (let i = 0; i < degree; i++) {
    const next = new Array<number>(poly.length + 1).fill(0);
    poly.forEach((c, j) => {
      next[j] = next[j]! ^ c;
      next[j + 1] = next[j + 1]! ^ gfMul(c, EXP[i]!);
    });
    poly = next;
  }
  return poly; // highest degree first, leading 1
}

function rsRemainder(data: number[], degree: number): number[] {
  const gen = rsGenerator(degree);
  const rem = new Array<number>(degree).fill(0);
  for (const byte of data) {
    const factor = byte ^ rem.shift()!;
    rem.push(0);
    for (let i = 0; i < degree; i++) rem[i] = rem[i]! ^ gfMul(gen[i + 1]!, factor);
  }
  return rem;
}

// ---- Codewords ----

function buildCodewords(bytes: number[], info: VersionInfo): number[] {
  const bits: number[] = [];
  const push = (value: number, count: number) => {
    for (let i = count - 1; i >= 0; i--) bits.push((value >>> i) & 1);
  };
  push(0b0100, 4); // byte mode
  push(bytes.length, 8);
  for (const b of bytes) push(b, 8);
  const capacityBits = info.data * 8;
  push(0, Math.min(4, capacityBits - bits.length));
  while (bits.length % 8 !== 0) bits.push(0);
  const words: number[] = [];
  for (let i = 0; i < bits.length; i += 8) {
    let w = 0;
    for (let j = 0; j < 8; j++) w = (w << 1) | bits[i + j]!;
    words.push(w);
  }
  for (let pad = 0xec; words.length < info.data; pad ^= 0xec ^ 0x11) words.push(pad);

  const per = info.data / info.blocks;
  const dataBlocks: number[][] = [];
  for (let b = 0; b < info.blocks; b++) dataBlocks.push(words.slice(b * per, (b + 1) * per));
  const ecBlocks = dataBlocks.map((blk) => rsRemainder(blk, info.ec));
  const out: number[] = [];
  for (let i = 0; i < per; i++) for (const blk of dataBlocks) out.push(blk[i]!);
  for (let i = 0; i < info.ec; i++) for (const blk of ecBlocks) out.push(blk[i]!);
  return out;
}

// ---- Matrix ----

type Matrix = boolean[][];

const MASKS: ((x: number, y: number) => boolean)[] = [
  (x, y) => (x + y) % 2 === 0,
  (_x, y) => y % 2 === 0,
  (x) => x % 3 === 0,
  (x, y) => (x + y) % 3 === 0,
  (x, y) => (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0,
  (x, y) => ((x * y) % 2) + ((x * y) % 3) === 0,
  (x, y) => (((x * y) % 2) + ((x * y) % 3)) % 2 === 0,
  (x, y) => (((x + y) % 2) + ((x * y) % 3)) % 2 === 0,
];

function formatBits(mask: number): number {
  const data = (0b01 << 3) | mask; // error correction level L = 01
  let rem = data;
  for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
  return ((data << 10) | rem) ^ 0x5412;
}

function penalty(m: Matrix): number {
  const n = m.length;
  let score = 0;
  const lineScore = (get: (i: number) => boolean): number => {
    let s = 0;
    let run = 1;
    for (let i = 1; i < n; i++) {
      if (get(i) === get(i - 1)) {
        run++;
        if (run === 5) s += 3;
        else if (run > 5) s++;
      } else run = 1;
    }
    const str = Array.from({ length: n }, (_, i) => (get(i) ? "1" : "0")).join("");
    for (const pat of ["10111010000", "00001011101"]) {
      let at = str.indexOf(pat);
      while (at !== -1) {
        s += 40;
        at = str.indexOf(pat, at + 1);
      }
    }
    return s;
  };
  for (let i = 0; i < n; i++) {
    score += lineScore((j) => m[i]![j]!);
    score += lineScore((j) => m[j]![i]!);
  }
  for (let y = 0; y < n - 1; y++) {
    for (let x = 0; x < n - 1; x++) {
      const c = m[y]![x];
      if (c === m[y]![x + 1] && c === m[y + 1]![x] && c === m[y + 1]![x + 1]) score += 3;
    }
  }
  let dark = 0;
  for (const row of m) for (const c of row) if (c) dark++;
  score += Math.floor(Math.abs((dark * 100) / (n * n) - 50) / 5) * 10;
  return score;
}

/** Encodes `text` (UTF-8) and returns the module matrix, `matrix[y][x]`, true = dark. */
export function encodeQr(text: string): boolean[][] {
  const bytes = Array.from(new TextEncoder().encode(text));
  const idx = VERSIONS.findIndex((v) => bytes.length <= v.data - 2);
  if (idx < 0) throw new Error(`QR payload too long (${bytes.length} bytes, max ${QR_MAX_BYTES})`);
  const info = VERSIONS[idx]!;
  const version = idx + 1;
  const size = 17 + 4 * version;

  const modules: Matrix = Array.from({ length: size }, () => new Array<boolean>(size).fill(false));
  const isFn: boolean[][] = Array.from({ length: size }, () =>
    new Array<boolean>(size).fill(false),
  );
  const setFn = (x: number, y: number, dark: boolean) => {
    if (x < 0 || y < 0 || x >= size || y >= size) return;
    modules[y]![x] = dark;
    isFn[y]![x] = true;
  };

  for (let i = 0; i < size; i++) {
    setFn(6, i, i % 2 === 0);
    setFn(i, 6, i % 2 === 0);
  }
  const finder = (cx: number, cy: number) => {
    for (let dy = -4; dy <= 4; dy++) {
      for (let dx = -4; dx <= 4; dx++) {
        const d = Math.max(Math.abs(dx), Math.abs(dy));
        setFn(cx + dx, cy + dy, d !== 4 && d !== 2);
      }
    }
  };
  finder(3, 3);
  finder(size - 4, 3);
  finder(3, size - 4);
  if (info.align !== null) {
    for (let dy = -2; dy <= 2; dy++) {
      for (let dx = -2; dx <= 2; dx++) {
        setFn(info.align + dx, info.align + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
      }
    }
  }
  // Reserve format areas (real values are written per mask below).
  for (let i = 0; i < 9; i++) {
    if (i === 6) continue; // (6, 8) and (8, 6) belong to the timing patterns
    setFn(8, i, false);
    setFn(i, 8, false);
  }
  for (let i = 0; i < 8; i++) {
    setFn(size - 1 - i, 8, false);
    setFn(8, size - 1 - i, false);
  }
  setFn(8, size - 8, true); // always-dark module

  // Place data bits in the zigzag order.
  const words = buildCodewords(bytes, info);
  const bits: boolean[] = [];
  for (const w of words) for (let i = 7; i >= 0; i--) bits.push(((w >>> i) & 1) === 1);
  let bi = 0;
  let upward = true;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5;
    for (let v = 0; v < size; v++) {
      const y = upward ? size - 1 - v : v;
      for (let j = 0; j < 2; j++) {
        const x = right - j;
        if (!isFn[y]![x]) modules[y]![x] = bi < bits.length ? bits[bi++]! : false;
      }
    }
    upward = !upward;
  }

  const withMask = (mask: number): Matrix => {
    const m = modules.map((row) => row.slice());
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        if (!isFn[y]![x] && MASKS[mask]!(x, y)) m[y]![x] = !m[y]![x];
      }
    }
    const f = formatBits(mask);
    const bit = (i: number) => ((f >>> i) & 1) === 1;
    for (let i = 0; i <= 5; i++) m[i]![8] = bit(i);
    m[7]![8] = bit(6);
    m[8]![8] = bit(7);
    m[8]![7] = bit(8);
    for (let i = 9; i < 15; i++) m[8]![14 - i] = bit(i);
    for (let i = 0; i < 8; i++) m[8]![size - 1 - i] = bit(i);
    for (let i = 8; i < 15; i++) m[size - 15 + i]![8] = bit(i);
    m[size - 8]![8] = true;
    return m;
  };

  let best = withMask(0);
  let bestScore = penalty(best);
  for (let mask = 1; mask < 8; mask++) {
    const candidate = withMask(mask);
    const score = penalty(candidate);
    if (score < bestScore) {
      best = candidate;
      bestScore = score;
    }
  }
  return best;
}
