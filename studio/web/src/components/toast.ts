import { create } from "zustand";

export interface ToastAction {
  label: string;
  run: () => void;
}

export interface Toast {
  id: number;
  message: string;
  kind: "success" | "error" | "info";
  action?: ToastAction;
}

interface ToastState {
  toasts: Toast[];
  show: (t: Omit<Toast, "id">) => number;
  dismiss: (id: number) => void;
}

/** How long a toast stays: long enough to read it and reach its Undo button. */
export const TOAST_MS = 7000;
const MAX_TOASTS = 3;

let nextId = 1;
const timers = new Map<number, ReturnType<typeof setTimeout>>();

export const useToasts = create<ToastState>((set, get) => ({
  toasts: [],
  show(t) {
    const id = nextId++;
    set((s) => ({ toasts: [...s.toasts, { ...t, id }].slice(-MAX_TOASTS) }));
    // Errors stay until closed: they carry something the user has to do.
    if (t.kind !== "error") {
      timers.set(
        id,
        setTimeout(() => get().dismiss(id), TOAST_MS),
      );
    }
    return id;
  },
  dismiss(id) {
    const timer = timers.get(id);
    if (timer) clearTimeout(timer);
    timers.delete(id);
    set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) }));
  },
}));

/** Show a short note at the bottom of the screen. Usable outside React components. */
export function toast(message: string, opts: Partial<Omit<Toast, "id" | "message">> = {}): number {
  return useToasts.getState().show({ message, kind: opts.kind ?? "success", ...opts });
}
