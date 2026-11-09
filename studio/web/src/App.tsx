import { NavLink, useMatch, useRoutes } from "react-router-dom";
import styles from "./App.module.css";
import { PROJECTOR_PATH, routes } from "./routes";

const NAV = ["setup", "surfaces", "arrange", "publish"] as const;

function Header() {
  const match = useMatch("/p/:projectId/*");
  const projectId = match?.params.projectId;
  return (
    <header className={styles.header}>
      <strong>FacadeKit</strong>
      {projectId && (
        <nav className={styles.nav}>
          {NAV.map((name) => (
            <NavLink key={name} to={`/p/${projectId}/${name}`}>
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
