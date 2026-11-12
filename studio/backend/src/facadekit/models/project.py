from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from facadekit.errors import INVALID_PROJECT, FkError

Point = tuple[float, float]  # (x, y) projector pixels
CANVAS = "canvas"


class ProjectorSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: int = Field(default=1920, ge=160, le=4096)
    height: int = Field(default=1080, ge=90, le=2160)
    fps: Literal[24, 25, 30, 50, 60] = 30


class Mask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outer: list[Point] = Field(min_length=3)
    holes: list[list[Point]] = Field(default_factory=list)


class Mesh(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cols: int = Field(ge=1, le=16)
    rows: int = Field(ge=1, le=16)
    offsets: list[Point]  # (cols+1)*(rows+1) entries, row-major


class Surface(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    quad: tuple[Point, Point, Point, Point]  # TL, TR, BR, BL
    mask: Mask | None = None
    feather_px: float = Field(default=0.0, ge=0, le=200)
    aspect: float = Field(gt=0)
    mesh: Mesh | None = None


class Asset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["video", "image", "audio"]
    filename: str
    original_name: str
    width: int | None
    height: int | None
    duration_ms: int | None
    fps: float | None
    has_audio: bool = False
    has_alpha: bool = False


class Generator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["solid", "wash", "outline", "strobe"]
    params: dict[str, float | str] = Field(default_factory=dict)


class Clip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    asset_id: str | None
    generator: Generator | None = None
    surface_id: str  # a surface id or "canvas"
    start_ms: int = Field(ge=0)
    duration_ms: int = Field(gt=0)
    in_offset_ms: int = Field(default=0, ge=0)
    loop: bool = True
    fit: Literal["fill", "fit", "stretch"] = "fill"
    opacity: float = Field(default=1.0, ge=0, le=1)
    fade_in_ms: int = Field(default=0, ge=0)
    fade_out_ms: int = Field(default=0, ge=0)
    blend: Literal["normal", "add", "screen"] = "normal"


class Track(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    clips: list[Clip] = Field(default_factory=list)


class Timeline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_ms: int = Field(default=60000, gt=0)
    audio_asset_id: str | None = None
    tracks: list[Track] = Field(default_factory=list)


class Marker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    t_ms: int
    kind: Literal["beat", "downbeat", "onset", "user"]


class FppTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str | None = None
    playlist_name: str | None = None


def _require_unique(kind: str, ids: list[str]) -> None:
    seen: set[str] = set()
    for item_id in ids:
        if item_id in seen:
            raise ValueError(f"duplicate {kind} id {item_id!r}")
        seen.add(item_id)


class Project(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: str
    name: str
    revision: int = 0
    projector: ProjectorSpec = Field(default_factory=ProjectorSpec)
    calibration_id: str | None = None
    surfaces: list[Surface] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    timeline: Timeline = Field(default_factory=Timeline)
    markers: list[Marker] = Field(default_factory=list)
    fpp: FppTarget = Field(default_factory=FppTarget)

    @model_validator(mode="after")
    def _check_cross_fields(self) -> Project:
        # Rule 1: ids unique.
        clips = [clip for track in self.timeline.tracks for clip in track.clips]
        _require_unique("surface", [s.id for s in self.surfaces])
        _require_unique("asset", [a.id for a in self.assets])
        _require_unique("track", [t.id for t in self.timeline.tracks])
        _require_unique("clip", [c.id for c in clips])

        # Rule 6: surface ids, mesh size.
        for surface in self.surfaces:
            if surface.id == CANVAS:
                raise ValueError(f"surface id {CANVAS!r} is reserved")
            if surface.mesh is not None:
                mesh = surface.mesh
                expected = (mesh.cols + 1) * (mesh.rows + 1)
                if len(mesh.offsets) != expected:
                    raise ValueError(
                        f"surface {surface.id!r}: mesh.offsets must have "
                        f"{expected} entries, got {len(mesh.offsets)}"
                    )

        assets = {a.id: a for a in self.assets}
        surface_ids = {s.id for s in self.surfaces}
        for clip in clips:
            # Rule 2: surface reference.
            if clip.surface_id != CANVAS and clip.surface_id not in surface_ids:
                raise ValueError(f"clip {clip.id!r}: unknown surface_id {clip.surface_id!r}")
            # Rule 4: exactly one of asset_id / generator.
            if (clip.asset_id is None) == (clip.generator is None):
                raise ValueError(
                    f"clip {clip.id!r}: exactly one of asset_id and generator must be set"
                )
            # Rule 3: asset reference must exist and not be audio.
            if clip.asset_id is not None:
                asset = assets.get(clip.asset_id)
                if asset is None:
                    raise ValueError(f"clip {clip.id!r}: unknown asset_id {clip.asset_id!r}")
                if asset.kind == "audio":
                    raise ValueError(f"clip {clip.id!r}: asset {clip.asset_id!r} is audio")

        # Rule 3: timeline audio.
        audio_id = self.timeline.audio_asset_id
        if audio_id is not None:
            audio = assets.get(audio_id)
            if audio is None or not (
                audio.kind == "audio" or (audio.kind == "video" and audio.has_audio)
            ):
                raise ValueError(f"timeline.audio_asset_id {audio_id!r} is not an audio source")

        # Rule 5: clips on a track do not overlap.
        for track in self.timeline.tracks:
            ordered = sorted(track.clips, key=lambda c: c.start_ms)
            for prev, nxt in zip(ordered, ordered[1:], strict=False):
                if nxt.start_ms < prev.start_ms + prev.duration_ms:
                    raise ValueError(
                        f"clips {prev.id!r} and {nxt.id!r} overlap on track {track.id!r}"
                    )
        return self


def _prune(value: Any, schema: dict[str, Any], defs: dict[str, Any]) -> Any:
    """Drop object keys the schema does not know (a file saved by a newer version)."""
    if "$ref" in schema:
        return _prune(value, defs[schema["$ref"].rsplit("/", 1)[-1]], defs)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            for option in schema[key]:
                pruned = _prune(value, option, defs)
                if pruned is not value:
                    return pruned
            return value
    if isinstance(value, dict) and "properties" in schema:
        props = schema["properties"]
        return {k: _prune(v, props[k], defs) for k, v in value.items() if k in props}
    if isinstance(value, list) and "items" in schema:
        return [_prune(v, schema["items"], defs) for v in value]
    return value


def parse_project(data: Any, *, ignore_unknown: bool = False) -> Project:
    """Validate a project. ``ignore_unknown`` (used when loading from disk) drops fields this
    version does not know instead of rejecting them, so a newer version's file still opens."""
    try:
        if ignore_unknown:
            schema = Project.model_json_schema(mode="validation")
            data = _prune(data, schema, schema.get("$defs", {}))
        return Project.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "project"
        raise FkError(INVALID_PROJECT, f"{location}: {first['msg']}") from exc
