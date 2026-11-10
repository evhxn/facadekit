export interface FrameSource {
  texture(gl: WebGL2RenderingContext, sourceMs: number): WebGLTexture | null;
  width: number;
  height: number;
}

export type VideoAction = "none" | "seek";

/**
 * Decide whether a video element needs a seek. Playing tolerates 80 ms of drift,
 * paused tolerates 20 ms.
 */
export function videoSyncAction(
  currentTimeS: number,
  targetS: number,
  playing: boolean,
): VideoAction {
  const tol = playing ? 0.08 : 0.02;
  return Math.abs(currentTimeS - targetS) > tol ? "seek" : "none";
}

/** Clamp a source time (ms) into a video's playable range; the last frame is held. */
export function clampVideoSeconds(sourceMs: number, durationMs: number | null): number {
  let s = Math.max(0, sourceMs) / 1000;
  if (durationMs !== null && durationMs > 0) s = Math.min(s, durationMs / 1000);
  return s;
}

export interface ClipTextureSource extends FrameSource {
  /** Called every render before texture(). */
  setPlaying(playing: boolean): void;
  /** Pause playback of sources that are not about to be used. */
  idle(): void;
  dispose(gl: WebGL2RenderingContext): void;
}

function makeTexture(gl: WebGL2RenderingContext): WebGLTexture | null {
  const tex = gl.createTexture();
  if (!tex) return null;
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  return tex;
}

function setUploadState(gl: WebGL2RenderingContext): void {
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
  gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
}

export class ImageSource implements ClipTextureSource {
  width = 0;
  height = 0;
  private tex: WebGLTexture | null = null;
  private img = new Image();
  private loaded = false;
  private uploaded = false;
  private disposed = false;

  constructor(url: string) {
    this.img.crossOrigin = "anonymous";
    this.img.onload = () => {
      if (this.disposed) return;
      this.width = this.img.naturalWidth;
      this.height = this.img.naturalHeight;
      this.loaded = true;
    };
    this.img.src = url;
  }

  setPlaying(): void {}
  idle(): void {}

  texture(gl: WebGL2RenderingContext): WebGLTexture | null {
    if (!this.loaded) return null;
    if (!this.uploaded) {
      this.tex = makeTexture(gl);
      if (!this.tex) return null;
      setUploadState(gl);
      gl.bindTexture(gl.TEXTURE_2D, this.tex);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, this.img);
      this.uploaded = true;
    }
    return this.tex;
  }

  dispose(gl: WebGL2RenderingContext): void {
    this.disposed = true;
    if (this.tex) gl.deleteTexture(this.tex);
    this.tex = null;
    this.img.onload = null;
    this.img.src = "";
  }
}

export class VideoSource implements ClipTextureSource {
  width = 0;
  height = 0;
  private tex: WebGLTexture | null = null;
  private video: HTMLVideoElement;
  private playing = false;

  constructor(
    url: string,
    private durationMs: number | null,
  ) {
    const v = document.createElement("video");
    v.muted = true;
    v.playsInline = true;
    v.preload = "auto";
    v.crossOrigin = "anonymous";
    v.src = url;
    this.video = v;
  }

  setPlaying(playing: boolean): void {
    this.playing = playing;
  }

  idle(): void {
    if (!this.video.paused) this.video.pause();
  }

  texture(gl: WebGL2RenderingContext, sourceMs: number): WebGLTexture | null {
    const v = this.video;
    const s = clampVideoSeconds(sourceMs, this.durationMs);
    if (videoSyncAction(v.currentTime, s, this.playing) === "seek") v.currentTime = s;
    if (this.playing) {
      if (v.paused) void v.play().catch(() => undefined);
    } else if (!v.paused) {
      v.pause();
    }
    if (v.readyState < 2) return this.tex;
    this.width = v.videoWidth;
    this.height = v.videoHeight;
    if (!this.tex) this.tex = makeTexture(gl);
    if (!this.tex) return null;
    setUploadState(gl);
    gl.bindTexture(gl.TEXTURE_2D, this.tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, v);
    return this.tex;
  }

  dispose(gl: WebGL2RenderingContext): void {
    this.video.pause();
    this.video.removeAttribute("src");
    this.video.load();
    if (this.tex) gl.deleteTexture(this.tex);
    this.tex = null;
  }
}
