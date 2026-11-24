import { useEffect, useState } from "react";
import { Link, NavLink, useLocation, useMatch, useRoutes } from "react-router-dom";
import { listRenders } from "./api/client";
import styles from "./App.module.css";
import DiagnosticsButton from "./components/DiagnosticsButton";
import HelpDialog from "./components/HelpDialog";
import { isTyping } from "./components/keys";
import SaveStatus, { SaveProblem } from "./components/SaveStatus";
import Toaster from "./components/Toaster";
import { PROJECTOR_PATH, routes } from "./routes";
import { useProjectStore } from "./state/projectStore";

const NAV = ["setup", "surfaces", "arrange", "publish"] as const;

function Header({ onHelp }: { onHelp: () => void }) {
  const match = useMatch("/p/:projectId/*");
  const projectId = match?.params.projectId;
  const project = useProjectStore((s) => s.project);
  const { pathname } = useLocation();
  const [renderCount, setRenderCount] = useState(0);

  useEffect(() => {
    if (!projectId) return;
    let live = true;
    listRenders(projectId)
      .then((r) => live && setRenderCount(r.length))
      .catch(() => live && setRenderCount(0));
    return () => {
      live = false;
    };
  }, [projectId, pathname]);

  const loaded = project !== null && project.id === projectId;
  const done: Record<(typeof NAV)[number], boolean> = {
    setup: loaded && project.calibration_id !== null,
    surfaces: loaded && project.surfaces.length > 0,
    arrange: loaded && project.timeline.tracks.some((t) => t.clips.length > 0),
    publish: renderCount > 0,
  };
  return (
    <header className={styles.header}>
      <Link to="/" className={styles.brand} title="All projects">
        FacadeKit
      </Link>
      {projectId && loaded ? (
        <span className={styles.projectBox}>
          <span className={styles.project}>{project.name}</span>
          <SaveStatus />
        </span>
      ) : null}
      {projectId && (
        <nav className={styles.nav} aria-label="Steps">
          {NAV.map((name) => (
            <NavLink
              key={name}
              to={`/p/${projectId}/${name}`}
              className={`${styles.step} ${done[name] ? styles.done : ""}`}
              title={done[name] ? "Done" : undefined}
            >
              {name.charAt(0).toUpperCase() + name.slice(1)}
            </NavLink>
          ))}
        </nav>
      )}
      <span className={styles.tools}>
        <button
          type="button"
          onClick={onHelp}
          aria-keyshortcuts="?"
          title="How FacadeKit works, and keyboard shortcuts (?)"
        >
          Help
        </button>
        <DiagnosticsButton />
      </span>
    </header>
  );
}

export default function App() {
  const element = useRoutes(routes);
  const isProjector = useMatch(PROJECTOR_PATH) !== null;
  const inProject = useMatch("/p/:projectId/*") !== null;
  const [help, setHelp] = useState(false);

  // "?" opens the help anywhere except while typing.
  useEffect(() => {
    if (isProjector) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "?" || e.ctrlKey || e.metaKey || e.altKey || isTyping(e.target)) return;
      e.preventDefault();
      setHelp(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isProjector]);

  if (isProjector) return <div className={styles.projector}>{element}</div>;
  return (
    <>
      <Header onHelp={() => setHelp(true)} />
      <main className={styles.main}>
        {inProject ? <SaveProblem /> : null}
        {element}
      </main>
      {help ? <HelpDialog onClose={() => setHelp(false)} /> : null}
      <Toaster />
    </>
  );
}
