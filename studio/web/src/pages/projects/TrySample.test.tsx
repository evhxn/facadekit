import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as client from "../../api/client";
import * as events from "../../api/events";
import TrySample from "./TrySample";

vi.mock("../../api/client", async (orig) => ({
  ...(await orig<typeof import("../../api/client")>()),
  createSampleProject: vi.fn(),
}));
vi.mock("../../api/events", async (orig) => ({
  ...(await orig<typeof import("../../api/events")>()),
  waitForJob: vi.fn(),
}));

const c = vi.mocked(client);
const e = vi.mocked(events);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderIt() {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<TrySample />} />
        <Route path="/p/:id/arrange" element={<div>arrange route</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

const job = (state: string, error: unknown = null) =>
  ({ id: "job_1", kind: "calibration", state, progress: 1, message: "", error }) as never;

describe("TrySample", () => {
  it("makes the chosen sample and opens it in Arrange", async () => {
    c.createSampleProject.mockResolvedValue({ job_id: "job_1", project_id: "sample-winter" });
    e.waitForJob.mockResolvedValue(job("succeeded"));
    renderIt();
    expect(screen.getByText(/simulated house/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Theme"), { target: { value: "winter" } });
    fireEvent.click(screen.getByRole("button", { name: "Try a sample project" }));
    expect(await screen.findByText("arrange route")).toBeTruthy();
    expect(c.createSampleProject).toHaveBeenCalledWith("winter");
  });

  it("shows the error when the job fails", async () => {
    c.createSampleProject.mockResolvedValue({ job_id: "job_1", project_id: "x" });
    e.waitForJob.mockResolvedValue(job("failed", { code: "INTERNAL", message: "No door found" }));
    renderIt();
    fireEvent.click(screen.getByRole("button", { name: "Try a sample project" }));
    expect(await screen.findByText(/No door found/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try a sample project" })).toBeTruthy();
  });
});
