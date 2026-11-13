import { useState } from "react";
import { ApiError, remotePair } from "../api/client";
import styles from "../pages/RemotePage.module.css";

/** Enter the 6-digit code shown on the laptop (T-117 pairing); used by the phone pages. */
export default function PairForm({ onPaired }: { onPaired: () => void | Promise<void> }) {
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);

  const pair = async () => {
    setError(null);
    try {
      await remotePair(code);
      setCode("");
      await onPaired();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Pairing failed.");
    }
  };

  return (
    <div className={`${styles.page} ${styles.pair}`}>
      <h1>Pair this phone</h1>
      <p>Enter the 6-digit code shown on your laptop.</p>
      <input
        aria-label="Pairing code"
        inputMode="numeric"
        pattern="[0-9]*"
        maxLength={6}
        value={code}
        onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
      />
      <button type="button" className={styles.big} disabled={code.length !== 6} onClick={pair}>
        Pair
      </button>
      {error && <p className={styles.error}>{error}</p>}
    </div>
  );
}
