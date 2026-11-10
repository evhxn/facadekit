import type { RouteObject } from "react-router-dom";
import DevParityPage from "./pages/DevParityPage";
import ArrangePage from "./pages/ArrangePage";
import ProjectsPage from "./pages/ProjectsPage";
import PublishPage from "./pages/PublishPage";
import SetupPage from "./pages/SetupPage";
import SurfacesPage from "./pages/SurfacesPage";
import ProjectorPage from "./projector/ProjectorPage";

export const PROJECTOR_PATH = "/projector";

export const routes: RouteObject[] = [
  { path: "/", element: <ProjectsPage /> },
  { path: "/p/:projectId/setup", element: <SetupPage /> },
  { path: "/p/:projectId/surfaces", element: <SurfacesPage /> },
  { path: "/p/:projectId/arrange", element: <ArrangePage /> },
  { path: "/p/:projectId/publish", element: <PublishPage /> },
  { path: PROJECTOR_PATH, element: <ProjectorPage /> },
];

if (import.meta.env.DEV || new URLSearchParams(window.location.search).get("dev") === "1") {
  routes.push({ path: "/dev/parity", element: <DevParityPage /> });
}
