import { useCallback, useEffect, useRef, useState } from "react";
import { drawBlank, drawGrid, drawImage, drawOutlines, drawText, drawWhite } from "./draw";
import {
  connectProjector,
  type ConnectionStatus,
  type ProjectorConnection,
  type ServerMessage,
  type TransportMessage,
} from "./projectorSocket";
import styles from "./ProjectorPage.module.css";

export interface LiveRenderer {
  start(projectId: string, canvas: HTMLCanvasElement): void;
  transport(msg: TransportMessage): void;
  stop(): void;
}

let liveRenderer: LiveRenderer | null = null;

/** Hook for T-033 to plug in the WebGL compositor. */
export function setLiveRenderer(r: LiveRenderer | null): void {
  liveRenderer = r;
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => img.decode().then(() => resolve(img), reject);
    img.onerror = () => reject(new Error(`image failed to load: ${url}`));
    img.src = url;
  });
}

function physicalSize(): { w: number; h: number; dpr: number } {
  const dpr = window.devicePixelRatio || 1;
  return {
    w: Math.round(window.innerWidth * dpr),
    h: Math.round(window.innerHeight * dpr),
    dpr,
  };
}

export default function ProjectorPage() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const connRef = useRef<ProjectorConnection | null>(null);
  const lastRef = useRef<{ msg: ServerMessage; img?: HTMLImageElement } | null>(null);
  const liveActive = useRef(false);
  const [status, setStatus] = useState<ConnectionStatus>("reconnecting");
  const [fullscreen, setFullscreen] = useState(false);
  const [takenOver, setTakenOver] = useState(false);
  const [size, setSize] = useState(() => physicalSize());

  const render = useCallback((entry: { msg: ServerMessage; img?: HTMLImageElement }) => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const { width: w, height: h } = canvas;
    const { msg } = entry;
    if (msg.type !== "compositor" && msg.type !== "transport" && liveActive.current) {
      liveRenderer?.stop();
      liveActive.current = false;
    }
    switch (msg.type) {
      case "blank":
        drawBlank(ctx, w, h);
        break;
      case "white":
        drawWhite(ctx, w, h);
        break;
      case "grid":
        drawGrid(ctx, w, h);
        break;
      case "image":
        if (entry.img) drawImage(ctx, entry.img, w, h);
        break;
      case "outlines":
        drawOutlines(ctx, msg, w, h);
        break;
      case "compositor":
        if (liveRenderer) {
          liveRenderer.start(msg.project_id, canvas);
          liveActive.current = true;
        } else {
          drawText(ctx, w, h, "Live preview");
        }
        break;
      case "transport":
        if (liveRenderer && liveActive.current) liveRenderer.transport(msg);
        else drawText(ctx, w, h, "Live preview");
        break;
    }
  }, []);

  const resizeAndHello = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const s = physicalSize();
    canvas.width = s.w;
    canvas.height = s.h;
    setSize(s);
    connRef.current?.sendHello(window.innerWidth, window.innerHeight, s.dpr);
    if (lastRef.current) render(lastRef.current);
  }, [render]);

  useEffect(() => {
    const conn = connectProjector({
      onOpen: resizeAndHello,
      onStatus: setStatus,
      onTakenOver: () => setTakenOver(true),
      onMessage: async (msg) => {
        const entry: { msg: ServerMessage; img?: HTMLImageElement } = { msg };
        if (msg.type === "image") entry.img = await loadImage(msg.url);
        lastRef.current = entry;
        render(entry);
      },
    });
    connRef.current = conn;

    const onFs = () => {
      setFullscreen(document.fullscreenElement !== null);
      resizeAndHello();
    };
    window.addEventListener("resize", resizeAndHello);
    document.addEventListener("fullscreenchange", onFs);

    // devicePixelRatio changes (browser zoom, moving between displays)
    let mq: MediaQueryList | null = null;
    const watchDpr = () => {
      mq?.removeEventListener("change", onDpr);
      mq = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`);
      mq.addEventListener("change", onDpr);
    };
    const onDpr = () => {
      resizeAndHello();
      watchDpr();
    };
    if (typeof window.matchMedia === "function") watchDpr();

    const canvas = canvasRef.current;
    if (canvas) {
      const s = physicalSize();
      canvas.width = s.w;
      canvas.height = s.h;
    }

    return () => {
      conn.close();
      connRef.current = null;
      window.removeEventListener("resize", resizeAndHello);
      document.removeEventListener("fullscreenchange", onFs);
      mq?.removeEventListener("change", onDpr);
      if (liveActive.current) liveRenderer?.stop();
      liveActive.current = false;
    };
  }, [render, resizeAndHello]);

  const goFullscreen = () => {
    void document.documentElement.requestFullscreen();
  };

  if (takenOver) {
    return <div className={styles.message}>Another projector window is active</div>;
  }

  return (
    <div className={styles.root}>
      <canvas
        ref={canvasRef}
        className={`${styles.canvas} ${fullscreen ? styles.fullscreen : ""}`}
      />
      {!fullscreen && (
        <div className={styles.overlay}>
          <h1>Projector</h1>
          <p>Drag this window to the projector, then click here</p>
          <button type="button" className={styles.button} onClick={goFullscreen}>
            Go fullscreen
          </button>
          <p>
            {size.w} × {size.h} (scale {size.dpr})
          </p>
          <p>
            <span
              className={`${styles.status} ${status === "connected" ? styles.connected : ""}`}
            />
            {status === "connected" ? "Connected" : "Reconnecting…"}
          </p>
        </div>
      )}
    </div>
  );
}
