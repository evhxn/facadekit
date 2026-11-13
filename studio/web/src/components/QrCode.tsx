import { useMemo } from "react";
import { encodeQr } from "./qr";

/** A QR code as SVG; renders nothing when the text does not fit. */
export default function QrCode({ text, className }: { text: string; className?: string }) {
  const matrix = useMemo(() => {
    try {
      return encodeQr(text);
    } catch {
      return null;
    }
  }, [text]);
  if (!matrix) return null;
  const quiet = 4;
  const size = matrix.length + quiet * 2;
  let d = "";
  matrix.forEach((row, y) =>
    row.forEach((dark, x) => {
      if (dark) d += `M${x + quiet} ${y + quiet}h1v1h-1z`;
    }),
  );
  return (
    <svg
      className={className}
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label={`QR code for ${text}`}
      shapeRendering="crispEdges"
    >
      <rect width={size} height={size} fill="#fff" />
      <path d={d} fill="#000" />
    </svg>
  );
}
