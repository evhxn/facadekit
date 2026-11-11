import { useState } from "react";
import type { Surface } from "../../api/types";
import styles from "./SurfaceList.module.css";

interface Props {
  surfaces: Surface[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onRename: (id: string, name: string) => void;
  onDelete: (id: string) => void;
}

export default function SurfaceList({ surfaces, selectedId, onSelect, onRename, onDelete }: Props) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const commit = (id: string) => {
    const name = draft.trim();
    if (name) onRename(id, name);
    setEditing(null);
  };

  if (surfaces.length === 0) return <p className={styles.empty}>No surfaces yet.</p>;
  return (
    <ul className={styles.list}>
      {surfaces.map((s) => (
        <li key={s.id} className={s.id === selectedId ? styles.selected : undefined}>
          {editing === s.id ? (
            <input
              autoFocus
              aria-label={`Rename ${s.name}`}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={() => commit(s.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter") commit(s.id);
                if (e.key === "Escape") setEditing(null);
              }}
            />
          ) : (
            <button
              type="button"
              className={styles.name}
              onClick={() => onSelect(s.id)}
              onDoubleClick={() => {
                setDraft(s.name);
                setEditing(s.id);
              }}
              title="Double-click to rename"
            >
              {s.name}
            </button>
          )}
          <button
            type="button"
            aria-label={`Delete ${s.name}`}
            onClick={() => onDelete(s.id)}
            className="danger"
          >
            Delete
          </button>
        </li>
      ))}
    </ul>
  );
}
