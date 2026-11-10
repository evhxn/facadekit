import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Asset, Clip } from "../../api/types";
import ClipForm from "./ClipForm";

const asset: Asset = {
  id: "ast_v",
  kind: "image",
  filename: "a.png",
  original_name: "a.png",
  width: 10,
  height: 10,
  duration_ms: null,
  fps: null,
  has_audio: false,
  has_alpha: false,
};

const clip: Clip = {
  id: "clp_1",
  asset_id: "ast_v",
  generator: null,
  surface_id: "canvas",
  start_ms: 0,
  duration_ms: 5000,
  in_offset_ms: 0,
  loop: true,
  fit: "fill",
  opacity: 1,
  fade_in_ms: 0,
  fade_out_ms: 0,
  blend: "normal",
};

function renderForm(c: Clip, onPatch = vi.fn()) {
  render(
    <ClipForm
      clip={c}
      assets={[asset]}
      surfaces={[]}
      tracks={[]}
      layer={0}
      isNew
      message={null}
      onPatch={onPatch}
      onLayer={vi.fn()}
    />,
  );
  return onPatch;
}

describe("ClipForm source switch", () => {
  afterEach(cleanup);

  it("switching to Effect clears the asset and stores a default generator", () => {
    const onPatch = renderForm(clip);
    fireEvent.click(screen.getByLabelText("Effect", { selector: "input" }));
    expect(onPatch).toHaveBeenCalledWith({
      asset_id: null,
      generator: { type: "solid", params: { color: "#ffffff" } },
    });
  });

  it("switching back to Media restores an asset and drops the generator", () => {
    const onPatch = renderForm({
      ...clip,
      asset_id: null,
      generator: { type: "strobe", params: {} },
    });
    fireEvent.click(screen.getByLabelText("Media", { selector: "input" }));
    expect(onPatch).toHaveBeenCalledWith({ asset_id: "ast_v", generator: null });
  });

  it("shows parameter inputs for the chosen effect and patches a change", () => {
    const onPatch = renderForm({
      ...clip,
      asset_id: null,
      generator: { type: "strobe", params: { color: "#ffffff", rate_hz: 8, duty: 0.5 } },
    });
    fireEvent.change(screen.getByLabelText("Rate (Hz)"), { target: { value: "4" } });
    expect(onPatch).toHaveBeenCalledWith({
      generator: { type: "strobe", params: { color: "#ffffff", rate_hz: 4, duty: 0.5 } },
    });
    fireEvent.change(screen.getByLabelText("Effect", { selector: "select" }), {
      target: { value: "wash" },
    });
    expect(onPatch).toHaveBeenLastCalledWith({
      generator: {
        type: "wash",
        params: {
          color_a: "#ff0000",
          color_b: "#0000ff",
          period_s: 4,
          angle_deg: 0,
        },
      },
    });
  });
});
