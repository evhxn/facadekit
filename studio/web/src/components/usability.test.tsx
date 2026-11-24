import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeProject } from "../api/client.test";
import App from "../App";
import { useProjectStore } from "../state/projectStore";
import Dialog from "./Dialog";
import InfoTip from "./InfoTip";
import { isTyping } from "./keys";
import SaveStatus, { SaveProblem } from "./SaveStatus";
import { toast, TOAST_MS, useToasts } from "./toast";
import Toaster from "./Toaster";
import UndoButtons, { useUndoShortcuts } from "./UndoButtons";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  useToasts.setState({ toasts: [] });
});

describe("SaveStatus", () => {
  beforeEach(() => {
    useProjectStore.setState({ project: makeProject(), dirty: false, saving: false, error: null });
  });

  it("says saved, saving and not saved", () => {
    const { rerender } = render(<SaveStatus />);
    expect(screen.getByTestId("save-status").textContent).toBe("All changes saved");
    act(() => useProjectStore.setState({ dirty: true }));
    rerender(<SaveStatus />);
    expect(screen.getByTestId("save-status").textContent).toBe("Saving…");
    act(() => useProjectStore.setState({ error: "Saving the project failed." }));
    expect(screen.getByTestId("save-status").textContent).toBe("Not saved");
  });

  it("shows nothing without a project", () => {
    useProjectStore.setState({ project: null });
    render(<SaveStatus />);
    expect(screen.queryByTestId("save-status")).toBeNull();
  });

  it("a failed save is an alert with what to do and a retry", () => {
    const saveNow = vi.fn().mockResolvedValue(undefined);
    useProjectStore.setState({ dirty: true, error: "Saving the project failed.", saveNow });
    render(<SaveProblem />);
    expect(screen.getByRole("alert").textContent).toMatch(/What to do:/);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(saveNow).toHaveBeenCalled();
  });
});

describe("toasts", () => {
  it("shows a toast with an action, and hides it after a while", () => {
    vi.useFakeTimers();
    const run = vi.fn();
    render(<Toaster />);
    act(() => {
      toast("Deleted Door.", { action: { label: "Undo", run } });
    });
    expect(screen.getByRole("status").textContent).toMatch("Deleted Door.");
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(run).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Deleted Door.")).toBeNull();
    act(() => {
      toast("Imported 2 files.");
    });
    act(() => {
      vi.advanceTimersByTime(TOAST_MS + 10);
    });
    expect(screen.queryByText("Imported 2 files.")).toBeNull();
  });

  it("errors are alerts and stay until closed", () => {
    vi.useFakeTimers();
    render(<Toaster />);
    act(() => {
      toast("Sending failed.", { kind: "error" });
    });
    act(() => {
      vi.advanceTimersByTime(TOAST_MS * 3);
    });
    expect(screen.getByRole("alert").textContent).toMatch("Sending failed.");
    fireEvent.click(screen.getByRole("button", { name: "Close notification" }));
    expect(screen.queryByText("Sending failed.")).toBeNull();
  });
});

describe("InfoTip", () => {
  it("opens and closes a plain-language note", () => {
    render(
      <>
        <label>
          Blend <select />
        </label>
        <InfoTip term="Blend">mixes the clip with what is below.</InfoTip>
      </>,
    );
    const button = screen.getByRole("button", { name: "What is Blend?" });
    expect(button.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(button);
    expect(button.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("note").textContent).toMatch("mixes the clip");
    // The field keeps its own name.
    expect(screen.getByLabelText("Blend").tagName).toBe("SELECT");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("note")).toBeNull();
  });
});

describe("Dialog", () => {
  it("moves focus in, keeps Tab inside, closes on Esc and gives focus back", () => {
    const onClose = vi.fn();
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    const { unmount } = render(
      <Dialog title="Help" onClose={onClose}>
        <button type="button">Inside</button>
      </Dialog>,
    );
    const dialog = screen.getByRole("dialog", { name: "Help" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    const close = screen.getByRole("button", { name: "Close" });
    expect(document.activeElement).toBe(close);
    const inside = screen.getByRole("button", { name: "Inside" });
    inside.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(close);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
    unmount();
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });
});

describe("Help", () => {
  it("opens with ? and with the Help button, and lists the steps and shortcuts", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.keyDown(window, { key: "?" });
    const dialog = screen.getByRole("dialog", { name: "How FacadeKit works" });
    expect(dialog.textContent).toMatch("Surfaces");
    expect(dialog.textContent).toMatch("Undo the last change");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Help" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("? typed in a field is just a character", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>,
    );
    const input = document.createElement("input");
    document.body.appendChild(input);
    fireEvent.keyDown(input, { key: "?" });
    expect(screen.queryByRole("dialog")).toBeNull();
    input.remove();
  });
});

describe("undo", () => {
  function Page() {
    useUndoShortcuts();
    return <UndoButtons />;
  }

  beforeEach(() => {
    useProjectStore.setState({ project: makeProject(), canUndo: true, canRedo: true });
  });

  it("buttons and Ctrl/Cmd+Z, Shift+Z, Ctrl+Y call the store", () => {
    const undo = vi.fn();
    const redo = vi.fn();
    useProjectStore.setState({ undo, redo });
    render(<Page />);
    fireEvent.click(screen.getByRole("button", { name: /Undo/ }));
    fireEvent.click(screen.getByRole("button", { name: /Redo/ }));
    fireEvent.keyDown(window, { key: "z", ctrlKey: true });
    fireEvent.keyDown(window, { key: "z", metaKey: true, shiftKey: true });
    fireEvent.keyDown(window, { key: "y", ctrlKey: true });
    expect(undo).toHaveBeenCalledTimes(2);
    expect(redo).toHaveBeenCalledTimes(3);
  });

  it("leaves text fields alone", () => {
    const undo = vi.fn();
    useProjectStore.setState({ undo });
    render(
      <>
        <Page />
        <input aria-label="Name" />
      </>,
    );
    fireEvent.keyDown(screen.getByLabelText("Name"), { key: "z", ctrlKey: true });
    expect(undo).not.toHaveBeenCalled();
  });

  it("buttons are disabled when there is nothing to undo or redo", () => {
    useProjectStore.setState({ canUndo: false, canRedo: false });
    render(<UndoButtons />);
    expect((screen.getByRole("button", { name: /Undo/ }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect((screen.getByRole("button", { name: /Redo/ }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });
});

describe("isTyping", () => {
  it("is true for text fields only", () => {
    const text = document.createElement("input");
    const box = document.createElement("input");
    box.type = "checkbox";
    expect(isTyping(text)).toBe(true);
    expect(isTyping(box)).toBe(false);
    expect(isTyping(document.createElement("textarea"))).toBe(true);
    expect(isTyping(document.createElement("button"))).toBe(false);
    expect(isTyping(null)).toBe(false);
  });
});
