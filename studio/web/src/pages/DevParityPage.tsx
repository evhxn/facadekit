import { useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { assetUrl, getProject } from "../api/client";
import { PreviewCompositor } from "../gl/compositor";

declare global {
  interface Window {
    __parityReady?: boolean;
    __parityError?: string;
  }
}

/** Dev-only page used by the Playwright parity test: renders one frame at the exact projector size. */
export default function DevParityPage() {
  const [params] = useSearchParams();
  const ref = useRef<HTMLCanvasElement>(null);
  const projectId = params.get("project") ?? "";
  const tMs = Number(params.get("t") ?? "0");

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas || !projectId) return;
    let compositor: PreviewCompositor | null = null;
    let cancelled = false;
    window.__parityReady = false;
    void (async () => {
      try {
        const project = await getProject(projectId);
        if (cancelled) return;
        const { width, height } = project.projector;
        canvas.width = width;
        canvas.height = height;
        canvas.style.width = `${width}px`;
        canvas.style.height = `${height}px`;
        compositor = new PreviewCompositor(canvas, {
          assetUrl: (aid) => assetUrl(projectId, aid),
          preserveDrawingBuffer: true,
        });
        compositor.setProject(project);
        compositor.render(tMs, false);
        await compositor.ready();
        compositor.render(tMs, false);
        window.__parityReady = true;
      } catch (e) {
        window.__parityError = e instanceof Error ? e.message : String(e);
      }
    })();
    return () => {
      cancelled = true;
      compositor?.dispose();
    };
  }, [projectId, tMs]);

  return <canvas id="parity-canvas" ref={ref} style={{ display: "block" }} />;
}
