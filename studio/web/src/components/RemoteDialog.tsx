import { useEffect, useMemo, useState } from "react";
import { remoteInfo, type RemoteInfo } from "../api/client";
import { encodeQr } from "./qr";
import styles from "./RemoteDialog.module.css";

function QrCode({ text }: { text: string }) {
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
      className={styles.qr}
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

export default function RemoteDialog({
  projectId,
  onClose,
}: {
  projectId: string;
  onClose: () => void;
}) {
  const [info, setInfo] = useState<RemoteInfo | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    remoteInfo()
      .then((i) => {
        if (!cancelled) setInfo(i);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className={styles.backdrop}>
      <div className={styles.dialog} role="dialog" aria-label="Use phone as remote">
        <h2>Use phone as remote</h2>
        {failed && <p>Could not read the remote settings.</p>}
        {!failed && !info && <p>Loading…</p>}
        {info && !info.enabled && (
          <p>
            Restart FacadeKit with <code>facadekit serve --lan</code> to use a phone on your Wi-Fi.
          </p>
        )}
        {info?.enabled && (
          <>
            <p>
              On your phone (same Wi-Fi), scan a code or type the address, then enter the pairing
              code.
            </p>
            {info.urls.length === 0 && <p>No network address was found. Check your Wi-Fi.</p>}
            {info.urls.map((base) => {
              const url = `${base}/remote/${encodeURIComponent(projectId)}`;
              return (
                <div key={base} className={styles.entry}>
                  <QrCode text={url} />
                  <code>{url}</code>
                </div>
              );
            })}
            <p>
              Pairing code: <strong className={styles.code}>{info.code}</strong>
            </p>
          </>
        )}
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
    </div>
  );
}
