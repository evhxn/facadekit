import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import styles from "./InfoTip.module.css";

/**
 * A small "?" button next to a word a newcomer may not know. It opens a short plain-language
 * explanation under it; Esc or a click elsewhere closes it. Put it next to a label, not inside
 * one, so the field's name stays the same.
 */
export default function InfoTip({ term, children }: { term: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const id = useId();
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        ref.current?.querySelector("button")?.focus();
      }
    };
    // The note is fixed to the window: close it when the page scrolls under it.
    const onScroll = () => setOpen(false);
    window.addEventListener("pointerdown", onDown);
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("pointerdown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <span ref={ref} className={styles.wrap}>
      <button
        type="button"
        className={styles.button}
        aria-label={`What is ${term}?`}
        aria-expanded={open}
        aria-controls={id}
        onClick={(e) => {
          // Fixed position, kept inside the window: never causes sideways scrolling.
          const r = e.currentTarget.getBoundingClientRect();
          const w = Math.min(288, window.innerWidth - 16);
          setPos({
            left: Math.max(8, Math.min(r.left + r.width / 2 - w / 2, window.innerWidth - w - 8)),
            top: r.bottom + 6,
          });
          setOpen((v) => !v);
        }}
      >
        ?
      </button>
      {open ? (
        <span
          id={id}
          role="note"
          className={styles.pop}
          style={pos ? { left: pos.left, top: pos.top } : undefined}
        >
          <strong>{term}</strong> {children}
        </span>
      ) : null}
    </span>
  );
}
