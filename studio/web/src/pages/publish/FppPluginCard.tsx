import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  fppPlugin,
  fppPluginPair,
  fppPluginPower,
  fppPluginUnpair,
} from "../../api/client";
import { userMessage } from "../../api/errors";
import type { FppPluginProjector, FppPluginSummary } from "../../api/types";
import ErrorBanner from "../../components/ErrorBanner";
import InfoTip from "../../components/InfoTip";
import styles from "./Panels.module.css";

const POWER_LABELS: Record<string, string> = {
  on: "On",
  standby: "Off",
  warmup: "Warming up",
  cooling: "Cooling down",
  busy: "Busy",
  unknown: "Unknown",
};

function projectorLine(p: FppPluginProjector): string {
  const parts = [p.ok ? (POWER_LABELS[p.power] ?? p.power) : "No answer"];
  const lamp = p.lamps?.[0];
  if (lamp) parts.push(`${lamp.hours} h`);
  if (p.error) parts.push(p.error);
  return `${p.name}: ${parts.join(", ")}`;
}

function message(e: unknown, fallback: string): string {
  return e instanceof ApiError ? userMessage(e.code, e.message) : fallback;
}

/**
 * Status of the optional FacadeKit plugin on the connected FPP: projector power, faults, the
 * shows it lists, pairing, and On / Off buttons. Without the plugin it only says that it is
 * optional; publishing works the same either way.
 */
export default function FppPluginCard({
  host,
  onPaired,
  refreshKey = 0,
}: {
  host: string;
  onPaired?: (paired: boolean) => void;
  /** Changing this value makes the card ask the plugin again (e.g. after a publish). */
  refreshKey?: number;
}) {
  const [summary, setSummary] = useState<FppPluginSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const show = useCallback(
    (s: FppPluginSummary) => {
      if (!alive.current) return;
      setSummary(s);
      onPaired?.(s.paired);
    },
    [onPaired],
  );

  // keep: leave the message of the action that asked for this refresh on screen.
  const refresh = useCallback(
    async (keep = false) => {
      setLoading(true);
      if (!keep) setError(null);
      try {
        show(await fppPlugin(host));
      } catch (e) {
        if (alive.current) setError(message(e, "Could not ask FPP about the plugin."));
      } finally {
        if (alive.current) setLoading(false);
      }
    },
    [host, show],
  );

  useEffect(() => {
    void refresh();
  }, [refresh, refreshKey]);

  const pair = async () => {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const s = await fppPluginPair(host, token.trim());
      show(s);
      if (s.paired) {
        setToken("");
        setNote("Paired with the plugin.");
      } else {
        setError(s.problem ?? "The plugin did not accept that token.");
      }
    } catch (e) {
      if (alive.current) setError(message(e, "Pairing failed. Copy the token again from FPP."));
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const unpair = async () => {
    setBusy(true);
    try {
      show(await fppPluginUnpair(host));
      setNote(null);
    } catch (e) {
      if (alive.current) setError(message(e, "Could not forget the token."));
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const power = async (action: "on" | "off") => {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const r = await fppPluginPower(host, action, true);
      const bad = r.results.filter((x) => !x.ok);
      if (!alive.current) return;
      if (bad.length > 0) setError(bad.map((x) => `${x.name}: ${x.message}`).join("; "));
      else setNote(action === "on" ? "Projectors are on." : "Projectors are off.");
      await refresh(true);
    } catch (e) {
      if (alive.current) setError(message(e, "The plugin could not switch the projectors."));
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const health = summary?.health ?? null;

  return (
    <section className={styles.panel} aria-label="Projectors (FPP plugin)">
      <h2>
        Projectors
        <InfoTip term="FacadeKit FPP plugin">
          An optional plugin for Falcon Player that switches the projectors on and off from FPP
          playlists and the scheduler, and lists the shows FacadeKit sent. Without it, sending shows
          works exactly the same.
        </InfoTip>
      </h2>
      {loading && summary === null ? <p className={styles.note}>Checking for the plugin…</p> : null}
      {summary && !summary.present ? (
        <p className={styles.note}>
          The FacadeKit plugin is not installed on this FPP. That is fine: shows are still sent and
          played through FPP itself. Install the plugin to control the projectors from here.
        </p>
      ) : null}
      {summary?.present && !summary.paired ? (
        <>
          <p className={styles.note}>
            Plugin {summary.version} found. On FPP, open Status/Control &gt; FacadeKit Projectors,
            press <strong>Create pairing token</strong> and paste the token here.
          </p>
          <div className={styles.row}>
            <label>
              Pairing token{" "}
              <input
                className={styles.input}
                type="password"
                autoComplete="off"
                value={token}
                onChange={(e) => setToken(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="primary"
              disabled={busy || token.trim() === ""}
              onClick={() => void pair()}
            >
              Pair
            </button>
          </div>
        </>
      ) : null}
      {summary?.paired && health ? (
        <>
          <p className={health.healthy ? styles.ok : styles.warn}>
            {health.healthy
              ? `Plugin ${summary.version}: ${health.summary.on} of ${health.summary.total} projectors on`
              : `Problem: ${health.health?.reason ?? `${health.summary.error} projector(s) not answering`}`}
          </p>
          <ul className={styles.list} aria-label="Projector status">
            {health.projectors.map((p) => (
              <li key={p.id}>{projectorLine(p)}</li>
            ))}
          </ul>
          {health.player ? (
            <p className={styles.note}>
              {health.player.current_playlist
                ? `FPP is playing ${health.player.current_playlist}`
                : "FPP is idle"}
              {` · ${health.shows.length} show${health.shows.length === 1 ? "" : "s"} from FacadeKit on this FPP`}
            </p>
          ) : null}
          <div className={styles.row}>
            <button type="button" disabled={busy} onClick={() => void power("on")}>
              Projectors on
            </button>
            <button type="button" disabled={busy} onClick={() => void power("off")}>
              Projectors off
            </button>
            <button type="button" disabled={busy || loading} onClick={() => void refresh()}>
              Refresh
            </button>
            <button type="button" disabled={busy} onClick={() => void unpair()}>
              Forget token
            </button>
          </div>
        </>
      ) : null}
      {summary?.present && summary.paired && !health ? (
        <p className={styles.note}>Paired, but the plugin sent no status.</p>
      ) : null}
      {note ? <p className={styles.ok}>{note}</p> : null}
      {error ? <ErrorBanner message={error} /> : null}
    </section>
  );
}
