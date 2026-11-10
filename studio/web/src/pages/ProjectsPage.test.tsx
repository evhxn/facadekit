import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as client from "../api/client";
import ProjectsPage from "./ProjectsPage";

vi.mock("../api/client", async (orig) => ({
  ...(await orig<typeof import("../api/client")>()),
  listProjects: vi.fn(),
  createProject: vi.fn(),
  deleteProject: vi.fn(),
}));

const m = vi.mocked(client);

beforeEach(() => {
  m.listProjects.mockResolvedValue([
    { id: "p1", name: "Front yard", modified_at: "2026-10-01T10:00:00Z" },
    { id: "p2", name: "Roof", modified_at: "2026-10-02T10:00:00Z" },
  ]);
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<ProjectsPage />} />
        <Route path="/p/:id/setup" element={<div>setup route</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ProjectsPage", () => {
  it("lists projects", async () => {
    renderPage();
    expect(await screen.findByText("Front yard")).toBeTruthy();
    expect(screen.getByText("Roof")).toBeTruthy();
  });

  it("creates a project and navigates to setup", async () => {
    m.createProject.mockResolvedValue({ id: "new1" } as never);
    renderPage();
    await screen.findByText("Front yard");
    fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "Garage" } });
    fireEvent.change(screen.getByLabelText("Projector resolution"), {
      target: { value: "1280x720" },
    });
    fireEvent.click(screen.getByRole("button", { name: "New project" }));
    expect(await screen.findByText("setup route")).toBeTruthy();
    expect(m.createProject).toHaveBeenCalledWith("Garage", { width: 1280, height: 720, fps: 30 });
  });

  it("deletes after confirmation", async () => {
    m.deleteProject.mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();
    await screen.findByText("Front yard");
    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]!);
    await waitFor(() => expect(m.deleteProject).toHaveBeenCalledWith("p1"));
  });
});
