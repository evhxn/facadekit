import { useEffect, useState } from "react";
import { NavLink, useLocation, useMatch, useRoutes } from "react-router-dom";
import { listRenders } from "./api/client";
import styles from "./App.module.css";
import { PROJECTOR_PATH, routes } from "./routes";
import { useProjectStore } from "./state/projectStore";

const NAV = ["setup", "surfaces", "arrange", "publish"] as const;

function Header() {
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
      <strong className={styles.brand}>FacadeKit</strong>
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
    </header>
  );
}

export default function App() {
  const element = useRoutes(routes);
  const isProjector = useMatch(PROJECTOR_PATH) !== null;
  if (isProjector) return <div className={styles.projector}>{element}</div>;
  return (
    <>
      <Header />
      <main className={styles.main}>{element}</main>
    </>
  );
}
