/** True when a key press goes to a text field (page shortcuts must leave it alone). */
export function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || typeof el.tagName !== "string") return false;
  if (el.isContentEditable) return true;
  if (el.tagName === "TEXTAREA" || el.tagName === "SELECT") return true;
  if (el.tagName !== "INPUT") return false;
  const type = (el as HTMLInputElement).type;
  return !["checkbox", "radio", "range", "button", "submit", "reset", "file"].includes(type);
}

/** "Cmd" on a Mac, "Ctrl" elsewhere: how shortcuts are written for this user. */
export function modKey(): string {
  const p = typeof navigator === "undefined" ? "" : navigator.platform || navigator.userAgent;
  return /Mac|iPhone|iPad/i.test(p) ? "Cmd" : "Ctrl";
}
