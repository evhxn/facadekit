import { useRef, useState } from "react";
import { ApiError, assetThumbUrl, deleteAsset, uploadAsset } from "../../api/client";
import { userMessage } from "../../api/errors";
import type { Asset } from "../../api/types";
import { useProjectStore } from "../../state/projectStore";
import styles from "./MediaLibrary.module.css";

interface Upload {
  key: number;
  name: string;
  progress: number;
  error: string | null;
}

export const ASSET_DRAG_TYPE = "application/x-facadekit-asset";

export function formatDuration(ms: number | null): string {
  if (ms === null) return "";
  const s = ms / 1000;
  const m = Math.floor(s / 60);
  return `${m}:${(s - m * 60).toFixed(1).padStart(4, "0")}`;
}

function describeSize(a: Asset): string {
  return a.width && a.height ? `${a.width}×${a.height}` : "";
}

let uploadKey = 0;

export default function MediaLibrary({
  projectId,
  assets,
  audioAssetId,
  onUseAsAudio,
}: {
  projectId: string;
  assets: Asset[];
  audioAssetId: string | null;
  onUseAsAudio: (asset: Asset) => void;
}) {
  const replace = useProjectStore((s) => s.replace);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const patchUpload = (key: number, patch: Partial<Upload>) =>
    setUploads((list) => list.map((u) => (u.key === key ? { ...u, ...patch } : u)));

  async function importFiles(files: File[]) {
    if (files.length === 0) return;
    setDeleteError(null);
    const entries = files.map((file) => ({
      file,
      key: ++uploadKey,
    }));
    setUploads((list) => [
      ...list,
      ...entries.map((e) => ({ key: e.key, name: e.file.name, progress: 0, error: null })),
    ]);
    // Save local edits first: the server's project replaces ours after each upload.
    await useProjectStore.getState().saveNow();
    for (const { file, key } of entries) {
      try {
        const result = await uploadAsset(projectId, file, (f) => patchUpload(key, { progress: f }));
        replace(result.project);
        setUploads((list) => list.filter((u) => u.key !== key));
      } catch (e) {
        patchUpload(key, {
          error: e instanceof ApiError ? userMessage(e.code, e.message) : "Upload failed.",
        });
      }
    }
  }

  async function remove(asset: Asset) {
    setDeleteError(null);
    try {
      await useProjectStore.getState().saveNow();
      replace(await deleteAsset(projectId, asset.id));
    } catch (e) {
      if (e instanceof ApiError && e.code === "ASSET_IN_USE") {
        setDeleteError("Remove the clips that use this file first.");
      } else {
        setDeleteError(e instanceof ApiError ? userMessage(e.code, e.message) : "Delete failed.");
      }
    }
  }

  return (
    <section className={styles.library} aria-label="Media library">
      <h2>Media</h2>
      <div
        className={`${styles.drop} ${dragOver ? styles.dragOver : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          void importFiles(Array.from(e.dataTransfer.files));
        }}
      >
        <button type="button" onClick={() => input.current?.click()}>
          Import
        </button>
        <span> or drop video, image and audio files here</span>
        <input
          ref={input}
          type="file"
          multiple
          hidden
          aria-label="Import files"
          accept="video/*,image/*,audio/*"
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            e.target.value = "";
            void importFiles(files);
          }}
        />
      </div>
      {uploads.map((u) => (
        <div key={u.key} className={styles.upload}>
          <span>{u.name}</span>
          {u.error ? (
            <span role="alert" className={styles.error}>
              {u.error}
            </span>
          ) : (
            <progress value={u.progress} max={1} aria-label={`Uploading ${u.name}`} />
          )}
        </div>
      ))}
      {deleteError ? (
        <p role="alert" className={styles.error}>
          {deleteError}
        </p>
      ) : null}
      <ul className={styles.grid}>
        {assets.map((a) => (
          <li
            key={a.id}
            className={styles.card}
            draggable
            onDragStart={(e) => {
              e.dataTransfer.setData(ASSET_DRAG_TYPE, a.id);
              e.dataTransfer.effectAllowed = "copy";
            }}
          >
            <div className={styles.thumb}>
              {a.kind === "audio" ? (
                <span aria-hidden="true" className={styles.note}>
                  ♪
                </span>
              ) : (
                <img src={assetThumbUrl(projectId, a.id)} alt="" loading="lazy" />
              )}
            </div>
            <div className={styles.name} title={a.original_name}>
              {a.original_name}
            </div>
            <div className={styles.meta}>
              {[formatDuration(a.duration_ms), describeSize(a)].filter(Boolean).join(" · ")}
            </div>
            <div className={styles.actions}>
              {a.kind === "audio" ? (
                <button
                  type="button"
                  onClick={() => onUseAsAudio(a)}
                  disabled={audioAssetId === a.id}
                >
                  {audioAssetId === a.id ? "Show audio" : "Use as show audio"}
                </button>
              ) : null}
              <button
                type="button"
                aria-label={`Delete ${a.original_name}`}
                onClick={() => void remove(a)}
              >
                Delete
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
