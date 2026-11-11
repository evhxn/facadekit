import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as client from "../../api/client";
import type { LearningSummary } from "../../api/types";
import LearningPanel from "./LearningPanel";

vi.mock("../../api/client", async (orig) => ({
  ...(await orig<typeof import("../../api/client")>()),
  getLearning: vi.fn(),
  forgetLearning: vi.fn(),
}));
const m = vi.mocked(client);

const learned: LearningSummary = {
  enabled: true,
  scans: 3,
  strategies: [{ id: "fast", label: "Fast scan", tries: 3, mistakes: 1, mean_reward: 0.8 }],
};

beforeEach(() => {
  m.getLearning.mockReset();
  m.forgetLearning.mockReset();
});
afterEach(cleanup);

describe("LearningPanel", () => {
  it("renders strategies", async () => {
    m.getLearning.mockResolvedValue(learned);
    render(<LearningPanel />);
    expect(await screen.findByText("Fast scan")).toBeTruthy();
    expect(screen.getByText(/learned from 3 scans/)).toBeTruthy();
    expect(screen.getByText(/1 mistake,/)).toBeTruthy();
  });

  it("shows a friendly empty state", async () => {
    m.getLearning.mockResolvedValue({ enabled: true, scans: 0, strategies: [] });
    render(<LearningPanel />);
    expect(await screen.findByText(/Nothing yet/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Forget" })).toBeNull();
  });

  it("explains when learning is off", async () => {
    m.getLearning.mockResolvedValue({ enabled: false, scans: 0, strategies: [] });
    render(<LearningPanel />);
    expect(await screen.findByText(/Learning is turned off/)).toBeTruthy();
  });

  it("forget asks to confirm, calls DELETE and refreshes", async () => {
    m.getLearning
      .mockResolvedValueOnce(learned)
      .mockResolvedValueOnce({ enabled: true, scans: 0, strategies: [] });
    m.forgetLearning.mockResolvedValue(undefined);
    render(<LearningPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "Forget" }));
    expect(m.forgetLearning).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Yes, forget it" }));
    await waitFor(() => expect(m.forgetLearning).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/Nothing yet/)).toBeTruthy();
    expect(m.getLearning).toHaveBeenCalledTimes(2);
  });
});
