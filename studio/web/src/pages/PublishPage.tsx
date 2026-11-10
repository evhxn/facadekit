import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { listRenders } from "../api/client";
import type { RenderInfo } from "../api/types";
import { useProjectStore } from "../state/projectStore";
import FppPanel from "./publish/FppPanel";
import RenderPanel from "./publish/RenderPanel";
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
      load(projectId).catch(() => setLoadError("Could not load the project."));
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

  if (loadError) return <p role="alert">{loadError}</p>;
  if (!project || project.id !== projectId) return <p>Loading…</p>;

  return (
    <div className={styles.page}>
      <h1>Publish</h1>
      <RenderPanel
        project={project}
        renders={renders}
        selectedId={selectedId}
        onSelect={setSelectedId}
        onRendered={(rid) => refresh(rid)}
      />
      <FppPanel project={project} selectedRenderId={selectedId} />
      <nav className={styles.nav}>
        <Link to={`/p/${project.id}/arrange`}>Back</Link>
      </nav>
    </div>
  );
}
