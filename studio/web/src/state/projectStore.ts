import { create } from "zustand";
import { ApiError, getProject, putProject } from "../api/client";
import { userMessage } from "../api/errors";
import type { Project } from "../api/types";

const SAVE_DELAY_MS = 500;

interface ProjectState {
  project: Project | null;
  dirty: boolean;
  saving: boolean;
  error: string | null;
  load: (id: string) => Promise<void>;
  update: (mutator: (draft: Project) => void) => void;
  saveNow: () => Promise<void>;
  replace: (project: Project) => void;
}

let timer: ReturnType<typeof setTimeout> | null = null;
let chain: Promise<void> = Promise.resolve();
let editCounter = 0;

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
          set({ project: fresh, dirty: false, saving: false, error: message });
        } catch (e2) {
          set({
            saving: false,
            error: e2 instanceof ApiError ? userMessage(e2.code, e2.message) : message,
          });
        }
      } else {
        const message =
          e instanceof ApiError ? userMessage(e.code, e.message) : "Saving the project failed.";
        set({ saving: false, error: message });
      }
    }
  };

  return {
    project: null,
    dirty: false,
    saving: false,
    error: null,

    async load(id) {
      clearTimer();
      const project = await getProject(id);
      editCounter++;
      set({ project, dirty: false, saving: false, error: null });
    },

    update(mutator) {
      const { project } = get();
      if (!project) return;
      const draft = structuredClone(project);
      mutator(draft);
      editCounter++;
      set({ project: draft, dirty: true });
      clearTimer();
      timer = setTimeout(() => {
        timer = null;
        void get().saveNow();
      }, SAVE_DELAY_MS);
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
      set({ project, dirty: false });
    },
  };
});
