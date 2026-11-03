"""High-level demo pipeline: synthetic world -> mesh -> software render -> post."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .mesh import Scene, build_scene
from .post import apply_post
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


@dataclass
class DemoResult:
    image: np.ndarray
    scene: Scene
    world: SyntheticWorld
    camera: Camera


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
