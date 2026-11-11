import { create } from "zustand";
import { ApiError, getProject, putProject } from "../api/client";
import { userMessage } from "../api/errors";
import type { Project } from "../api/types";

const SAVE_DELAY_MS = 500;
const MAX_HISTORY = 100;
const COALESCE_MS = 500;

export interface UpdateOptions {
  /** Updates with the same key within 500 ms share one undo step (e.g. one drag). */
  coalesceKey?: string;
}

interface ProjectState {
  project: Project | null;
  dirty: boolean;
  saving: boolean;
  error: string | null;
  load: (id: string) => Promise<void>;
  canUndo: boolean;
  canRedo: boolean;
  update: (mutator: (draft: Project) => void, opts?: UpdateOptions) => void;
  undo: () => void;
  redo: () => void;
  saveNow: () => Promise<void>;
  replace: (project: Project) => void;
}

let timer: ReturnType<typeof setTimeout> | null = null;
let chain: Promise<void> = Promise.resolve();
let editCounter = 0;
let past: Project[] = [];
let future: Project[] = [];
let lastKey: string | null = null;
let lastKeyAt = 0;

function resetHistory() {
  past = [];
  future = [];
  lastKey = null;
}

function clearTimer() {
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
}

export const useProjectStore = create<ProjectState>((set, get) => {
  const saveOnce = async (): Promise<void> => {
    const { project, dirty } = get();
    if (!project || !dirty) return;
    const sentEdit = editCounter;
    set({ saving: true });
    try {
      const saved = await putProject(project);
      if (editCounter === sentEdit) {
        set({ project: saved, dirty: false, saving: false, error: null });
      } else {
        // More edits happened while saving: keep them, only adopt the new revision.
        set((s) => ({
          project: s.project ? { ...s.project, revision: saved.revision } : s.project,
          saving: false,
          error: null,
        }));
      }
    } catch (e) {
      if (e instanceof ApiError && e.code === "REVISION_CONFLICT") {
        const message = userMessage(e.code, e.message);
        try {
          const fresh = await getProject(project.id);
          editCounter++;
          resetHistory();
          set({
            project: fresh,
            dirty: false,
            saving: false,
            error: message,
            canUndo: false,
            canRedo: false,
          });
        } catch (e2) {
          set({
            saving: false,
            error: e2 instanceof ApiError ? userMessage(e2.code, e2.message) : message,
          });
        }
      } else {
        const message =
          e instanceof ApiError ? userMessage(e.code, e.message) : "Saving the project failed. Check that FacadeKit is running, then make your last change again.";
        set({ saving: false, error: message });
      }
    }
  };

  const commit = (next: Project) => {
    editCounter++;
    set({ project: next, dirty: true, canUndo: past.length > 0, canRedo: future.length > 0 });
    clearTimer();
    timer = setTimeout(() => {
      timer = null;
      void get().saveNow();
    }, SAVE_DELAY_MS);
  };

  return {
    project: null,
    dirty: false,
    saving: false,
    error: null,
    canUndo: false,
    canRedo: false,

    async load(id) {
      clearTimer();
      const project = await getProject(id);
      editCounter++;
      resetHistory();
      set({ project, dirty: false, saving: false, error: null, canUndo: false, canRedo: false });
    },

    update(mutator, opts) {
      const { project } = get();
      if (!project) return;
      const draft = structuredClone(project);
      mutator(draft);
      const now = Date.now();
      const key = opts?.coalesceKey ?? null;
      const coalesced = key !== null && key === lastKey && now - lastKeyAt < COALESCE_MS;
      if (!coalesced) {
        past.push(project);
        if (past.length > MAX_HISTORY) past.shift();
      }
      future = [];
      lastKey = key;
      lastKeyAt = now;
      commit(draft);
    },

    undo() {
      const { project } = get();
      const prev = past.pop();
      if (!project || !prev) return;
      future.push(project);
      lastKey = null;
      commit({ ...prev, revision: project.revision });
    },

    redo() {
      const { project } = get();
      const next = future.pop();
      if (!project || !next) return;
      past.push(project);
      lastKey = null;
      commit({ ...next, revision: project.revision });
    },

    saveNow() {
      clearTimer();
      const next = chain.then(saveOnce);
      chain = next.catch(() => {});
      return next;
    },

    replace(project) {
      clearTimer();
      editCounter++;
      resetHistory();
      set({ project, dirty: false, canUndo: false, canRedo: false });
    },
  };
});
