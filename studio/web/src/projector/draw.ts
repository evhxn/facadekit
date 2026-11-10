import type { OutlinesMessage } from "./projectorSocket";

export const GRID_STEP = 120;

export function drawBlank(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  ctx.fillStyle = "#000000";
  ctx.fillRect(0, 0, w, h);
}

export function drawWhite(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, w, h);
}

export function drawGrid(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  drawBlank(ctx, w, h);
  ctx.fillStyle = "#ffffff";
  for (let x = 0; x < w; x += GRID_STEP) ctx.fillRect(x, 0, 1, h);
  for (let y = 0; y < h; y += GRID_STEP) ctx.fillRect(0, y, w, 1);
  // 1 px border (right and bottom edges are the last pixel row/column)
  ctx.fillRect(w - 1, 0, 1, h);
  ctx.fillRect(0, h - 1, w, 1);
  // 3 px red cross at the exact centre
  ctx.fillStyle = "#ff0000";
  const cx = Math.floor(w / 2) - 1;
  const cy = Math.floor(h / 2) - 1;
  ctx.fillRect(cx, 0, 3, h);
  ctx.fillRect(0, cy, w, 3);
}

export function drawImage(
  ctx: CanvasRenderingContext2D,
  img: CanvasImageSource,
  w: number,
  h: number,
): void {
  void w;
  void h; // size is accepted for API symmetry; the image is never scaled
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(img, 0, 0);
}

export function drawOutlines(
  ctx: CanvasRenderingContext2D,
  msg: OutlinesMessage,
  w: number,
  h: number,
): void {
  drawBlank(ctx, w, h);
  const s = w / msg.width;
  for (const surf of msg.surfaces) {
    const pts = surf.quad.map(([x, y]) => [x * s, y * s] as const);
    const first = pts[0];
    if (!first) continue;
    const color = surf.selected ? "#ffff00" : "#ffffff";
    ctx.strokeStyle = color;
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(first[0], first[1]);
    for (const p of pts.slice(1)) ctx.lineTo(p[0], p[1]);
    ctx.closePath();
    ctx.stroke();

    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(first[0], first[1], 4, 0, Math.PI * 2);
    ctx.fill();

    const cx = pts.reduce((a, p) => a + p[0], 0) / pts.length;
    const cy = pts.reduce((a, p) => a + p[1], 0) / pts.length;
    ctx.fillStyle = "#ffffff";
    ctx.font = "24px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(surf.name, cx, cy);
  }
}

export function drawText(ctx: CanvasRenderingContext2D, w: number, h: number, text: string): void {
  drawBlank(ctx, w, h);
  ctx.fillStyle = "#ffffff";
  ctx.font = "24px sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, w / 2, h / 2);
}
