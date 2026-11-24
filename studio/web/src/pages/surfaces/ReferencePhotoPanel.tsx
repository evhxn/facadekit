import { useRef, useState } from "react";
import {
  ApiError,
  deleteReferencePhoto,
  uploadReferencePhoto,
  type ReferencePhotoInfo,
} from "../../api/client";
import ErrorBanner from "../../components/ErrorBanner";
import styles from "./ReferencePhotoPanel.module.css";

export const REFERENCE_LABEL = "Reference photo (by eye, not measured)";
export const REFERENCE_NOTE =
  "Optional. Add a photo of the house or wall (for example from your phone) and place the surfaces on it. FacadeKit does not measure this photo: it is only a background, so the outlines land where you put them, not where the projector really shines.";
export const MAX_PHOTO_MB = 25;

export interface ReferencePhotoPanelProps {
  projectId: string;
  photo: ReferencePhotoInfo | null;
  /** The photo was added, replaced (info) or removed (null). */
  onChange: (photo: ReferencePhotoInfo | null) => void;
}

export default function ReferencePhotoPanel({
  projectId,
  photo,
  onChange,
}: ReferencePhotoPanelProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const claimed = useRef(false); // a second pick while one is sending is ignored

  const pick = async (file: File | undefined) => {
    if (!file || claimed.current) return;
    if (file.size > MAX_PHOTO_MB * 1024 * 1024) {
      setError(`This photo is larger than ${MAX_PHOTO_MB} MB. Use a smaller photo.`);
      return;
    }
    if (file.type && !file.type.startsWith("image/")) {
      setError("This is not a picture. Choose a JPEG, PNG or WebP photo.");
      return;
    }
    claimed.current = true;
    setBusy(true);
    setError(null);
    try {
      onChange(await uploadReferencePhoto(projectId, file));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The photo could not be added. Try again.");
    } finally {
      claimed.current = false;
      setBusy(false);
    }
  };

  const remove = async () => {
    if (claimed.current) return;
    claimed.current = true;
    setBusy(true);
    setError(null);
    try {
      await deleteReferencePhoto(projectId);
      onChange(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The photo could not be removed. Try again.");
    } finally {
      claimed.current = false;
      setBusy(false);
    }
  };

  return (
    <section className={styles.panel} aria-label={REFERENCE_LABEL}>
      <h2>{REFERENCE_LABEL}</h2>
      <p className={styles.help}>{REFERENCE_NOTE}</p>
      <label className={styles.choose}>
        <span>{photo ? "Replace the photo" : "Add a photo"}</span>
        <input
          type="file"
          accept="image/jpeg,image/png,image/webp"
          disabled={busy}
          aria-label={photo ? "Replace the reference photo" : "Add a reference photo"}
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = "";
            void pick(file);
          }}
        />
      </label>
      {busy ? (
        <p className={styles.help} role="status">
          Working…
        </p>
      ) : null}
      {photo ? (
        <p className={styles.help}>
          Photo in use ({photo.width} × {photo.height}). It is fitted into the projector frame
          without stretching.{" "}
          <button type="button" onClick={() => void remove()} disabled={busy}>
            Remove photo
          </button>
        </p>
      ) : null}
      {error ? <ErrorBanner message={error} /> : null}
    </section>
  );
}
