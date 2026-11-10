import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { Mask, Pt, Surface } from "../../api/types";
import MaskEditor, { addHole, maskFromQuad, removeMaskVertex } from "./MaskEditor";
import SurfaceCanvas from "./SurfaceCanvas";

const quad: Surface["quad"] = [
  [100, 100],
  [300, 100],
  [300, 200],
  [100, 200],
];

const surface = (mask: Mask | null): Surface => ({
  id: "srf_a",
  name: "Door",
  quad,
  mask,
  feather_px: 0,
  aspect: 2,
  mesh: null,
});

afterEach(cleanup);

describe("MaskEditor panel", () => {
  it("switching to Custom outline stores a mask equal to the quad", () => {
    const onChange = vi.fn();
    render(<MaskEditor surface={surface(null)} onChange={onChange} />);
    fireEvent.click(screen.getByLabelText(/Custom outline/));
    expect(onChange).toHaveBeenCalledWith({ outer: quad, holes: [] });
  });

  it("switching back to corners clears the mask", () => {
    const onChange = vi.fn();
    render(<MaskEditor surface={surface(maskFromQuad(quad))} onChange={onChange} />);
    fireEvent.click(screen.getByLabelText(/Use corners/));
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it("warns about a self-crossing outline", () => {
    const bow: Mask = {
      outer: [
        [0, 0],
        [10, 10],
        [10, 0],
        [0, 10],
      ],
      holes: [],
    };
    render(<MaskEditor surface={surface(bow)} onChange={vi.fn()} />);
    expect(screen.getByRole("alert").textContent).toMatch(/crosses itself/);
  });

  it("removes holes", () => {
    const onChange = vi.fn();
    const m = addHole(maskFromQuad(quad), [
      [120, 120],
      [150, 120],
      [150, 150],
    ]);
    render(<MaskEditor surface={surface(m)} onChange={onChange} />);
    fireEvent.click(screen.getByText("Remove hole 1"));
    expect(onChange).toHaveBeenCalledWith({ outer: quad, holes: [] });
  });
});

describe("mask helpers", () => {
  it("blocks deleting below 3 vertices", () => {
    const tri: Mask = {
      outer: [
        [0, 0],
        [10, 0],
        [5, 8],
      ],
      holes: [],
    };
    expect(removeMaskVertex(tri, -1, 0).outer).toEqual(tri.outer);
    expect(removeMaskVertex(maskFromQuad(quad), -1, 0).outer).toHaveLength(3);
  });
});

describe("mask editing on the canvas", () => {
  beforeAll(() => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    if (typeof window.PointerEvent === "undefined") {
      (window as unknown as { PointerEvent: unknown }).PointerEvent = class extends MouseEvent {};
    }
  });

  const setup = (mask: Mask) => {
    const props = {
      onSelect: vi.fn(),
      onChangeQuad: vi.fn(),
      onChangeMask: vi.fn(),
      onChangeShape: vi.fn(),
      onCreate: vi.fn(),
      onDelete: vi.fn(),
    };
    render(
      <SurfaceCanvas
        imageUrl="x.png"
        imgW={800}
        imgH={450}
        surfaces={[surface(mask)]}
        selectedId="srf_a"
        corner={null}
        {...props}
      />,
    );
    return { props, canvas: screen.getByLabelText("Surface canvas") };
  };
  const click = (canvas: HTMLElement, pt: Pt) => {
    fireEvent.pointerDown(canvas, { clientX: pt[0], clientY: pt[1], button: 0 });
    fireEvent.pointerUp(canvas, { clientX: pt[0], clientY: pt[1], button: 0 });
  };

  it("does not delete the third-last vertex", () => {
    const tri: Mask = {
      outer: [
        [100, 100],
        [300, 100],
        [200, 200],
      ],
      holes: [],
    };
    const { props, canvas } = setup(tri);
    click(canvas, [100, 100]);
    fireEvent.keyDown(canvas, { key: "Delete" });
    expect(props.onChangeMask).not.toHaveBeenCalled();
    expect(props.onDelete).not.toHaveBeenCalled();
    expect(screen.getByRole("status").textContent).toMatch(/at least 3/);
  });

  it("deletes a vertex of a 4-point outline", () => {
    const { props, canvas } = setup(maskFromQuad(quad));
    click(canvas, [300, 100]);
    fireEvent.keyDown(canvas, { key: "Backspace" });
    expect(props.onChangeMask).toHaveBeenCalledTimes(1);
    expect(props.onChangeMask.mock.calls[0]![1].outer).toHaveLength(3);
  });

  it("inserts a vertex when an edge is clicked", () => {
    const { props, canvas } = setup(maskFromQuad(quad));
    click(canvas, [200, 101]);
    expect(props.onChangeMask.mock.calls[0]![1].outer).toHaveLength(5);
  });

  it("adds a hole by clicking points and closing with Enter", () => {
    const { props, canvas } = setup(maskFromQuad(quad));
    fireEvent.click(screen.getByText("Add hole"));
    click(canvas, [150, 130]);
    click(canvas, [200, 130]);
    click(canvas, [200, 170]);
    fireEvent.keyDown(canvas, { key: "Enter" });
    const mask = props.onChangeMask.mock.calls[0]![1] as Mask;
    expect(mask.holes).toEqual([
      [
        [150, 130],
        [200, 130],
        [200, 170],
      ],
    ]);
  });

  it("closes a hole by clicking its first point", () => {
    const { props, canvas } = setup(maskFromQuad(quad));
    fireEvent.click(screen.getByText("Add hole"));
    click(canvas, [150, 130]);
    click(canvas, [200, 130]);
    click(canvas, [200, 170]);
    click(canvas, [152, 131]);
    expect((props.onChangeMask.mock.calls[0]![1] as Mask).holes).toHaveLength(1);
  });

  it("nudges the selected vertex with the arrow keys", () => {
    const { props, canvas } = setup(maskFromQuad(quad));
    click(canvas, [100, 100]);
    fireEvent.keyDown(canvas, { key: "ArrowRight", shiftKey: true });
    expect((props.onChangeMask.mock.calls[0]![1] as Mask).outer[0]).toEqual([110, 100]);
  });

  it("moves quad and mask together", () => {
    const { props, canvas } = setup(maskFromQuad(quad));
    fireEvent.pointerDown(canvas, { clientX: 200, clientY: 150, button: 0 });
    fireEvent.pointerMove(canvas, { clientX: 210, clientY: 155 });
    const [, q, m] = props.onChangeShape.mock.calls[0]!;
    expect(q[0]).toEqual([110, 105]);
    expect(m.outer[0]).toEqual([110, 105]);
  });
});
