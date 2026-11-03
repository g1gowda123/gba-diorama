"""High-level demo pipeline: synthetic world -> mesh -> software render -> post."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .dump import DumpWorld, GbaDump, load_dump
from .mesh import Scene, build_scene, scene_extent
from .post import apply_post
from .profiles import ProfileBook, SceneProfile, SceneSignature
from .raster import Camera, render
from .synthetic import WORLD_PX, SyntheticWorld, build_synthetic_world

DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720
DEFAULT_SEED = 7


def _background(width: int, height: int) -> np.ndarray:
    y = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None, None]
    top = np.array([8.0, 10.0, 20.0], dtype=np.float32)
    bottom = np.array([34.0, 38.0, 54.0], dtype=np.float32)
    rgb = top[None, None, :] * (1.0 - y) + bottom[None, None, :] * y
    rgb = np.broadcast_to(rgb, (height, width, 3)).copy()

    yy = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    xx = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :]
    glow = np.exp(-(((xx - 0.5) / 0.55) ** 2 + ((yy - 0.32) / 0.42) ** 2))
    rgb = rgb + glow[..., None] * np.array([12.0, 10.0, 18.0], dtype=np.float32)
    return np.clip(rgb, 0, 255).astype(np.uint8)


def demo_camera(
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    yaw_deg: float = 0.0,
) -> Camera:
    center = np.array([WORLD_PX * 0.5, 0.0, WORLD_PX * 0.5], dtype=np.float32)
    yaw = np.deg2rad(yaw_deg)
    radius = 600.0
    eye = center + np.array(
        [np.sin(yaw) * radius, 300.0, np.cos(yaw) * radius], dtype=np.float32
    )
    return Camera(
        eye=eye,
        target=center,
        up=np.array([0.0, 1.0, 0.0], dtype=np.float32),
        fov_deg=42.0,
        width=width,
        height=height,
        near=1.0,
        far=4000.0,
    )


def dump_camera(
    world: DumpWorld,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    yaw_deg: float = 0.0,
    profile: SceneProfile | None = None,
) -> Camera:
    """Frame a dumped screen-sized map without stretching its aspect ratio."""

    world_w, world_h = scene_extent(world)
    center = np.array([world_w * 0.5, 0.0, world_h * 0.5], dtype=np.float32)
    max_dim = max(world_w, world_h)
    if profile is not None and profile.camera.yaw_deg is not None:
        yaw_deg = profile.camera.yaw_deg
    yaw = np.deg2rad(yaw_deg)
    distance = max_dim * 1.15
    if profile is not None and profile.camera.distance is not None:
        distance = max(1.0, profile.camera.distance)
    pitch_deg = 25.6
    if profile is not None and profile.camera.pitch_deg is not None:
        pitch_deg = min(89.0, max(1.0, profile.camera.pitch_deg))
    eye_height = distance * math.tan(math.radians(pitch_deg))
    eye = center + np.array(
        [np.sin(yaw) * distance, eye_height, np.cos(yaw) * distance],
        dtype=np.float32,
    )
    return Camera(
        eye=eye,
        target=center,
        up=np.array([0.0, 1.0, 0.0], dtype=np.float32),
        fov_deg=42.0,
        width=width,
        height=height,
        near=1.0,
        far=4000.0,
    )


@dataclass
class DemoResult:
    image: np.ndarray
    scene: Scene
    world: SyntheticWorld
    camera: Camera


@dataclass
class DumpResult:
    image: np.ndarray
    scene: Scene | None
    world: DumpWorld | None
    camera: Camera | None
    dump: GbaDump
    fallback: bool = False
    reason: str | None = None
    profile: SceneProfile | None = None
    signature: SceneSignature | None = None


def _resize_nearest(image: np.ndarray, width: int, height: int) -> np.ndarray:
    src_h, src_w = image.shape[:2]
    ys = np.arange(height, dtype=np.int64) * src_h // height
    xs = np.arange(width, dtype=np.int64) * src_w // width
    return image[ys][:, xs]


def composite_dump(dump: GbaDump, width: int, height: int) -> np.ndarray:
    """Flatten BG3..BG0 + OBJ for scenes that must stay 2D (menus, battles)."""

    sizes = [layer.shape[:2] for layer in dump.layers.values()]
    if dump.obj is not None:
        sizes.append(dump.obj.shape[:2])
    if not sizes:
        raise ValueError("dump has no layers")
    src_h, src_w = sizes[0]
    out = np.zeros((src_h, src_w, 4), dtype=np.float32)
    if dump.backdrop is not None and dump.backdrop.shape[:2] == (src_h, src_w):
        out[..., :3] = dump.backdrop[..., :3].astype(np.float32)
    out[..., 3] = 255.0

    for index in (3, 2, 1, 0):
        layer = dump.layers.get(index)
        if layer is None:
            continue
        alpha = layer[..., 3:4].astype(np.float32) / 255.0
        out[..., :3] = layer[..., :3] * alpha + out[..., :3] * (1.0 - alpha)
    if dump.obj is not None:
        alpha = dump.obj[..., 3:4].astype(np.float32) / 255.0
        out[..., :3] = dump.obj[..., :3] * alpha + out[..., :3] * (1.0 - alpha)

    return _resize_nearest(np.clip(out[..., :3], 0, 255).astype(np.uint8), width, height)


def _ui_fallback_reason(dump: GbaDump, ui_coverage: float = 0.05) -> str | None:
    mode = int(dump.meta.get("mode", 0))
    if mode != 0:
        return f"mode {mode} is not text-mode 0"
    bg0 = dump.layers.get(0)
    if bg0 is not None and bg0.size:
        coverage = float((bg0[..., 3] > 0).mean())
        if coverage >= ui_coverage:
            return f"BG0 UI covers {coverage:.1%}"
    return None


def render_demo(
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    seed: int = DEFAULT_SEED,
    yaw_deg: float = 0.0,
    post: bool = True,
) -> DemoResult:
    world = build_synthetic_world(seed)
    camera = demo_camera(width, height, yaw_deg)
    scene = build_scene(world, camera)
    image = render(
        scene.triangles,
        camera,
        _background(width, height),
        fog_color=(18, 24, 40),
        fog_start=900.0,
        fog_end=1900.0,
    )
    if post:
        image = apply_post(image)
    return DemoResult(image=image, scene=scene, world=world, camera=camera)


def render_dump(
    root: str | Path,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    yaw_deg: float = 0.0,
    post: bool = True,
    all_layers: bool = False,
    force_3d: bool = False,
    profile: SceneProfile | None = None,
    profile_path: str | Path | None = None,
) -> DumpResult:
    """Render a real mGBA dump through the same mesher as the synthetic demo."""

    dump = load_dump(root)
    signature = SceneSignature.from_meta(dump.meta)
    if profile is None and profile_path is not None:
        profile = ProfileBook.from_path(profile_path).match(
            signature, dump.game_code
        )

    reason = None if force_3d else _ui_fallback_reason(dump)
    if reason:
        image = composite_dump(dump, width, height)
        if post:
            image = apply_post(image)
        return DumpResult(
            image=image,
            scene=None,
            world=None,
            camera=None,
            dump=dump,
            fallback=True,
            reason=reason,
            profile=profile,
            signature=signature,
        )

    world = DumpWorld.from_dump(
        dump,
        min_overlay_coverage=0.0 if all_layers else 0.05,
        profile=profile,
    )
    camera = dump_camera(world, width, height, yaw_deg, profile=profile)
    scene = build_scene(world, camera)
    image = render(
        scene.triangles,
        camera,
        _background(width, height),
        fog_color=(18, 24, 40),
        fog_start=900.0,
        fog_end=1900.0,
    )
    if post:
        image = apply_post(image)
    return DumpResult(
        image=image,
        scene=scene,
        world=world,
        camera=camera,
        dump=dump,
        profile=profile,
        signature=signature,
    )
