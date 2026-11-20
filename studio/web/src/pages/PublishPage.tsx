import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { listRenders } from "../api/client";
import type { RenderInfo } from "../api/types";
import { useProjectStore } from "../state/projectStore";
import FppPanel from "./publish/FppPanel";
import RenderPanel from "./publish/RenderPanel";
import EditorHeader from "../components/EditorHeader";
import PageState from "../components/PageState";
import styles from "./PublishPage.module.css";

export default function PublishPage() {
  const { projectId } = useParams();
  const project = useProjectStore((s) => s.project);
  const load = useProjectStore((s) => s.load);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [renders, setRenders] = useState<RenderInfo[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (projectId && useProjectStore.getState().project?.id !== projectId) {
      load(projectId).catch(() =>
        setLoadError("Could not load the project. Go back to Projects and open it again."),
      );
    }
  }, [projectId, load]);

  const pid = project?.id ?? null;

  const refresh = useCallback(
    async (select?: string) => {
      if (!pid) return;
      const list = await listRenders(pid);
      if (!mounted.current) return;
      setRenders(list);
      setSelectedId((cur) => {
        if (select && list.some((r) => r.render_id === select)) return select;
        if (cur && list.some((r) => r.render_id === cur)) return cur;
        return list[0]?.render_id ?? null;
      });
    },
    [pid],
  );

  useEffect(() => {
    refresh().catch(() => {});
  }, [refresh]);

  if (loadError) {
    return (
      <PageState
        kind="error"
        title="Could not open this project"
        action={
          <Link to="/" className="button primary">
            All projects
          </Link>
        }
      >
        <p>{loadError}</p>
      </PageState>
    );
  }
  if (!project || project.id !== projectId) {
    return <PageState kind="loading" title="Loading the project" />;
  }
  const clipCount = project.timeline.tracks.reduce((n, t) => n + t.clips.length, 0);

  return (
    <div className={styles.page}>
      <EditorHeader
        title="Publish"
        help="Render the show as a video for the projector, then send it to Falcon Player (FPP) on your Raspberry Pi."
      />
      <ol className={styles.steps}>
        <li data-done={clipCount > 0 || undefined}>
          Arrange:{" "}
          {clipCount > 0 ? `${clipCount} clip${clipCount === 1 ? "" : "s"}` : "no clips yet"}
        </li>
        <li data-done={renders.length > 0 || undefined}>
          Render: {renders.length > 0 ? `${renders.length} made` : "not yet"}
        </li>
        <li>Send to the Pi</li>
      </ol>
      <RenderPanel
        project={project}
        renders={renders}
        selectedId={selectedId}
        onSelect={setSelectedId}
        onRendered={(rid) => refresh(rid)}
      />
      <FppPanel
        project={project}
        selectedRenderId={selectedId}
        onRendered={(rid) => refresh(rid)}
      />
      <nav className={styles.nav}>
        <Link to={`/p/${project.id}/arrange`} className={styles.back}>
          Back
        </Link>
      </nav>
    </div>
  );
}
