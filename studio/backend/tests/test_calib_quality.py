from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
import pytest

from facadekit.calib.capture import precheck
from facadekit.calib.patterns import PatternSpec
from facadekit.calib.quality import (
    Reason,
    Verdict,
    finest_readable_skip,
    lit_blob,
    probe_stripes,
    projection_found,
    px_per_proj_px,
    px_per_proj_px_from_h,
    verdict,
)
from facadekit.errors import FkError
from facadekit.models.calibration import CalibQuality, CalibSettings
from facadekit.sim.devices import SimCamera, SimDisplay
from facadekit.sim.scene import (
    SimScene,
    default_scene,
    scene_far_marginal,
    scene_far_too_coarse,
    scene_phone,
    true_px_per_proj_px,
)


def _noop(_s: float) -> None:
    return None


def _rig(scene: SimScene) -> tuple[SimDisplay, SimCamera, PatternSpec]:
    display = SimDisplay(scene)
    cam = SimCamera(scene, display)
    cam.open()
    return display, cam, PatternSpec(*scene.proj_size)


@pytest.mark.parametrize(
    ("s", "want"),
    [(0.48, 2), (0.375, 2), (0.37, 3), (0.25, 3), (0.1875, 3), (0.167, 4), (1.0, 1)],
)
def test_finest_readable_skip(s: float, want: int) -> None:
    assert finest_readable_skip(s) == want


def test_finest_readable_skip_is_clamped() -> None:
    assert finest_readable_skip(100.0) == 0
    assert finest_readable_skip(0.001) == 6


@pytest.mark.parametrize(
    ("args", "kwargs", "want"),
    [
        ((0.48, 40.0), {}, ("good", [])),
        ((0.25, 40.0), {}, ("marginal", ["SMALL_IN_FRAME"])),
        ((0.15, 40.0), {}, ("too_coarse", ["SMALL_IN_FRAME"])),
        ((0.48, 5.0), {}, ("marginal", ["NOISY"])),
        ((0.48, 40.0), {"modulation": {2: 0.1, 3: 0.35, 4: 0.6}}, ("marginal", ["BLURRY"])),
        ((0.48, 40.0), {"modulation": {2: 0.1, 3: 0.1, 4: 0.1}}, ("too_coarse", ["BLURRY"])),
        ((0.48, 40.0), {"modulation": {2: 0.5, 3: 0.6, 4: 0.6}}, ("good", [])),
        ((0.15, 5.0), {"p99_white": 255.0}, ("too_coarse", ["SMALL_IN_FRAME", "NOISY", "CLIPPED"])),
        ((0.48, 40.0), {"controls": {"auto_exposure": False}}, ("good", ["NO_EXPOSURE_CONTROL"])),
    ],
)
def test_verdict_table(
    args: tuple[float, float],
    kwargs: dict[str, object],
    want: tuple[Verdict, list[Reason]],
) -> None:
    assert verdict(*args, **kwargs) == want  # type: ignore[arg-type]


def test_lit_blob_finds_the_rectangle_not_the_speckle() -> None:
    rng = np.random.default_rng(1)
    contrast = np.zeros((400, 600), np.float32)
    x0, y0 = 150, 120
    contrast[y0 : y0 + 100, x0 : x0 + 200] = 80.0
    placed = 0
    while placed < 300:  # isolated single pixels, none touching the rectangle or each other
        y, x = int(rng.integers(2, 398)), int(rng.integers(2, 598))
        if contrast[y - 2 : y + 3, x - 2 : x + 3].any() or (
            y0 - 2 <= y <= y0 + 102 and x0 - 2 <= x <= x0 + 202
        ):
            continue
        contrast[y, x] = 80.0
        placed += 1
    blob = lit_blob(contrast)
    assert blob is not None
    assert blob.box == (x0, y0, x0 + 200, y0 + 100)
    assert blob.area_px == 20000
    assert blob.total_lit_px == 20300
    assert blob.hull_area_px == pytest.approx(199 * 99, rel=0.01)
    assert projection_found(blob)


def test_scattered_pixels_are_not_a_projection() -> None:
    rng = np.random.default_rng(2)
    contrast = np.zeros((400, 600), np.float32)
    ys, xs = rng.integers(0, 400, 300), rng.integers(0, 600, 300)
    contrast[ys, xs] = 80.0
    assert not projection_found(lit_blob(contrast))
    assert lit_blob(np.zeros((10, 10), np.float32)) is None


def test_small_blob_and_dominated_blob_are_rejected() -> None:
    small = np.zeros((300, 300), np.float32)
    small[100:150, 100:150] = 80.0  # 2500 px < MIN_LIT_PX
    assert not projection_found(lit_blob(small))
    two = np.zeros((300, 600), np.float32)
    two[10:90, 10:100] = 80.0  # 7200 px
    two[110:290, 110:590] = 80.0  # 86400 px, so the first blob is not the main one...
    blob = lit_blob(two)
    assert blob is not None and blob.area_px == 86400
    split = np.zeros((300, 600), np.float32)
    split[10:90, 10:100] = 80.0
    split[110:190, 300:390] = 80.0  # equal blobs: the largest holds only half of the light
    split[210:290, 400:480] = 80.0
    assert not projection_found(lit_blob(split))


def test_px_per_proj_px_formulas() -> None:
    assert px_per_proj_px(1920 * 1080 * 0.25, 1920, 1080) == pytest.approx(0.5)
    h = np.array([[0.4, 0, 30], [0, 0.4, 20], [0, 0, 1.0]])  # camera -> projector
    assert px_per_proj_px_from_h(h, 1920, 1080) == pytest.approx(2.5)


def test_precheck_far_too_coarse(capsys: pytest.CaptureFixture[str]) -> None:
    sc = scene_far_too_coarse()
    display, cam, spec = _rig(sc)
    pre = precheck(display, cam, spec, sleep=_noop)
    truth = true_px_per_proj_px(sc)
    with capsys.disabled():
        got = pre["px_per_proj_px"]
        print(f"\ntoo_coarse: true s={truth:.4f} precheck s={got:.4f} snr={pre['snr']:.1f}")
        print(f"lit_px={pre['lit_px']:.0f} lit_fraction={pre['lit_fraction']:.4f}")
    assert abs(pre["px_per_proj_px"] - truth) / truth < 0.15
    assert verdict(pre["px_per_proj_px"], pre["snr"])[0] == "too_coarse"
    for key in ("lit_px", "p99_white", "lit_x0", "lit_y0", "lit_x1", "lit_y1"):
        assert isinstance(pre[key], float)


@pytest.mark.parametrize(
    ("make", "want"),
    [
        (default_scene, "good"),
        (scene_far_marginal, "marginal"),
        (scene_phone, "good"),
    ],
)
def test_precheck_verdicts(
    make: Callable[[], SimScene], want: str, capsys: pytest.CaptureFixture[str]
) -> None:
    sc = make()
    display, cam, spec = _rig(sc)
    pre = precheck(display, cam, spec, sleep=_noop)
    truth = true_px_per_proj_px(sc)
    with capsys.disabled():
        print(f"\n{make.__name__}: true s={truth:.4f} precheck s={pre['px_per_proj_px']:.4f}"
              f" snr={pre['snr']:.1f}")  # fmt: skip
    assert abs(pre["px_per_proj_px"] - truth) / truth < 0.15
    assert verdict(pre["px_per_proj_px"], pre["snr"])[0] == want


def test_precheck_keeps_old_keys_and_lighting() -> None:
    display, cam, spec = _rig(default_scene())
    pre = precheck(display, cam, spec, sleep=_noop)
    assert pre["lit_fraction"] > 0.2 and pre["median_contrast"] > 40
    assert pre["lit_x1"] - pre["lit_x0"] > 800


def test_camera_seeing_nothing_still_refused() -> None:
    sc = default_scene()
    sc.gain = 0.0
    display, cam, spec = _rig(sc)
    with pytest.raises(FkError) as ei:
        precheck(display, cam, spec, sleep=_noop)
    assert ei.value.code == "CAMERA_NO_PROJECTION"


def test_probe_stripes_sharp_vs_blurry() -> None:
    settings = CalibSettings(discard_frames=0, average_frames=1)
    sharp = probe_stripes(*_rig(default_scene()), settings, sleep=_noop)
    blurry = probe_stripes(*_rig(SimScene(blur_sigma=5.0)), settings, sleep=_noop)
    assert sorted(sharp) == [2, 3, 4]
    assert sharp[2] > 0.5 and sharp[4] > sharp[2] * 0.9
    assert blurry[2] < sharp[2] and blurry[2] < 0.25


def test_calib_quality_without_new_fields_parses() -> None:
    q = CalibQuality.model_validate(
        {"coverage": 1.0, "planarity": 1.0, "median_contrast": 80.0, "lit_fraction": 0.5}
    )
    assert q.px_per_proj_px is None and q.snr is None and q.verdict is None
    assert not math.isnan(q.coverage)
