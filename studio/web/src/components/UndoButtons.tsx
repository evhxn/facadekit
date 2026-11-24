import { useEffect } from "react";
import { useProjectStore } from "../state/projectStore";
import { isTyping, modKey } from "./keys";

/**
 * Ctrl/Cmd+Z undoes, Ctrl/Cmd+Shift+Z or Ctrl+Y redoes, anywhere on the page except while
 * typing in a field (the field's own undo wins there).
 */
export function useUndoShortcuts(): void {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
      const k = e.key.toLowerCase();
      if (k !== "z" && k !== "y") return;
      if (isTyping(e.target)) return;
      e.preventDefault();
      const s = useProjectStore.getState();
      if (k === "y" || e.shiftKey) s.redo();
      else s.undo();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}

/** Undo and Redo buttons for an editor page, with their shortcuts in the tooltip. */
export default function UndoButtons() {
  const canUndo = useProjectStore((s) => s.canUndo);
  const canRedo = useProjectStore((s) => s.canRedo);
  const undo = useProjectStore((s) => s.undo);
  const redo = useProjectStore((s) => s.redo);
  return (
    <span role="group" aria-label="Undo and redo" style={{ display: "inline-flex", gap: 8 }}>
      <button
        type="button"
        onClick={undo}
        disabled={!canUndo}
        title={`Undo the last change (${modKey()}+Z)`}
        aria-keyshortcuts="Control+Z Meta+Z"
      >
        ↶ Undo
      </button>
      <button
        type="button"
        onClick={redo}
        disabled={!canRedo}
        title={`Redo (${modKey()}+Shift+Z)`}
        aria-keyshortcuts="Control+Shift+Z Meta+Shift+Z Control+Y"
      >
        ↷ Redo
      </button>
    </span>
  );
}
