import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { SegmentResult } from "../../api/client";
import type { Mask } from "../../api/types";
import type { Quad } from "./geometry";
import SurfaceCanvas from "./SurfaceCanvas";

const segment = vi.fn();
vi.mock("../../api/client", async (orig) => ({
  ...(await orig<typeof import("../../api/client")>()),
  segment: (...a: unknown[]) => segment(...a),
}));

const quad: Quad = [
  [100, 100],
  [300, 100],
  [300, 200],
  [100, 200],
];
const nine = [
  [100, 100],
  [200, 90],
  [300, 100],
  [310, 150],
  [300, 200],
  [200, 210],
  [100, 200],
  [90, 150],
  [95, 120],
] as [number, number][];
const res = (outer: [number, number][]): SegmentResult => ({
  outer,
  quad,
  score: 0.9,
  engine: "grabcut",
});

beforeAll(() => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  if (typeof window.PointerEvent === "undefined") {
    (window as unknown as { PointerEvent: unknown }).PointerEvent = class extends MouseEvent {};
  }
});
beforeEach(() => segment.mockReset());
afterEach(cleanup);

const setup = (disabledReason?: string) => {
  const onAccept = vi.fn<(q: Quad, m: Mask | null) => void>();
  render(
    <SurfaceCanvas
      imageUrl="x.png"
      imgW={800}
      imgH={450}
      surfaces={[]}
      selectedId={null}
      corner={null}
      onSelect={vi.fn()}
      onChangeQuad={vi.fn()}
      onChangeMask={vi.fn()}
      onChangeShape={vi.fn()}
      onCreate={vi.fn()}
      onDelete={vi.fn()}
      magic={{ projectId: "prj", onAccept, ...(disabledReason ? { disabledReason } : {}) }}
    />,
  );
  return { onAccept, canvas: screen.getByLabelText("Surface canvas") };
};
const down = (canvas: HTMLElement, x: number, y: number, button = 0) =>
  fireEvent.pointerDown(canvas, { clientX: x, clientY: y, button });
const start = () => fireEvent.click(screen.getByText("Magic select"));

describe("Magic select", () => {
  it("sends a label-1 point, then [1, 0] after a right click", async () => {
    segment.mockResolvedValue(res(nine));
    const { canvas } = setup();
    start();
    down(canvas, 200, 150);
    await waitFor(() => expect(segment).toHaveBeenCalledTimes(1));
    expect(segment.mock.calls[0]?.[1]).toEqual([{ x: 200, y: 150, label: 1 }]);
    down(canvas, 250, 160, 2);
    await waitFor(() => expect(segment).toHaveBeenCalledTimes(2));
    expect(segment.mock.calls[1]?.[1].map((p: { label: number }) => p.label)).toEqual([1, 0]);
  });

  it("ignores a slow response that arrives after a newer one", async () => {
    let slow!: (r: SegmentResult) => void;
    segment.mockImplementationOnce(() => new Promise((r) => (slow = r)));
    segment.mockResolvedValueOnce(res(quad));
    const { canvas, onAccept } = setup();
    start();
    down(canvas, 200, 150);
    down(canvas, 210, 150);
    await waitFor(() =>
      expect((screen.getByText("Create surface") as HTMLButtonElement).disabled).toBe(false),
    );
    await act(async () => slow(res(nine)));
    fireEvent.click(screen.getByText("Create surface"));
    expect(onAccept).toHaveBeenCalledWith(quad, null);
  });

  it("creates a surface with a mask for a 9-point polygon", async () => {
    segment.mockResolvedValue(res(nine));
    const { canvas, onAccept } = setup();
    start();
    down(canvas, 200, 150);
    await waitFor(() =>
      expect((screen.getByText("Create surface") as HTMLButtonElement).disabled).toBe(false),
    );
    fireEvent.click(screen.getByText("Create surface"));
    expect(onAccept).toHaveBeenCalledWith(quad, { outer: nine, holes: [] });
    expect(screen.queryByText("Create surface")).toBeNull();
  });

  it("stores no mask when the polygon equals the quad", async () => {
    segment.mockResolvedValue(res(quad));
    const { canvas, onAccept } = setup();
    start();
    down(canvas, 200, 150);
    await waitFor(() =>
      expect((screen.getByText("Create surface") as HTMLButtonElement).disabled).toBe(false),
    );
    fireEvent.click(screen.getByText("Create surface"));
    expect(onAccept).toHaveBeenCalledWith(quad, null);
  });

  it("retries once with grabcut when the model download fails", async () => {
    segment.mockRejectedValueOnce(new ApiError("MODEL_DOWNLOAD_FAILED", "x", 502));
    segment.mockResolvedValueOnce(res(nine));
    const { canvas } = setup();
    start();
    down(canvas, 200, 150);
    await waitFor(() => expect(segment).toHaveBeenCalledTimes(2));
    expect(segment.mock.calls[1]?.[2]).toBe("grabcut");
    await screen.findByText(/Using the simple selector/);
  });

  it("shows a friendly message for BAD_REQUEST", async () => {
    segment.mockRejectedValueOnce(new ApiError("BAD_REQUEST", "Nothing was selected", 422));
    const { canvas } = setup();
    start();
    down(canvas, 200, 150);
    await screen.findByText(/Nothing found there/);
  });

  it("is disabled for a manual calibration", () => {
    setup("No photo in manual mode");
    expect((screen.getByText("Magic select") as HTMLButtonElement).disabled).toBe(true);
  });
});
