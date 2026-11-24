import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, createProject, deleteProject, listProjects } from "../api/client";
import { userMessage } from "../api/errors";
import type { ProjectSummary } from "../api/types";
import ErrorBanner from "../components/ErrorBanner";
import WelcomeGuide from "../components/WelcomeGuide";
import styles from "./ProjectsPage.module.css";
import TrySample from "./projects/TrySample";

const PRESETS = ["1920x1080", "1280x720", "1280x800", "854x480", "800x480"];
const FPS = 30;

function fail(e: unknown, fallback: string): string {
  return e instanceof ApiError ? userMessage(e.code, e.message) : fallback;
}

export default function ProjectsPage() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [choice, setChoice] = useState("1920x1080");
  const [customW, setCustomW] = useState("1920");
  const [customH, setCustomH] = useState("1080");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setProjects(await listProjects());
    } catch (e) {
      setError(
        fail(
          e,
          "Could not load the projects. Check that FacadeKit is running, then reload this page.",
        ),
      );
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onDelete = async (p: ProjectSummary) => {
    if (!window.confirm(`Delete "${p.name}"? This cannot be undone.`)) return;
    try {
      await deleteProject(p.id);
      await refresh();
    } catch (e) {
      setError(fail(e, "Could not delete the project. Try again."));
    }
  };

  const onCreate = async (ev: FormEvent) => {
    ev.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Type a name for the project, then press New project.");
      return;
    }
    let width: number;
    let height: number;
    if (choice === "custom") {
      width = Number(customW);
      height = Number(customH);
    } else {
      const [w, h] = choice.split("x").map(Number);
      width = w ?? 0;
      height = h ?? 0;
    }
    if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1) {
      setError("Enter the projector width and height as whole numbers, for example 1920 and 1080.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const project = await createProject(trimmed, { width, height, fps: FPS });
      navigate(`/p/${project.id}/setup`);
    } catch (e) {
      setError(
        fail(e, "Could not create the project. Check that FacadeKit is running, then try again."),
      );
      setBusy(false);
    }
  };

  return (
    <main className={styles.page}>
      <h1>Projects</h1>
      <p className={styles.lead}>
        A project is one house and one projector: scan it, mark the surfaces, put videos on them,
        and send the show to your Falcon Player.
      </p>
      {error ? <ErrorBanner message={error} /> : null}
      {projects !== null ? (
        <WelcomeGuide
          first={projects.length === 0}
          onStart={() => {
            const field = document.getElementById("project-name");
            field?.scrollIntoView?.({ block: "center" });
            field?.focus();
          }}
        />
      ) : null}
      <div className={styles.columns}>
        <section aria-label="Your projects">
          {projects === null && !error ? (
            <p role="status" className={styles.empty}>
              Loading projects…
            </p>
          ) : null}
          {projects && projects.length === 0 ? (
            <p className={styles.empty}>No projects yet. Make your first one with the form.</p>
          ) : null}
          <ul className={styles.list}>
            {(projects ?? []).map((p) => (
              <li key={p.id} className={styles.item}>
                <span className={styles.text}>
                  <span className={styles.name}>{p.name}</span>
                  <span className={styles.date}>
                    Changed {new Date(p.modified_at).toLocaleString()}
                  </span>
                </span>
                <span className={styles.actions}>
                  <button
                    type="button"
                    onClick={() => navigate(`/p/${p.id}/surfaces`)}
                    className="primary"
                  >
                    Open
                  </button>
                  <button type="button" onClick={() => navigate(`/p/${p.id}/setup`)}>
                    Set up
                  </button>
                  <button type="button" onClick={() => void onDelete(p)} className="danger">
                    Delete
                  </button>
                </span>
              </li>
            ))}
          </ul>
        </section>

        <form className={styles.form} onSubmit={(e) => void onCreate(e)}>
          <h2>New project</h2>
          <label className={styles.field}>
            Project name
            <input
              id="project-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="For example: Our house, Halloween"
              required
            />
          </label>
          <label className={styles.field}>
            Projector resolution
            <select value={choice} onChange={(e) => setChoice(e.target.value)}>
              {PRESETS.map((p) => (
                <option key={p} value={p}>
                  {p.replace("x", "×")}
                </option>
              ))}
              <option value="custom">Custom</option>
            </select>
          </label>
          {choice === "custom" ? (
            <div className={styles.custom}>
              <label>
                Width
                <input
                  type="number"
                  min={1}
                  value={customW}
                  onChange={(e) => setCustomW(e.target.value)}
                />
              </label>
              <label>
                Height
                <input
                  type="number"
                  min={1}
                  value={customH}
                  onChange={(e) => setCustomH(e.target.value)}
                />
              </label>
            </div>
          ) : null}
          <p className={styles.help}>
            Use your projector&apos;s <em>native</em> resolution (see its manual or label), not the
            highest it accepts.
          </p>
          <button type="submit" disabled={busy} className="primary">
            {busy ? "Creating…" : "New project"}
          </button>
        </form>
      </div>
      <TrySample />
    </main>
  );
}
