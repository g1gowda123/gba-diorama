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
from .sprites import make_character_sprite, make_shadow_texture, make_trunk_texture
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
    tile: int = TILE,
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
        x0 = tx * tile * sx
        x1 = (tx + tw) * tile * sx
        z0 = ty * tile * sz
        z1 = (ty + th) * tile * sz
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

            x0 = tx * tile * sx
            x1 = (tx + 1) * tile * sx
            z0 = ty * tile * sz
            z1 = (ty + 1) * tile * sz

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


def _organic_cutout(image: np.ndarray) -> np.ndarray:
    """Trim a rectangular BG tile into a lobed canopy silhouette."""

    height, width = image.shape[:2]
    yy, xx = np.mgrid[0:height, 0:width]
    cx = (width - 1) * 0.5
    cy = (height - 1) * 0.5
    nx = (xx - cx) / max(cx, 1.0)
    ny = (yy - cy) / max(cy, 1.0)
    radius = np.sqrt(nx * nx + ny * ny)
    angle = np.arctan2(ny, nx)
    edge = 1.0 + 0.08 * np.sin(5.0 * angle) + 0.05 * np.sin(9.0 * angle)
    mask = radius <= edge
    out = image.copy()
    out[..., 3] = (out[..., 3].astype(np.float32) * mask).astype(np.uint8)
    return out


def _add_cards(
    out: list[Triangle],
    world: SyntheticWorld,
    camera: Camera,
    texture: np.ndarray,
    boxes: list[tuple[int, int, int, int]],
    *,
    name: str = "canopy-card",
    with_trunk: bool = False,
    organic: bool = False,
    height_scale: float = 1.25,
    width_scale: float = 1.0,
    cluster: int = 1,
) -> None:
    """Stand 2D cutouts up as camera-facing sprite cards.

    This is the Octopath-style 3D2D move: keep the original BG art, but pop it
    out of the ground as a sprite card instead of extruding a terraced volume.
    """

    if not boxes:
        return
    img_h, img_w = texture.shape[:2]
    scale = WORLD_PX / max(img_w, img_h)
    tex = Texture(texture, name=name)
    trunk_tex = (
        Texture(make_trunk_texture(), name=f"{name}-trunk")
        if with_trunk
        else None
    )

    for index, (x0, y0, x1, y1) in enumerate(boxes):
        width = (x1 - x0) * scale
        depth = (y1 - y0) * scale
        if width < 4.0 or depth < 4.0:
            continue
        cx = (x0 + x1) * 0.5 * scale
        cz = (y0 + y1) * 0.5 * scale
        gy = world.ground_height(cx, cz)
        # Keep the card close to the source art's proportions.  The cap avoids
        # turning a merged forest component into a giant wall.
        height = max(8.0, min(120.0, depth * height_scale))
        card_width = width * width_scale
        trunk_h = min(24.0, height * 0.32) if trunk_tex is not None else 0.0
        base = np.array([cx, gy, cz], dtype=np.float32)
        leaf_base = base + np.array([0.0, trunk_h, 0.0], dtype=np.float32)
        top = leaf_base + np.array([0.0, height - trunk_h, 0.0], dtype=np.float32)
        top = top + camera.forward * (height * 0.04)

        if trunk_tex is not None:
            trunk_half = camera.right * max(3.0, card_width * 0.10)
            trunk_top = base + np.array([0.0, trunk_h, 0.0], dtype=np.float32)
            _quad(
                out,
                base - trunk_half,
                base + trunk_half,
                trunk_top + trunk_half,
                trunk_top - trunk_half,
                (0.0, 1.0),
                (1.0, 1.0),
                (1.0, 0.0),
                (0.0, 0.0),
                trunk_tex,
                alpha_test=True,
                name=f"{name}-trunk",
            )

        if organic:
            card_tex = Texture(
                _organic_cutout(texture[y0:y1, x0:x1]),
                name=f"{name}-{index}",
            )
            u0 = v0 = 0.0
            u1 = v1 = 1.0
        else:
            card_tex = tex
            u0 = x0 / img_w
            u1 = x1 / img_w
            v0 = y0 / img_h
            v1 = y1 / img_h
        if cluster > 1:
            # A billboard cloud: one full tree card plus several smaller
            # canopy-only cards lifted around it.  All pixels still come from
            # the original BG art; only placement/scale are procedural.
            canopy_rows = max(1, int((y1 - y0) * 0.72))
            cloud_tex = Texture(
                _organic_cutout(texture[y0 : y0 + canopy_rows, x0:x1]),
                name=f"{name}-cloud-{index}",
            )
            specs = [(0.0, 1.0, 0.00, True)]
            for i in range(1, cluster):
                angle = 2.0 * np.pi * (i - 1) / max(1, cluster - 1)
                specs.append(
                    (
                        0.28 * np.cos(angle),
                        0.62 + 0.08 * (i % 2),
                        0.12 + 0.10 * (i % 3),
                        False,
                    )
                )
        else:
            specs = [(0.0, 1.0, 0.0, True)]

        for offset, card_scale, lift, full in specs:
            card_base = (
                leaf_base
                + camera.right * (offset * card_width)
                + camera.up * (lift * height * 0.25)
            )
            card_top = card_base + camera.up * ((height - trunk_h) * card_scale)
            half = camera.right * (card_width * card_scale * 0.5)
            use_tex = card_tex if full else cloud_tex
            _quad(
                out,
                card_base - half,
                card_base + half,
                card_top + half,
                card_top - half,
                (0.0, 1.0),
                (1.0, 1.0),
                (1.0, 0.0),
                (0.0, 0.0),
                use_tex,
                alpha_test=True,
                name=name,
            )


def _add_prebuilt_cards(
    out: list[Triangle],
    world: SyntheticWorld,
    camera: Camera,
    cards: list[tuple[np.ndarray, tuple[int, int, int, int]]],
    *,
    name: str = "canopy-card",
) -> None:
    """Render pre-warped side-view cards at their source-world positions."""

    if not cards:
        return
    ground = world.layers[getattr(world, "ground_layer", 3)]
    img_h, img_w = ground.shape[:2]
    scale = WORLD_PX / max(img_w, img_h)
    for index, (image, (x0, y0, x1, y1)) in enumerate(cards):
        height, width = image.shape[:2]
        if width < 4 or height < 4:
            continue
        card_width = width * scale
        card_height = height * scale
        cx = (x0 + x1) * 0.5 * scale
        # Stand the card at the front edge of its source footprint.  The
        # cleared ground lies behind it, so it does not read as an empty patch
        # in front of the tree.
        cz = (y1 - 1) * scale
        base = np.array([cx, world.ground_height(cx, cz), cz], dtype=np.float32)
        texture = Texture(image, name=f"{name}-{index}")
        # Three depth slices: a darker, slightly wider card behind the main
        # card gives the silhouette thickness and separates it from the tree
        # behind.  All slices still use the same source-art texture.
        slices = (
            (0.20, 1.06, 0.62),
            (0.10, 1.03, 0.80),
            (0.0, 1.0, 1.0),
        )
        for depth, size_scale, shade in slices:
            slice_base = base + camera.forward * (card_height * depth)
            slice_w = card_width * size_scale
            slice_h = card_height * (0.98 if depth else 1.0)
            top = slice_base + camera.up * slice_h
            half = camera.right * (slice_w * 0.5)
            _quad(
                out,
                slice_base - half,
                slice_base + half,
                top + half,
                top - half,
                (0.0, 1.0),
                (1.0, 1.0),
                (1.0, 0.0),
                (0.0, 0.0),
                texture,
                shade=shade,
                alpha_test=True,
                name=f"{name}-{index}-{depth:.2f}",
            )


def _add_canopy_cards(
    out: list[Triangle], world: SyntheticWorld, camera: Camera
) -> None:
    cards = getattr(world, "canopy_cards", None)
    if cards:
        _add_prebuilt_cards(out, world, camera, cards)
        return
    shadows = getattr(world, "canopy_shadows", None)
    texture = getattr(world, "canopy_texture", None)
    if shadows and texture is not None:
        _add_cards(
            out,
            world,
            camera,
            texture,
            shadows,
            name="canopy-card",
            with_trunk=False,
            organic=True,
            height_scale=2.0,
            width_scale=1.5,
            cluster=1,
        )


def _add_structures(
    out: list[Triangle],
    world: SyntheticWorld,
    camera: Camera,
) -> None:
    """Build buildings as walls plus a roof plane from their own art."""

    structures = getattr(world, "structures", None)
    if not structures:
        return
    ground = world.layers[getattr(world, "ground_layer", 3)]
    img_h, img_w = ground.shape[:2]
    scale = WORLD_PX / max(img_w, img_h)
    shadow_tex = Texture(make_shadow_texture(32), name="structure-shadow")
    for index, struct in enumerate(structures):
        x0, y0, x1, y1 = struct.bbox
        left = x0 * scale
        right = x1 * scale
        back = y0 * scale
        front = (y1 - 1) * scale
        cx = (left + right) * 0.5
        cz = (back + front) * 0.5
        base = world.ground_height(cx, cz)
        wall_h = max(10.0, struct.wall_texture.shape[0] * scale * 0.9)
        roof_y = base + wall_h
        wall_tex = Texture(struct.wall_texture, name=f"structure-{index}-wall")
        roof_tex = Texture(struct.roof_texture, name=f"structure-{index}-roof")

        # Front facade: the game already draws the door and windows here.
        _quad(
            out,
            np.array([left, base, front], dtype=np.float32),
            np.array([right, base, front], dtype=np.float32),
            np.array([right, roof_y, front], dtype=np.float32),
            np.array([left, roof_y, front], dtype=np.float32),
            (0.0, 1.0),
            (1.0, 1.0),
            (1.0, 0.0),
            (0.0, 0.0),
            wall_tex,
            alpha_test=True,
            name=f"structure-{index}-front",
        )
        # Side and back walls reuse edge strips of the facade art.
        strip = max(2, struct.wall_texture.shape[1] // 10)
        left_tex = Texture(
            struct.wall_texture[:, :strip],
            name=f"structure-{index}-left",
            wrap_u=True,
        )
        right_tex = Texture(
            struct.wall_texture[:, -strip:],
            name=f"structure-{index}-right",
            wrap_u=True,
        )
        _quad(
            out,
            np.array([left, base, front], dtype=np.float32),
            np.array([left, base, back], dtype=np.float32),
            np.array([left, roof_y, back], dtype=np.float32),
            np.array([left, roof_y, front], dtype=np.float32),
            (0.0, 1.0),
            (1.0, 1.0),
            (1.0, 0.0),
            (0.0, 0.0),
            left_tex,
            shade=0.80,
            alpha_test=True,
            name=f"structure-{index}-left",
        )
        _quad(
            out,
            np.array([right, base, back], dtype=np.float32),
            np.array([right, base, front], dtype=np.float32),
            np.array([right, roof_y, front], dtype=np.float32),
            np.array([right, roof_y, back], dtype=np.float32),
            (0.0, 1.0),
            (1.0, 1.0),
            (1.0, 0.0),
            (0.0, 0.0),
            right_tex,
            shade=0.72,
            alpha_test=True,
            name=f"structure-{index}-right",
        )
        _quad(
            out,
            np.array([right, base, back], dtype=np.float32),
            np.array([left, base, back], dtype=np.float32),
            np.array([left, roof_y, back], dtype=np.float32),
            np.array([right, roof_y, back], dtype=np.float32),
            (0.0, 1.0),
            (1.0, 1.0),
            (1.0, 0.0),
            (0.0, 0.0),
            wall_tex,
            shade=0.55,
            alpha_test=True,
            name=f"structure-{index}-back",
        )

        # Gable roof: the top-down roof art is split at its ridge and laid on
        # two slopes, so the building reads as a roof instead of a flat slab.
        overhang = max(2.0, min(right - left, front - back) * 0.06)
        rise = max(4.0, struct.roof_texture.shape[0] * scale * 0.35)
        ridge_y = roof_y + rise
        ridge_z = (back + front) * 0.5
        front_eave = front + overhang
        back_eave = back - overhang
        _quad(
            out,
            np.array([left - overhang, roof_y, front_eave], dtype=np.float32),
            np.array([right + overhang, roof_y, front_eave], dtype=np.float32),
            np.array([right + overhang, ridge_y, ridge_z], dtype=np.float32),
            np.array([left - overhang, ridge_y, ridge_z], dtype=np.float32),
            (0.0, 1.0),
            (1.0, 1.0),
            (1.0, 0.5),
            (0.0, 0.5),
            roof_tex,
            alpha_test=True,
            name=f"structure-{index}-roof-front",
        )
        _quad(
            out,
            np.array([left - overhang, ridge_y, ridge_z], dtype=np.float32),
            np.array([right + overhang, ridge_y, ridge_z], dtype=np.float32),
            np.array([right + overhang, roof_y, back_eave], dtype=np.float32),
            np.array([left - overhang, roof_y, back_eave], dtype=np.float32),
            (0.0, 0.5),
            (1.0, 0.5),
            (1.0, 0.0),
            (0.0, 0.0),
            roof_tex,
            shade=0.82,
            alpha_test=True,
            name=f"structure-{index}-roof-back",
        )
        # Close the triangular gable ends above the side walls.
        for side, sx, side_tex in (
            ("left", left, left_tex),
            ("right", right, right_tex),
        ):
            out.append(
                make_triangle(
                    np.array([sx, roof_y, front], dtype=np.float32),
                    np.array([sx, roof_y, back], dtype=np.float32),
                    np.array([sx, ridge_y, ridge_z], dtype=np.float32),
                    (0.0, 1.0),
                    (1.0, 1.0),
                    (0.5, 0.0),
                    texture=side_tex,
                    shade=0.70,
                    alpha_test=True,
                    name=f"structure-{index}-gable-{side}",
                )
            )

        # Contact shadow on the ground under the building.
        half_w = (right - left) * 0.58
        half_d = (front - back) * 0.58
        _quad(
            out,
            np.array([cx - half_w, base + 0.05, cz - half_d], dtype=np.float32),
            np.array([cx + half_w, base + 0.05, cz - half_d], dtype=np.float32),
            np.array([cx + half_w, base + 0.05, cz + half_d], dtype=np.float32),
            np.array([cx - half_w, base + 0.05, cz + half_d], dtype=np.float32),
            (0.0, 0.0),
            (1.0, 0.0),
            (1.0, 1.0),
            (0.0, 1.0),
            shadow_tex,
            blend=True,
            depth_write=False,
            fog=False,
            name=f"structure-{index}-shadow",
        )


def _add_canopy_shadows(out: list[Triangle], world: SyntheticWorld) -> None:
    shadows = getattr(world, "canopy_shadows", None)
    texture = getattr(world, "canopy_texture", None)
    if not shadows or texture is None:
        return
    img_h, img_w = texture.shape[:2]
    scale = WORLD_PX / max(img_w, img_h)
    shadow_tex = Texture(make_shadow_texture(32), name="canopy-shadow")
    for x0, y0, x1, y1 in shadows:
        cx = (x0 + x1) * 0.5 * scale
        cz = (y0 + y1) * 0.5 * scale
        half_w = max(5.0, (x1 - x0) * scale * 0.55)
        half_d = max(4.0, (y1 - y0) * scale * 0.45)
        y = world.ground_height(cx, cz) + 0.06
        _quad(
            out,
            np.array([cx - half_w, y, cz - half_d], dtype=np.float32),
            np.array([cx + half_w, y, cz - half_d], dtype=np.float32),
            np.array([cx + half_w, y, cz + half_d], dtype=np.float32),
            np.array([cx - half_w, y, cz + half_d], dtype=np.float32),
            (0.0, 0.0),
            (1.0, 0.0),
            (1.0, 1.0),
            (0.0, 1.0),
            shadow_tex,
            blend=True,
            depth_write=False,
            fog=False,
            name="canopy-shadow",
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
    _add_canopy_shadows(triangles, world)
    _add_canopy_cards(triangles, world, camera)
    _add_structures(triangles, world, camera)
    for index, (texture, boxes) in sorted(
        getattr(world, "billboard_layers", {}).items()
    ):
        _add_cards(
            triangles,
            world,
            camera,
            texture,
            boxes,
            name=f"BG{index}-billboard",
            organic=False,
            height_scale=1.4,
            width_scale=1.0,
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
