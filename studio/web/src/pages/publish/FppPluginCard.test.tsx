import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FppPluginSummary } from "../../api/types";
import FppPluginCard from "./FppPluginCard";

const api = vi.hoisted(() => ({
  fppPlugin: vi.fn(),
  fppPluginPair: vi.fn(),
  fppPluginUnpair: vi.fn(),
  fppPluginPower: vi.fn(),
}));

vi.mock("../../api/client", async (orig) => ({
  ...(await orig<typeof import("../../api/client")>()),
  ...api,
}));

const absent: FppPluginSummary = {
  present: false,
  version: null,
  api: null,
  paired: false,
  token_stored: false,
  configured: false,
  health: null,
  problem: "not installed",
  reason: "absent",
};

const unpaired: FppPluginSummary = {
  ...absent,
  present: true,
  version: "0.1.0",
  api: 1,
  reason: "not_paired",
};

function paired(over: Partial<FppPluginSummary["health"] & object> = {}): FppPluginSummary {
  return {
    ...unpaired,
    paired: true,
    token_stored: true,
    reason: null,
    health: {
      healthy: true,
      health: { healthy: true, reason: null },
      projectors: [
        { id: "p1", name: "Left", ok: true, power: "on", lamps: [{ hours: 120, on: true }] },
        { id: "p2", name: "Right", ok: false, power: "unknown", error: "no answer" },
      ],
      summary: { total: 2, on: 1, off: 0, transition: 0, error: 1 },
      player: { status_name: "playing", current_playlist: "FacadeKit - Show" },
      shows: [{ name: "Show", playlist: "FacadeKit - Show", video: "fk_a.mp4" }],
      ...over,
    },
  };
}

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("FppPluginCard", () => {
  it("asks the plugin again when refreshKey changes", async () => {
    api.fppPlugin.mockResolvedValueOnce(paired());
    const { rerender } = render(<FppPluginCard host="fpp.local" refreshKey={0} />);
    expect(await screen.findByText(/1 of 2 projectors on/)).toBeTruthy();
    expect(api.fppPlugin).toHaveBeenCalledTimes(1);
    const after = paired();
    after.health!.summary.on = 2;
    api.fppPlugin.mockResolvedValueOnce(after);
    rerender(<FppPluginCard host="fpp.local" refreshKey={1} />);
    expect(await screen.findByText(/2 of 2 projectors on/)).toBeTruthy();
    expect(api.fppPlugin).toHaveBeenCalledTimes(2);
  });

  it("says the plugin is optional when it is absent", async () => {
    api.fppPlugin.mockResolvedValue(absent);
    const onPaired = vi.fn();
    render(<FppPluginCard host="fpp.local" onPaired={onPaired} />);
    expect(await screen.findByText(/not installed on this FPP/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Projectors on" })).toBeNull();
    expect(onPaired).toHaveBeenCalledWith(false);
    expect(api.fppPlugin).toHaveBeenCalledWith("fpp.local");
  });

  it("pairs with a token", async () => {
    api.fppPlugin.mockResolvedValue(unpaired);
    api.fppPluginPair.mockResolvedValue(paired());
    const onPaired = vi.fn();
    render(<FppPluginCard host="fpp.local" onPaired={onPaired} />);
    const input = await screen.findByLabelText(/Pairing token/);
    fireEvent.change(input, { target: { value: " fkp_abc12345 " } });
    fireEvent.click(screen.getByRole("button", { name: "Pair" }));
    await screen.findByText("Paired with the plugin.");
    expect(api.fppPluginPair).toHaveBeenCalledWith("fpp.local", "fkp_abc12345");
    expect(onPaired).toHaveBeenLastCalledWith(true);
    expect(screen.getByText("Left: On, 120 h")).toBeTruthy();
  });

  it("shows a rejected token", async () => {
    api.fppPlugin.mockResolvedValue(unpaired);
    api.fppPluginPair.mockResolvedValue({
      ...unpaired,
      problem: "The plugin did not accept FacadeKit's pairing token.",
    });
    render(<FppPluginCard host="fpp.local" />);
    fireEvent.change(await screen.findByLabelText(/Pairing token/), {
      target: { value: "fkp_wrong123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Pair" }));
    expect(await screen.findByText(/did not accept/)).toBeTruthy();
  });

  it("shows status, faults and the show count, and switches projectors", async () => {
    api.fppPlugin.mockResolvedValue(paired());
    api.fppPluginPower.mockResolvedValue({
      ok: false,
      action: "on",
      results: [
        { id: "p1", name: "Left", ok: true, state: "on", message: "on" },
        { id: "p2", name: "Right", ok: false, state: "error", message: "no answer" },
      ],
    });
    render(<FppPluginCard host="fpp.local" />);
    expect(await screen.findByText("Right: No answer, no answer")).toBeTruthy();
    expect(
      screen.getByText(/FPP is playing FacadeKit - Show · 1 show from FacadeKit/),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Projectors on" }));
    await waitFor(() => expect(api.fppPluginPower).toHaveBeenCalledWith("fpp.local", "on", true));
    expect(await screen.findByText(/^Right: no answer/)).toBeTruthy();
  });

  it("reports an unhealthy plugin", async () => {
    api.fppPlugin.mockResolvedValue(
      paired({ healthy: false, health: { healthy: false, reason: "Left reports a lamp error" } }),
    );
    render(<FppPluginCard host="fpp.local" />);
    expect(await screen.findByText("Problem: Left reports a lamp error")).toBeTruthy();
  });
});
