import { useState } from "react";
import { getDiagnostics, recentErrors } from "../api/client";
import styles from "./DiagnosticsButton.module.css";

/** Everything a bug report needs, as text. Works with the server down (browser part only). */
export async function collectDiagnostics(): Promise<string> {
  let server: unknown;
  try {
    server = await getDiagnostics();
  } catch (e) {
    server = { unavailable: e instanceof Error ? e.message : String(e) };
  }
  return JSON.stringify(
    {
      collected_at: new Date().toISOString(),
      browser: {
        user_agent: navigator.userAgent,
        page: window.location.pathname,
        window: `${window.innerWidth}x${window.innerHeight} @${window.devicePixelRatio}`,
        recent_errors: recentErrors(),
      },
      server,
    },
    null,
    2,
  );
}

type State = "idle" | "busy" | "copied" | "manual";

/** Copies the diagnostic info; when the clipboard is not allowed, shows it to copy by hand. */
export default function DiagnosticsButton({ label = "Copy diagnostic info" }: { label?: string }) {
  const [state, setState] = useState<State>("idle");
  const [text, setText] = useState("");

  const copy = async () => {
    setState("busy");
    const info = await collectDiagnostics();
    setText(info);
    try {
      await navigator.clipboard.writeText(info);
      setState("copied");
    } catch {
      setState("manual");
    }
  };

  return (
    <span className={styles.wrap}>
      <button type="button" className={styles.button} onClick={copy} disabled={state === "busy"}>
        {state === "busy" ? "Collecting…" : label}
      </button>
      {state === "copied" && (
        <span role="status" className={styles.note}>
          Copied. Paste it into your bug report.
        </span>
      )}
      {state === "manual" && (
        <textarea
          className={styles.manual}
          aria-label="Diagnostic info"
          readOnly
          value={text}
          onFocus={(e) => e.currentTarget.select()}
        />
      )}
    </span>
  );
}
