export type Pt = [number, number];
export interface ProjectorSpec {
  width: number;
  height: number;
  fps: number;
}
export interface Mask {
  outer: Pt[];
  holes: Pt[][];
}
export interface Mesh {
  cols: number;
  rows: number;
  offsets: Pt[];
}
export interface Surface {
  id: string;
  name: string;
  quad: [Pt, Pt, Pt, Pt];
  mask: Mask | null;
  feather_px: number;
  aspect: number;
  mesh: Mesh | null;
}
export interface Asset {
  id: string;
  kind: "video" | "image" | "audio";
  filename: string;
  original_name: string;
  width: number | null;
  height: number | null;
  duration_ms: number | null;
  fps: number | null;
  has_audio: boolean;
  has_alpha: boolean;
}
export interface Generator {
  type: "solid" | "wash" | "outline" | "strobe";
  params: Record<string, number | string>;
}
export interface Clip {
  id: string;
  asset_id: string | null;
  generator: Generator | null;
  surface_id: string;
  start_ms: number;
  duration_ms: number;
  in_offset_ms: number;
  loop: boolean;
  fit: "fill" | "fit" | "stretch";
  opacity: number;
  fade_in_ms: number;
  fade_out_ms: number;
  blend: "normal" | "add" | "screen";
}
export interface Track {
  id: string;
  name: string;
  clips: Clip[];
}
export interface Timeline {
  duration_ms: number;
  audio_asset_id: string | null;
  tracks: Track[];
}
export interface Marker {
  t_ms: number;
  kind: "beat" | "downbeat" | "onset" | "user";
}
export interface FppTarget {
  host: string | null;
  playlist_name: string | null;
}
export interface Project {
  schema_version: 1;
  id: string;
  name: string;
  revision: number;
  projector: ProjectorSpec;
  calibration_id: string | null;
  surfaces: Surface[];
  assets: Asset[];
  timeline: Timeline;
  markers: Marker[];
  fpp: FppTarget;
}
export interface ProjectSummary {
  id: string;
  name: string;
  modified_at: string;
}
export interface CalibSettings {
  skip_lsb: number;
  settle_ms: number;
  discard_frames: number;
  average_frames: number;
  min_conf?: number;
  /** Crop of the camera picture in full-frame pixels; null or missing = the whole picture. */
  roi?: CameraRoi | null;
}
export interface CameraRoi {
  x: number;
  y: number;
  w: number;
  h: number;
  frame_width: number;
  frame_height: number;
  bin: 1 | 2;
}
export type CameraVerdict = "good" | "marginal" | "too_coarse";
export interface CameraCheck {
  verdict: CameraVerdict;
  reasons: string[];
  camera_width: number;
  camera_height: number;
  lit_box: CameraRoi | null;
  suggested_roi: CameraRoi | null;
  px_per_proj_px: number;
  proj_px_per_cam_px: number;
  stripe_modulation: Record<string, number>;
  snr: number;
  median_contrast: number;
  controls: Record<string, boolean>;
  suggest_1080p: boolean;
}
export interface CalibQuality {
  coverage: number;
  planarity: number;
  median_contrast: number;
  lit_fraction: number;
  px_per_proj_px?: number | null;
  snr?: number | null;
  verdict?: CameraVerdict | null;
}
export interface Calibration {
  id: string;
  created_at: string;
  mode: "scan" | "manual";
  projector_width: number;
  projector_height: number;
  camera_id: string | null;
  camera_width: number | null;
  camera_height: number | null;
  settings: CalibSettings | null;
  quality: CalibQuality | null;
  h_c2p: number[][] | null;
  warnings: string[];
  /** Scan strategy picked by scan memory; null or missing when the caller fixed the settings. */
  strategy?: string | null;
}
export type JobState = "queued" | "running" | "succeeded" | "failed" | "cancelled";
/** POST /projects/{pid}/phone-scans */
export interface PhoneScanStart {
  session_id: string;
  job_id: string;
}
export interface Job {
  id: string;
  kind: "calibration" | "render" | "publish" | "analyze";
  state: JobState;
  progress: number;
  message: string;
  error: { code: string; message: string } | null;
  result: Record<string, unknown> | null;
  created_at: string;
}
export interface CameraInfo {
  id: string;
  name: string;
  width: number;
  height: number;
}
export interface ProjectorStatus {
  connected: boolean;
  width: number | null;
  height: number | null;
  dpr: number | null;
  mode: string;
}
export interface RenderInfo {
  render_id: string;
  filename: string;
  size_bytes: number;
  modified_at: string;
}
export interface FppProbe {
  reachable: boolean;
  version: string | null;
  major: number | null;
  ok: boolean;
  problems: string[];
}
export interface FppStatus {
  status_name: string | null;
  current_playlist: string | null;
  seconds_played: number | null;
}

export interface Health {
  status: string;
  version: string;
  sim: boolean;
}

/** Server -> client messages on /ws/events. */
export type EventMessage =
  | { type: "job"; job: Job }
  | { type: "projector"; status: ProjectorStatus }
  | { type: "project"; project_id: string; revision: number };

export function isFinalJobState(state: JobState): boolean {
  return state === "succeeded" || state === "failed" || state === "cancelled";
}

/** `prefix_` followed by 8 hex characters from crypto.getRandomValues. */
export function newId(prefix: string): string {
  const bytes = new Uint8Array(4);
  crypto.getRandomValues(bytes);
  let hex = "";
  for (const b of bytes) hex += b.toString(16).padStart(2, "0");
  return `${prefix}_${hex}`;
}

export interface LearningStrategy {
  id: string;
  label: string;
  tries: number;
  mistakes: number;
  mean_reward: number;
}

export interface LearningSummary {
  enabled: boolean;
  scans: number;
  strategies: LearningStrategy[];
}
