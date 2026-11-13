import { useEffect, useState } from "react";
import { remoteInfo, type RemoteInfo } from "../api/client";
import QrCode from "./QrCode";
import styles from "./RemoteDialog.module.css";

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
                  <QrCode text={url} className={styles.qr} />
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
