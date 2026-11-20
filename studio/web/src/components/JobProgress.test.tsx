import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Job } from "../api/types";
import JobProgress, { remainingText } from "./JobProgress";

afterEach(cleanup);

const job = (partial: Partial<Job>): Job => ({
  id: "j1",
  kind: "calibration",
  state: "running",
  progress: 0.4,
  message: "Pattern 14 of 38",
  error: null,
  result: null,
  created_at: "",
  ...partial,
});

describe("JobProgress", () => {
  it("estimates the time left from the progress so far", () => {
    expect(remainingText(0.5, 20)).toBe("about 20 s left");
    expect(remainingText(0.25, 60)).toBe("about 3 min left");
    expect(remainingText(0.95, 60)).toBe("a few seconds left");
    expect(remainingText(0.01, 30)).toBeNull(); // too early to say
    expect(remainingText(0.5, 1)).toBeNull();
  });

  it("shows the step, the percentage and Cancel while running", () => {
    const onCancel = vi.fn();
    render(<JobProgress job={job({})} onCancel={onCancel} />);
    expect(screen.getByText("Pattern 14 of 38")).toBeTruthy();
    expect(screen.getByText("40%")).toBeTruthy();
    expect((screen.getByRole("progressbar") as HTMLProgressElement).value).toBe(40);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("says a queued job is waiting, and offers no Cancel once finished", () => {
    const { rerender } = render(
      <JobProgress job={job({ state: "queued", progress: 0 })} onCancel={vi.fn()} />,
    );
    expect(screen.getByText(/Waiting for the job before this one/)).toBeTruthy();
    rerender(<JobProgress job={job({ state: "succeeded", progress: 1 })} onCancel={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
  });
});
