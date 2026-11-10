import type {
  Calibration,
  CalibSettings,
  CameraInfo,
  FppProbe,
  FppStatus,
  Health,
  Job,
  Project,
  ProjectorSpec,
  ProjectorStatus,
  ProjectSummary,
  RenderInfo,
  Asset,
} from "./types";

const BASE = "/api/v1";

export class ApiError extends Error {
  code: string;
  status: number;
  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

function errorFromBody(status: number, text: string): ApiError {
  let code = "HTTP_ERROR";
  let message = `Request failed (${status})`;
  try {
    const parsed: unknown = JSON.parse(text);
    const err = (parsed as { error?: { code?: unknown; message?: unknown } } | null)?.error;
    if (err && typeof err.code === "string") code = err.code;
    if (err && typeof err.message === "string") message = err.message;
  } catch {
    // keep defaults
  }
  return new ApiError(code, message, status);
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const init: RequestInit = { method, headers };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let res: Response;
  try {
    res = await fetch(BASE + path, init);
  } catch (e) {
    throw new ApiError("NETWORK", e instanceof Error ? e.message : "Network error", 0);
  }
  const text = await res.text();
  if (!res.ok) throw errorFromBody(res.status, text);
  if (res.status === 204 || text === "") return undefined as T;
  return JSON.parse(text) as T;
}

const enc = encodeURIComponent;

export const health = () => request<Health>("GET", "/health");

export const listProjects = () => request<ProjectSummary[]>("GET", "/projects");
export const createProject = (name: string, projector?: ProjectorSpec) =>
  request<Project>("POST", "/projects", { name, projector });
export const getProject = (id: string) => request<Project>("GET", `/projects/${enc(id)}`);
export const putProject = (project: Project) =>
  request<Project>("PUT", `/projects/${enc(project.id)}`, project);
export const deleteProject = (id: string) => request<void>("DELETE", `/projects/${enc(id)}`);

export function uploadAsset(
  pid: string,
  file: File,
  onProgress?: (fraction: number) => void,
): Promise<{ asset: Asset; project: Project }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${BASE}/projects/${enc(pid)}/assets`);
    xhr.setRequestHeader("Accept", "application/json");
    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && e.total > 0) onProgress(e.loaded / e.total);
      };
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as { asset: Asset; project: Project });
        } catch {
          reject(new ApiError("HTTP_ERROR", "Invalid server response", xhr.status));
        }
      } else {
        reject(errorFromBody(xhr.status, xhr.responseText));
      }
    };
    xhr.onerror = () => reject(new ApiError("NETWORK", "Network error", 0));
    xhr.onabort = () => reject(new ApiError("NETWORK", "Upload aborted", 0));
    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
  });
}
export const assetUrl = (pid: string, aid: string) =>
  `${BASE}/projects/${enc(pid)}/assets/${enc(aid)}/file`;
export const assetThumbUrl = (pid: string, aid: string) =>
  `${BASE}/projects/${enc(pid)}/assets/${enc(aid)}/thumb`;
export const deleteAsset = (pid: string, aid: string) =>
  request<Project>("DELETE", `/projects/${enc(pid)}/assets/${enc(aid)}`);

export const listCameras = () => request<CameraInfo[]>("GET", "/devices/cameras");
export const cameraSnapshotUrl = (cid: string) =>
  `${BASE}/devices/cameras/${enc(cid)}/snapshot.jpg`;
export const setCameraControls = (
  cid: string,
  controls: Record<string, number | string | boolean>,
) =>
  request<{ applied: Record<string, unknown> }>(
    "POST",
    `/devices/cameras/${enc(cid)}/controls`,
    controls,
  );

export const projectorStatus = () => request<ProjectorStatus>("GET", "/projector/status");
export const projectorShow = (body: { mode: string; project_id?: string; selected_id?: string }) =>
  request<ProjectorStatus>("POST", "/projector/show", body);
export const projectorTransport = (body: {
  project_id: string;
  playing: boolean;
  t_ms: number;
  revision: number;
}) => request<void>("POST", "/projector/transport", body);

export const startCalibration = (
  pid: string,
  body: { mode: "scan" | "manual"; camera_id?: string; settings?: CalibSettings },
) => request<{ job_id: string }>("POST", `/projects/${enc(pid)}/calibrations`, body);
export const getCalibration = (pid: string, cid: string) =>
  request<Calibration>("GET", `/projects/${enc(pid)}/calibrations/${enc(cid)}`);
export const projViewUrl = (pid: string, cid: string) =>
  `${BASE}/projects/${enc(pid)}/calibrations/${enc(cid)}/proj_view.png`;
export const measuredMaskUrl = (pid: string, cid: string) =>
  `${BASE}/projects/${enc(pid)}/calibrations/${enc(cid)}/measured_mask.png`;
export const frameUrl = (pid: string, tMs: number, scale: number) =>
  `${BASE}/projects/${enc(pid)}/frame?t_ms=${tMs}&scale=${scale}`;

export const startRender = (
  pid: string,
  body: { preset: string; include_audio: boolean; kind: string },
) => request<{ job_id: string }>("POST", `/projects/${enc(pid)}/renders`, body);
export const listRenders = (pid: string) =>
  request<RenderInfo[]>("GET", `/projects/${enc(pid)}/renders`);
export const renderFileUrl = (pid: string, rid: string) =>
  `${BASE}/projects/${enc(pid)}/renders/${enc(rid)}/file`;

export const fppProbe = (host: string) => request<FppProbe>("POST", "/fpp/probe", { host });
export const fppStatus = (host: string) =>
  request<FppStatus>("GET", `/fpp/status?host=${enc(host)}`);
export const fppSequences = (host: string) =>
  request<{ sequences: string[] }>("GET", `/fpp/sequences?host=${enc(host)}`);
export const publish = (
  pid: string,
  body: {
    host: string;
    render_id: string;
    playlist_name?: string;
    start: boolean;
    sequence_name?: string;
  },
) => request<{ job_id: string }>("POST", `/projects/${enc(pid)}/publish`, body);

export const getJob = (id: string) => request<Job>("GET", `/jobs/${enc(id)}`);
export const cancelJob = (id: string) => request<Job>("POST", `/jobs/${enc(id)}/cancel`);
