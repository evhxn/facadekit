import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { SegmentBox, SegmentEngines, SegmentResult } from "../../api/client";
import type { Job } from "../../api/types";
import type { Quad } from "./geometry";
import { boxFrom, formatMB } from "./MagicSelect";
import SurfaceCanvas from "./SurfaceCanvas";

// Spike S-6: honest engine state, the confirmed model download and box prompts.

const segment = vi.fn();
vi.mock("../../api/client", async (orig) => ({
  ...(await orig<typeof import("../../api/client")>()),
  segment: (...a: unknown[]) => segment(...a),
}));
const waitForJob = vi.fn();
vi.mock("../../api/events", async (orig) => ({
  ...(await orig<typeof import("../../api/events")>()),
  waitForJob: (...a: unknown[]) => waitForJob(...a),
}));

const quad: Quad = [
  [100, 100],
  [300, 100],
  [300, 200],
  [100, 200],
];
const res = (engine = "grabcut"): SegmentResult => ({ outer: quad, quad, score: 0.9, engine });

const ENC = 24799761;
const DEC = 16565728;
const engine = (installed: boolean) => ({
  id: "efficientsam_ti",
  label: "EfficientSAM-Ti",
  adapter: "efficientsam",
  licence: "Apache-2.0",
  source: "https://github.com/yformer/EfficientSAM",
  published: true,
  installed,
  download_bytes: installed ? 0 : ENC + DEC,
  files: [
    {
      name: "efficientsam_ti_encoder",
      role: "encoder",
      url: "https://huggingface.co/yunyangx/EfficientSAM/resolve/abc/efficientsam_ti_encoder.onnx",
      host: "huggingface.co",
      size_bytes: ENC,
      installed,
    },
    {
      name: "efficientsam_ti_decoder",
      role: "decoder",
      url: "https://huggingface.co/yunyangx/EfficientSAM/resolve/abc/efficientsam_ti_decoder.onnx",
      host: "huggingface.co",
      size_bytes: DEC,
      installed,
    },
  ],
});
const status = (installed: boolean): SegmentEngines => ({
  active: installed ? "efficientsam_ti" : "grabcut",
  onnxruntime: true,
  engines: [engine(installed)],
});

let engines: SegmentEngines;
let fetchMock: ReturnType<typeof vi.fn>;
const json = (body: unknown, status = 200) =>
  Promise.resolve({
    ok: status < 400,
    status,
    headers: { get: () => null },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });

beforeAll(() => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  if (typeof window.PointerEvent === "undefined") {
    (window as unknown as { PointerEvent: unknown }).PointerEvent = class extends MouseEvent {};
  }
});
beforeEach(() => {
  segment.mockReset();
  waitForJob.mockReset();
  engines = status(false);
  fetchMock = vi.fn((url: string) => {
    if (url.endsWith("/ai/segment/engines")) return json(engines);
    if (url.endsWith("/ai/capabilities"))
      return json({ segment_engines: ["efficientsam_ti", "grabcut"], beats: false });
    if (url.includes("/ai/models/")) return json({ job_id: "job_1" }, 202);
    return json({}, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const setup = () => {
  const onCreate = vi.fn();
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
      onCreate={onCreate}
      onDelete={vi.fn()}
      magic={{ projectId: "prj", onAccept: vi.fn() }}
    />,
  );
  return { onCreate, canvas: screen.getByLabelText("Surface canvas") };
};
const settle = () => act(() => new Promise<void>((r) => setTimeout(r, 0)));
const downloads = () => fetchMock.mock.calls.filter(([url]) => String(url).includes("/ai/models/"));

describe("Magic select engine state", () => {
  it("says the simple selector is active and offers the model without fetching it", async () => {
    setup();
    await settle();
    expect(screen.getByLabelText("Active selector").textContent).toBe("Simple selector (GrabCut)");
    const offer = screen.getByRole("button", { name: "Download model (41 MB)" });
    expect(downloads()).toHaveLength(0);

    fireEvent.click(offer);
    const dialog = screen.getByRole("dialog", { name: "Download smart select model" });
    expect(dialog.textContent).toContain("EfficientSAM-Ti (41 MB) from huggingface.co");
    expect(dialog.textContent).toContain("efficientsam_ti_encoder.onnx");
    expect(dialog.textContent).toContain("Apache-2.0");
    expect(dialog.textContent).toContain("SHA-256");
    expect(downloads()).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(downloads()).toHaveLength(0);
  });

  it("downloads only after Download is pressed, then shows the smart selector", async () => {
    waitForJob.mockImplementation(async (_id: string, onUpdate?: (j: Job) => void) => {
      onUpdate?.({ id: "job_1", progress: 0.5, state: "running" } as Job);
      engines = status(true);
      return { id: "job_1", progress: 1, state: "succeeded" } as Job;
    });
    setup();
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Download model (41 MB)" }));
    fireEvent.click(screen.getByRole("button", { name: "Download" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Active selector").textContent).toBe(
        "Smart select (EfficientSAM-Ti)",
      ),
    );
    const [[url, init]] = downloads() as [[string, RequestInit]];
    expect(url).toBe("/api/v1/ai/models/efficientsam_ti/download");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ confirm: true, expected_bytes: ENC + DEC });
    expect(waitForJob.mock.calls[0]?.[0]).toBe("job_1");
    expect(screen.queryByRole("button", { name: /Download model/ })).toBeNull();
  });

  it("shows why a download failed and keeps the simple selector", async () => {
    waitForJob.mockResolvedValue({
      id: "job_1",
      progress: 0.2,
      state: "failed",
      error: { code: "MODEL_DOWNLOAD_FAILED", message: "Checksum mismatch for x" },
    } as Job);
    setup();
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Download model (41 MB)" }));
    fireEvent.click(screen.getByRole("button", { name: "Download" }));
    await screen.findByRole("alert");
    expect(screen.getByLabelText("Active selector").textContent).toBe("Simple selector (GrabCut)");
  });

  it("asks for grabcut while no model is installed", async () => {
    segment.mockResolvedValue(res());
    const { canvas } = setup();
    await settle();
    fireEvent.click(screen.getByText("Magic select"));
    fireEvent.pointerDown(canvas, { clientX: 200, clientY: 150, button: 0 });
    await waitFor(() => expect(segment).toHaveBeenCalledTimes(1));
    expect(segment.mock.calls[0]?.[2]).toBe("grabcut");
  });

  it("lets the server use the installed model and offers no download", async () => {
    engines = status(true);
    segment.mockResolvedValue(res("efficientsam_ti"));
    const { canvas } = setup();
    await settle();
    expect(screen.getByLabelText("Active selector").textContent).toBe(
      "Smart select (EfficientSAM-Ti)",
    );
    expect(screen.queryByRole("button", { name: /Download model/ })).toBeNull();
    fireEvent.click(screen.getByText("Magic select"));
    fireEvent.pointerDown(canvas, { clientX: 200, clientY: 150, button: 0 });
    await waitFor(() => expect(segment).toHaveBeenCalledTimes(1));
    expect(segment.mock.calls[0]?.[2]).toBeUndefined();
  });

  it("falls back to grabcut once when the model cannot be used", async () => {
    engines = status(true);
    segment.mockRejectedValueOnce(new ApiError("AI_UNAVAILABLE", "not installed", 500));
    segment.mockResolvedValueOnce(res());
    const { canvas } = setup();
    await settle();
    fireEvent.click(screen.getByText("Magic select"));
    fireEvent.pointerDown(canvas, { clientX: 200, clientY: 150, button: 0 });
    await waitFor(() => expect(segment).toHaveBeenCalledTimes(2));
    expect(segment.mock.calls[1]?.[2]).toBe("grabcut");
    await screen.findByText(/Using the simple selector/);
  });
});

describe("Magic select box prompt", () => {
  it("Add rectangle draws a box prompt while Magic select is on", async () => {
    segment.mockResolvedValue(res());
    const { canvas, onCreate } = setup();
    await settle();
    fireEvent.click(screen.getByText("Magic select"));
    const add = screen.getByRole("button", { name: "Add rectangle" });
    fireEvent.click(add);
    expect(add.getAttribute("aria-pressed")).toBe("true");
    fireEvent.pointerDown(canvas, { clientX: 100, clientY: 80, button: 0 });
    fireEvent.pointerMove(canvas, { clientX: 220, clientY: 160 });
    fireEvent.pointerUp(canvas, { clientX: 260, clientY: 190 });
    await waitFor(() => expect(segment).toHaveBeenCalledTimes(1));
    const [, points, , box] = segment.mock.calls[0] as [
      string,
      unknown[],
      unknown,
      SegmentBox,
    ];
    expect(points).toEqual([]);
    expect(box.x1 - box.x0).toBeGreaterThan(10);
    expect(box.y1 - box.y0).toBeGreaterThan(10);
    expect(onCreate).not.toHaveBeenCalled();
    expect(add.getAttribute("aria-pressed")).toBe("false");

    // a click after the box is sent together with the box
    fireEvent.pointerDown(canvas, { clientX: 180, clientY: 130, button: 0 });
    await waitFor(() => expect(segment).toHaveBeenCalledTimes(2));
    expect(segment.mock.calls[1]?.[1]).toHaveLength(1);
    expect(segment.mock.calls[1]?.[3]).toEqual(box);

    // undo removes the click, then the box
    fireEvent.click(screen.getByText("Undo last point"));
    await waitFor(() => expect(segment).toHaveBeenCalledTimes(3));
    expect(segment.mock.calls[2]?.[1]).toEqual([]);
    fireEvent.click(screen.getByText("Undo last point"));
    expect((screen.getByText("Undo last point") as HTMLButtonElement).disabled).toBe(true);
  });

  it("Add rectangle still adds a plain surface when Magic select is off", () => {
    const { onCreate } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Add rectangle" }));
    expect(onCreate).toHaveBeenCalledTimes(1);
  });

  it("formats sizes and ignores tiny boxes", () => {
    expect(formatMB(ENC + DEC)).toBe("41 MB");
    expect(formatMB(4_903_810)).toBe("4.9 MB");
    expect(boxFrom([10, 10], [11, 40])).toBeNull();
    expect(boxFrom([40, 50], [10, 20])).toEqual({ x0: 10, y0: 20, x1: 40, y1: 50 });
  });
});
