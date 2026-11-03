"""Build a 3D diorama mesh from decoded GBA-style BG layers.

The important idea in this module is that *nothing here knows about Pokémon*.
It consumes four decoded BG images plus their heights/materials and emits
ordinary triangles.  The same code can therefore be fed by an mGBA layer dump
or by the synthetic lab world.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .raster import Camera, Texture, Triangle, make_triangle
from .sprites import make_character_sprite, make_shadow_texture
from .synthetic import SIDE_TEXTURES, TILE, WORLD_PX, SyntheticWorld

LIGHT = np.array([-0.35, 0.85, -0.4], dtype=np.float32)
LIGHT /= np.linalg.norm(LIGHT)


@dataclass
class Scene:
    triangles: list[Triangle]
    layers: dict[int, str]


def _shade(normal: tuple[float, float, float]) -> float:
    n = np.array(normal, dtype=np.float32)
    n /= max(float(np.linalg.norm(n)), 1e-6)
    return 0.55 + 0.45 * max(0.0, float(np.dot(n, LIGHT)))


def _quad(
    out: list[Triangle],
    p0: np.ndarray,
    p1: np.ndarray,
    p2: np.ndarray,
    p3: np.ndarray,
    uv0: tuple[float, float],
    uv1: tuple[float, float],
    uv2: tuple[float, float],
    uv3: tuple[float, float],
    texture: Texture | None,
    *,
    shade: float = 1.0,
    alpha_test: bool = False,
    blend: bool = False,
    depth_write: bool = True,
    fog: bool = True,
    name: str = "",
) -> None:
    out.append(
        make_triangle(
            p0,
            p1,
            p2,
            uv0,
            uv1,
            uv2,
            texture=texture,
            shade=shade,
            alpha_test=alpha_test,
            blend=blend,
            depth_write=depth_write,
            fog=fog,
            name=name,
        )
    )
    out.append(
        make_triangle(
            p0,
            p2,
            p3,
            uv0,
            uv2,
            uv3,
            texture=texture,
            shade=shade,
            alpha_test=alpha_test,
            blend=blend,
            depth_write=depth_write,
            fog=fog,
            name=name,
        )
    )


def _greedy_rects(
    present: np.ndarray, heights: np.ndarray, eps: float = 1e-4
):
    """Merge equal-height present cells into non-overlapping rectangles."""

    h, w = present.shape
    visited = np.zeros_like(present, dtype=bool)
    for ty in range(h):
        for tx in range(w):
            if not present[ty, tx] or visited[ty, tx]:
                continue
            z0 = float(heights[ty, tx])
            tw = 1
            while tx + tw < w:
                if not present[ty, tx + tw] or visited[ty, tx + tw]:
                    break
                if abs(float(heights[ty, tx + tw]) - z0) > eps:
                    break
                tw += 1

            th = 1
            while ty + th < h:
                ok = True
                for x in range(tx, tx + tw):
                    if not present[ty + th, x] or visited[ty + th, x]:
                        ok = False
                        break
                    if abs(float(heights[ty + th, x]) - z0) > eps:
                        ok = False
                        break
                if not ok:
                    break
                th += 1

            visited[ty : ty + th, tx : tx + tw] = True
            yield tx, ty, tw, th, z0


def _add_layer(
    out: list[Triangle],
    world: SyntheticWorld,
    layer_index: int,
    image: np.ndarray,
    present: np.ndarray,
    heights: np.ndarray,
    base_heights: np.ndarray,
    side_materials: np.ndarray,
    *,
    alpha_test: bool,
    name: str,
) -> None:
    img_h, img_w = image.shape[:2]
    # Keep pixels square for non-square dumps.  WORLD_PX becomes the size of
    # the longer axis instead of stretching every image to a 512x512 square.
    scale = WORLD_PX / max(img_w, img_h)
    sx = sz = scale
    world_w = img_w * scale
    world_h = img_h * scale
    tex = Texture(image, name=name)
    side_tex = {
        key: Texture(pix, name=f"side-{key}", wrap_u=True, wrap_v=False)
        for key, pix in SIDE_TEXTURES.items()
    }

    # Top faces: one merged rectangle per equal-height island.
    for tx, ty, tw, th, h in _greedy_rects(present, heights):
        x0 = tx * TILE * sx
        x1 = (tx + tw) * TILE * sx
        z0 = ty * TILE * sz
        z1 = (ty + th) * TILE * sz
        _quad(
            out,
            np.array([x0, h, z0], dtype=np.float32),
            np.array([x1, h, z0], dtype=np.float32),
            np.array([x1, h, z1], dtype=np.float32),
            np.array([x0, h, z1], dtype=np.float32),
            (x0 / world_w, z0 / world_h),
            (x1 / world_w, z0 / world_h),
            (x1 / world_w, z1 / world_h),
            (x0 / world_w, z1 / world_h),
            tex,
            alpha_test=alpha_test,
            name=f"{name}-top",
        )

    # Side walls: per-cell so cliffs and props stay crisp at their boundaries.
    h, w = present.shape
    for ty in range(h):
        for tx in range(w):
            if not present[ty, tx]:
                continue
            hc = float(heights[ty, tx])
            mat = side_materials[ty, tx]
            if not isinstance(mat, str) or mat not in side_tex:
                mat = "stone"
            wall_tex = side_tex[mat]

            x0 = tx * TILE * sx
            x1 = (tx + 1) * TILE * sx
            z0 = ty * TILE * sz
            z1 = (ty + 1) * TILE * sz

            for dx, dz, normal in (
                (-1, 0, (-1.0, 0.0, 0.0)),
                (1, 0, (1.0, 0.0, 0.0)),
                (0, -1, (0.0, 0.0, -1.0)),
                (0, 1, (0.0, 0.0, 1.0)),
            ):
                nx, nz = tx + dx, ty + dz
                if 0 <= nx < w and 0 <= nz < h and present[nz, nx]:
                    hn = float(heights[nz, nx])
                elif 0 <= nx < w and 0 <= nz < h:
                    hn = float(base_heights[nz, nx])
                else:
                    hn = -4.0
                if hc <= hn + 1e-4:
                    continue

                shade = _shade(normal)
                if dx < 0:
                    p0 = np.array([x0, hn, z1], dtype=np.float32)
                    p1 = np.array([x0, hn, z0], dtype=np.float32)
                    p2 = np.array([x0, hc, z0], dtype=np.float32)
                    p3 = np.array([x0, hc, z1], dtype=np.float32)
                elif dx > 0:
                    p0 = np.array([x1, hn, z0], dtype=np.float32)
                    p1 = np.array([x1, hn, z1], dtype=np.float32)
                    p2 = np.array([x1, hc, z1], dtype=np.float32)
                    p3 = np.array([x1, hc, z0], dtype=np.float32)
                elif dz < 0:
                    p0 = np.array([x0, hn, z0], dtype=np.float32)
                    p1 = np.array([x1, hn, z0], dtype=np.float32)
                    p2 = np.array([x1, hc, z0], dtype=np.float32)
                    p3 = np.array([x0, hc, z0], dtype=np.float32)
                else:
                    p0 = np.array([x1, hn, z1], dtype=np.float32)
                    p1 = np.array([x0, hn, z1], dtype=np.float32)
                    p2 = np.array([x0, hc, z1], dtype=np.float32)
                    p3 = np.array([x1, hc, z1], dtype=np.float32)

                _quad(
                    out,
                    p0,
                    p1,
                    p2,
                    p3,
                    (0.0, 1.0),
                    (1.0, 1.0),
                    (1.0, 0.0),
                    (0.0, 0.0),
                    wall_tex,
                    shade=shade,
                    name=f"{name}-side-{mat}",
                )


def _stone_top(seed: int, base: tuple[int, int, int], dark: tuple[int, int, int]) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = np.zeros((16, 16, 4), dtype=np.uint8)
    out[..., 0:3] = base
    out[..., 3] = 255
    for _ in range(34):
        x, y = rng.integers(0, 16, size=2)
        out[y, x, 0:3] = dark
    # Stone-block seams.
    out[0, :, 0:3] = dark
    out[8, :, 0:3] = dark
    out[:, 0, 0:3] = dark
    out[:, 8, 0:3] = dark
    return out


def _add_base(out: list[Triangle], world_w: float, world_h: float) -> None:
    stone_side = Texture(SIDE_TEXTURES["stone"], name="base-stone", wrap_u=True, wrap_v=False)
    dark_side = Texture(SIDE_TEXTURES["stone_dark"], name="base-dark", wrap_u=True, wrap_v=False)
    stone_top = Texture(_stone_top(11, (112, 112, 122), (78, 78, 88)), name="base-top", wrap_u=True, wrap_v=True)
    dark_top = Texture(_stone_top(23, (78, 78, 90), (52, 52, 64)), name="base-top-dark", wrap_u=True, wrap_v=True)

    def rim(
        outer_x0: float,
        outer_x1: float,
        outer_z0: float,
        outer_z1: float,
        inner_x0: float,
        inner_x1: float,
        inner_z0: float,
        inner_z1: float,
        y: float,
        tex: Texture,
    ) -> None:
        strips = [
            (outer_x0, outer_x1, outer_z0, inner_z0),
            (outer_x0, outer_x1, inner_z1, outer_z1),
            (outer_x0, inner_x0, inner_z0, inner_z1),
            (inner_x1, outer_x1, inner_z0, inner_z1),
        ]
        for x0, x1, z0, z1 in strips:
            if x1 <= x0 or z1 <= z0:
                continue
            _quad(
                out,
                np.array([x0, y, z0], dtype=np.float32),
                np.array([x1, y, z0], dtype=np.float32),
                np.array([x1, y, z1], dtype=np.float32),
                np.array([x0, y, z1], dtype=np.float32),
                (x0 / 16.0, z0 / 16.0),
                (x1 / 16.0, z0 / 16.0),
                (x1 / 16.0, z1 / 16.0),
                (x0 / 16.0, z1 / 16.0),
                tex,
                name="base-rim",
            )

    def walls(
        x0: float,
        x1: float,
        z0: float,
        z1: float,
        y0: float,
        y1: float,
        tex: Texture,
    ) -> None:
        length = max(x1 - x0, z1 - z0)
        u = length / 8.0
        _quad(
            out,
            np.array([x0, y0, z0], dtype=np.float32),
            np.array([x1, y0, z0], dtype=np.float32),
            np.array([x1, y1, z0], dtype=np.float32),
            np.array([x0, y1, z0], dtype=np.float32),
            (0.0, 1.0),
            (u, 1.0),
            (u, 0.0),
            (0.0, 0.0),
            tex,
            shade=_shade((0.0, 0.0, -1.0)),
            name="base-side",
        )
        _quad(
            out,
            np.array([x1, y0, z1], dtype=np.float32),
            np.array([x0, y0, z1], dtype=np.float32),
            np.array([x0, y1, z1], dtype=np.float32),
            np.array([x1, y1, z1], dtype=np.float32),
            (0.0, 1.0),
            (u, 1.0),
            (u, 0.0),
            (0.0, 0.0),
            tex,
            shade=_shade((0.0, 0.0, 1.0)),
            name="base-side",
        )
        _quad(
            out,
            np.array([x0, y0, z1], dtype=np.float32),
            np.array([x0, y0, z0], dtype=np.float32),
            np.array([x0, y1, z0], dtype=np.float32),
            np.array([x0, y1, z1], dtype=np.float32),
            (0.0, 1.0),
            (u, 1.0),
            (u, 0.0),
            (0.0, 0.0),
            tex,
            shade=_shade((-1.0, 0.0, 0.0)),
            name="base-side",
        )
        _quad(
            out,
            np.array([x1, y0, z0], dtype=np.float32),
            np.array([x1, y0, z1], dtype=np.float32),
            np.array([x1, y1, z1], dtype=np.float32),
            np.array([x1, y1, z0], dtype=np.float32),
            (0.0, 1.0),
            (u, 1.0),
            (u, 0.0),
            (0.0, 0.0),
            tex,
            shade=_shade((1.0, 0.0, 0.0)),
            name="base-side",
        )

    # Tier 1: the tabletop the map sits on.
    walls(-16.0, world_w + 16.0, -16.0, world_h + 16.0, -10.0, -4.0, stone_side)
    rim(
        -16.0,
        world_w + 16.0,
        -16.0,
        world_h + 16.0,
        0.0,
        world_w,
        0.0,
        world_h,
        -4.0,
        stone_top,
    )

    # Tier 2: a darker plinth below it.
    walls(-32.0, world_w + 32.0, -32.0, world_h + 32.0, -16.0, -10.0, dark_side)
    rim(
        -32.0,
        world_w + 32.0,
        -32.0,
        world_h + 32.0,
        -16.0,
        world_w + 16.0,
        -16.0,
        world_h + 16.0,
        -10.0,
        dark_top,
    )


def _add_sprites(out: list[Triangle], world: SyntheticWorld, camera: Camera) -> None:
    shadow_tex = Texture(make_shadow_texture(32), name="shadow")
    sprite_tex = {
        "player": Texture(make_character_sprite("player"), name="sprite-player"),
        "npc": Texture(make_character_sprite("npc"), name="sprite-npc"),
        "critter": Texture(make_character_sprite("critter"), name="sprite-critter"),
    }

    # Shadows first so sprites always composite over their own contact shadow.
    for sprite in world.sprites:
        gx = float(sprite.x)
        gz = float(sprite.z)
        gy = world.ground_height(gx, gz) + 0.08
        half_w = max(4.0, sprite.width * 0.42)
        half_d = max(2.2, sprite.width * 0.23)
        cx = gx + 2.5
        cz = gz + 1.5
        _quad(
            out,
            np.array([cx - half_w, gy, cz - half_d], dtype=np.float32),
            np.array([cx + half_w, gy, cz - half_d], dtype=np.float32),
            np.array([cx + half_w, gy, cz + half_d], dtype=np.float32),
            np.array([cx - half_w, gy, cz + half_d], dtype=np.float32),
            (0.0, 0.0),
            (1.0, 0.0),
            (1.0, 1.0),
            (0.0, 1.0),
            shadow_tex,
            blend=True,
            depth_write=False,
            fog=False,
            name="shadow",
        )

    for sprite in world.sprites:
        sprite_pixels = getattr(sprite, "texture", None)
        if sprite_pixels is not None:
            tex = Texture(sprite_pixels, name="sprite-obj")
        else:
            tex = sprite_tex.get(sprite.kind, sprite_tex["player"])
        gx = float(sprite.x)
        gz = float(sprite.z)
        gy = world.ground_height(gx, gz)
        base = np.array([gx, gy, gz], dtype=np.float32)
        top = base + np.array([0.0, sprite.height, 0.0], dtype=np.float32)
        top = top + camera.forward * (sprite.lean * sprite.height)
        half = camera.right * (sprite.width * 0.5)

        _quad(
            out,
            base - half,
            base + half,
            top + half,
            top - half,
            (0.0, 1.0),
            (1.0, 1.0),
            (1.0, 0.0),
            (0.0, 0.0),
            tex,
            alpha_test=True,
            name=f"sprite-{sprite.kind}",
        )


def scene_extent(world: SyntheticWorld) -> tuple[float, float]:
    """Return the world-space (width, depth) used by :func:`build_scene`."""

    ground_layer = getattr(world, "ground_layer", 3)
    h, w = world.layers[ground_layer].shape[:2]
    scale = WORLD_PX / max(w, h)
    return w * scale, h * scale


def build_scene(world: SyntheticWorld, camera: Camera) -> Scene:
    triangles: list[Triangle] = []
    ground_layer = getattr(world, "ground_layer", 3)

    _add_layer(
        triangles,
        world,
        ground_layer,
        world.layers[ground_layer],
        np.ones_like(world.terrain_height, dtype=bool),
        world.terrain_height,
        world.terrain_height,
        world.ground_side_material,
        alpha_test=False,
        name=f"BG{ground_layer}-ground",
    )
    layer_names = {2: "BG2-mid", 1: "BG1-top"}
    for index in sorted(world.layer_present, reverse=True):
        name = layer_names.get(index, f"BG{index}-layer")
        _add_layer(
            triangles,
            world,
            index,
            world.layers[index],
            world.layer_present[index],
            world.layer_height[index],
            world.terrain_height,
            world.layer_side_material[index],
            alpha_test=True,
            name=name,
        )

    world_w, world_h = scene_extent(world)
    _add_base(triangles, world_w, world_h)
    _add_sprites(triangles, world, camera)
    layers = {ground_layer: f"BG{ground_layer}-ground"}
    layers.update(
        {
            index: layer_names.get(index, f"BG{index}-layer")
            for index in sorted(world.layer_present, reverse=True)
        }
    )
    return Scene(triangles=triangles, layers=layers)
