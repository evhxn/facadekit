export interface Quad {
  id: string;
  name: string;
  quad: [number, number][];
  selected: boolean;
  /** Mesh editor preview: grid polylines in projector px. */
  grid?: [number, number][][];
}

export interface OutlinesMessage {
  type: "outlines";
  seq: number;
  width: number;
  height: number;
  surfaces: Quad[];
}

export interface TransportMessage {
  type: "transport";
  seq: number;
  playing: boolean;
  t_ms: number;
  revision: number;
}

export type ServerMessage =
  | { type: "image"; seq: number; url: string }
  | { type: "blank"; seq: number }
  | { type: "white"; seq: number }
  | { type: "grid"; seq: number }
  | OutlinesMessage
  | { type: "compositor"; seq: number; project_id: string }
  | TransportMessage;

export type ConnectionStatus = "connected" | "reconnecting";

export interface ProjectorHandlers {
  onOpen?: () => void;
  /** Resolve to ack after two animation frames; reject to withhold the ack. */
  onMessage: (msg: ServerMessage) => void | Promise<void>;
  onStatus?: (status: ConnectionStatus) => void;
  /** Close code 4000: another projector page took over. */
  onTakenOver?: () => void;
}

export interface ProjectorConnection {
  close(): void;
  sendHello(w: number, h: number, dpr: number): void;
  ack(seq: number): void;
}

export const TAKEN_OVER_CODE = 4000;
export const RECONNECT_DELAY_MS = 1000;

const KNOWN_TYPES = new Set([
  "image",
  "blank",
  "white",
  "grid",
  "outlines",
  "compositor",
  "transport",
]);

function parseMessage(data: unknown): ServerMessage | null {
  if (typeof data !== "string") return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(data);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null) return null;
  const m = parsed as { type?: unknown; seq?: unknown };
  if (typeof m.type !== "string" || !KNOWN_TYPES.has(m.type)) return null;
  if (typeof m.seq !== "number") return null;
  return parsed as ServerMessage;
}

function defaultUrl(): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws/projector`;
}

function twoFrames(): Promise<void> {
  return new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  });
}

export function connectProjector(
  handlers: ProjectorHandlers,
  url: string = defaultUrl(),
): ProjectorConnection {
  let ws: WebSocket | null = null;
  let closed = false;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const send = (obj: unknown) => {
    if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
  };

  const open = () => {
    const sock = new WebSocket(url);
    ws = sock;
    sock.onopen = () => {
      handlers.onStatus?.("connected");
      handlers.onOpen?.();
    };
    sock.onmessage = (ev: MessageEvent) => {
      const msg = parseMessage(ev.data);
      if (!msg) return;
      void (async () => {
        try {
          await handlers.onMessage(msg);
        } catch {
          return; // no ack: the backend times out and reports it
        }
        await twoFrames();
        if (ws === sock) send({ type: "ack", seq: msg.seq });
      })();
    };
    sock.onclose = (ev: CloseEvent) => {
      if (ws === sock) ws = null;
      if (closed) return;
      if (ev.code === TAKEN_OVER_CODE) {
        closed = true;
        handlers.onTakenOver?.();
        return;
      }
      handlers.onStatus?.("reconnecting");
      timer = setTimeout(open, RECONNECT_DELAY_MS);
    };
  };

  open();

  return {
    close() {
      closed = true;
      if (timer) clearTimeout(timer);
      ws?.close();
    },
    sendHello(width, height, dpr) {
      send({ type: "hello", width, height, dpr });
    },
    ack(seq) {
      send({ type: "ack", seq });
    },
  };
}
