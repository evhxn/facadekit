import { useEffect, useRef, useState } from "react";
import styles from "../SetupPage.module.css";

export type Reason = "dark" | "bright" | "outside" | "unclear";

/** Gray value in reasons.png = code * 50 (backend calib/reasons.py). */
const CODES: Record<number, Reason> = { 1: "dark", 2: "bright", 3: "outside", 4: "unclear" };

export const REASONS: Record<Reason, { rgb: [number, number, number]; title: string; fix: string }> = {
  dark: {
    rgb: [79, 140, 255],
    title: "Too dark",
    fix: "The camera saw almost no projector light here: a shadow, something in the beam, or dark or fuzzy material. Pictures will look dim there too. Move what is in the way, or leave these parts out of your surfaces.",
  },
  bright: {
    rgb: [255, 216, 79],
    title: "Washed out",
    fix: "The camera picture is too bright here to see stripes. Switch off other lights or lower the camera exposure, then scan again.",
  },
  outside: {
    rgb: [209, 108, 255],
    title: "Outside the camera picture",
    fix: "The projector lights this part, but the camera does not see it. Move the camera back or turn it until the whole grid is in the picture, then scan again.",
  },
  unclear: {
    rgb: [255, 80, 80],
    title: "Stripes not readable",
    fix: "There was light, but the stripes were blurred or squeezed: the camera looks along this surface at a steep angle, is out of focus, or something moved. Put the camera closer to the projector lens so both look from the same side, check focus, then use “Picture looks wrong, try another way” to scan with wider stripes.",
  },
};
const ORDER: Reason[] = ["outside", "dark", "bright", "unclear"];
/** Shares below this are not worth a line (speckle at the edges). */
const MIN_SHARE = 0.005;

export function reasonForGray(gray: number): Reason | null {
  return CODES[Math.round(gray / 50)] ?? null;
}

/** The reasons worth telling the user about, largest first within the fixed order. */
export function legend(unmeasured: Partial<Record<string, number>> | null | undefined) {
  if (!unmeasured) return [];
  return ORDER.map((reason) => ({ reason, share: unmeasured[reason] ?? 0 }))
    .filter((entry) => entry.share >= MIN_SHARE)
    .sort((a, b) => b.share - a.share);
}

const percent = (share: number) => (share < 0.01 ? "under 1" : String(Math.round(share * 100)));

interface Props {
  photoUrl: string;
  /** reasons.png; when absent (older scans) `maskUrl` paints everything unmeasured red. */
  reasonsUrl?: string;
  maskUrl?: string;
  unmeasured?: Partial<Record<string, number>> | null;
}

/** The projector-view photo with the unmeasured areas coloured by why they were missed. */
export default function ScanResultView({ photoUrl, reasonsUrl, maskUrl, unmeasured }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [show, setShow] = useState(true);
  const entries = legend(unmeasured);
  const overlayUrl = reasonsUrl ?? maskUrl;
  const byReason = reasonsUrl !== undefined;

  useEffect(() => {
    if (!overlayUrl) return;
    const img = new Image();
    img.onload = () => {
      const el = canvas.current;
      const ctx = el?.getContext("2d");
      if (!el || !ctx) return;
      el.width = img.naturalWidth;
      el.height = img.naturalHeight;
      ctx.drawImage(img, 0, 0);
      const data = ctx.getImageData(0, 0, el.width, el.height);
      const px = data.data;
      for (let i = 0; i < px.length; i += 4) {
        const gray = ((px[i] ?? 0) + (px[i + 1] ?? 0) + (px[i + 2] ?? 0)) / 3;
        const reason: Reason | null = byReason ? reasonForGray(gray) : gray < 128 ? "unclear" : null;
        const [r, g, b] = reason ? REASONS[reason].rgb : [0, 0, 0];
        px[i] = r;
        px[i + 1] = g;
        px[i + 2] = b;
        px[i + 3] = reason ? Math.round(0.45 * 255) : 0;
      }
      ctx.putImageData(data, 0, 0);
    };
    img.src = overlayUrl;
  }, [overlayUrl, byReason]);

  return (
    <div>
      <div className={styles.viewWrap}>
        <img className={styles.view} src={photoUrl} alt="Projector view of the house" />
        {overlayUrl ? (
          <canvas
            ref={canvas}
            className={styles.overlay}
            aria-hidden="true"
            style={{ visibility: show ? "visible" : "hidden" }}
          />
        ) : null}
      </div>
      {byReason && entries.length > 0 ? (
        <div className={styles.legend}>
          <label>
            <input type="checkbox" checked={show} onChange={(e) => setShow(e.target.checked)} /> Colour
            the parts that were not measured
          </label>
          <ul>
            {entries.map(({ reason, share }) => (
              <li key={reason}>
                <span
                  className={styles.swatch}
                  style={{ background: `rgb(${REASONS[reason].rgb.join(",")})` }}
                  aria-hidden="true"
                />
                <strong>
                  {REASONS[reason].title}: {percent(share)}% of the projection.
                </strong>{" "}
                {REASONS[reason].fix}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
