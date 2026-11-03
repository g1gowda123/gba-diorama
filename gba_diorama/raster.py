"""A small, dependency-free software 3D rasterizer.

It is deliberately simple: textured triangles, a z-buffer, alpha testing,
alpha blending, fog and nearest-neighbour texture sampling.  That is enough to
prototype the diorama look on a CPU, and the mesh/material model maps cleanly
onto a GLSL pass later.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Texture:
    pixels: np.ndarray  # (H, W, 4) uint8
    name: str = ""
    wrap_u: bool = False
    wrap_v: bool = False

    def __post_init__(self) -> None:
        if self.pixels.ndim != 3 or self.pixels.shape[2] != 4:
            raise ValueError("texture must be HxWx4 RGBA")
        self.pixels = np.ascontiguousarray(self.pixels, dtype=np.uint8)
        self._h, self._w = self.pixels.shape[:2]

    @property
    def width(self) -> int:
        return self._w

    @property
    def height(self) -> int:
        return self._h


@dataclass
class Vertex:
    pos: np.ndarray  # camera-space x, y, z (z forward)
    uv: np.ndarray  # normalized texture coordinates

    def lerp(self, other: "Vertex", t: float) -> "Vertex":
        return Vertex(self.pos + (other.pos - self.pos) * t, self.uv + (other.uv - self.uv) * t)


@dataclass
class Triangle:
    v0: Vertex
    v1: Vertex
    v2: Vertex
    texture: Texture | None = None
    color: tuple[int, int, int] = (255, 255, 255)
    shade: float = 1.0
    alpha_test: bool = False
    blend: bool = False
    depth_write: bool = True
    fog: bool = True
    name: str = ""


@dataclass
class Camera:
    eye: np.ndarray
    target: np.ndarray
    up: np.ndarray
    fov_deg: float
    width: int
    height: int
    near: float = 1.0
    far: float = 6000.0

    def __post_init__(self) -> None:
        self.eye = np.asarray(self.eye, dtype=np.float64)
        self.target = np.asarray(self.target, dtype=np.float64)
        self.up = np.asarray(self.up, dtype=np.float64)
        forward = self.target - self.eye
        self.forward = forward / np.linalg.norm(forward)
        right = np.cross(self.forward, self.up)
        self.right = right / np.linalg.norm(right)
        self.up = np.cross(self.right, self.forward)
        self.up /= np.linalg.norm(self.up)
        self.aspect = self.width / self.height
        self.focal = 1.0 / np.tan(np.radians(self.fov_deg) * 0.5)

    def world_to_camera(self, p: np.ndarray) -> np.ndarray:
        d = np.asarray(p, dtype=np.float64) - self.eye
        return np.array(
            [np.dot(d, self.right), np.dot(d, self.up), np.dot(d, self.forward)],
            dtype=np.float64,
        )

    def camera_to_screen(self, p: np.ndarray) -> tuple[float, float, float]:
        z = max(float(p[2]), 1e-6)
        x = (p[0] * self.focal / (z * self.aspect)) * 0.5 + 0.5
        y = 0.5 - (p[1] * self.focal / z) * 0.5
        return x * self.width, y * self.height, z


def make_triangle(
    p0: np.ndarray,
    p1: np.ndarray,
    p2: np.ndarray,
    uv0: tuple[float, float] = (0, 0),
    uv1: tuple[float, float] = (0, 0),
    uv2: tuple[float, float] = (0, 0),
    **kwargs,
) -> Triangle:
    return Triangle(
        Vertex(np.asarray(p0, dtype=np.float64), np.asarray(uv0, dtype=np.float64)),
        Vertex(np.asarray(p1, dtype=np.float64), np.asarray(uv1, dtype=np.float64)),
        Vertex(np.asarray(p2, dtype=np.float64), np.asarray(uv2, dtype=np.float64)),
        **kwargs,
    )


def _to_camera_space(tri: Triangle, camera: Camera) -> Triangle:
    """Return a copy whose vertices are in camera space.

    ``render`` is called repeatedly with the same scene in the interactive
    viewer, so mutating the scene's world-space vertices in place would make
    every frame depend on the previous camera.
    """

    return Triangle(
        Vertex(camera.world_to_camera(tri.v0.pos), tri.v0.uv),
        Vertex(camera.world_to_camera(tri.v1.pos), tri.v1.uv),
        Vertex(camera.world_to_camera(tri.v2.pos), tri.v2.uv),
        texture=tri.texture,
        color=tri.color,
        shade=tri.shade,
        alpha_test=tri.alpha_test,
        blend=tri.blend,
        depth_write=tri.depth_write,
        fog=tri.fog,
        name=tri.name,
    )


def _clip_near(tri: Triangle, near: float) -> list[Triangle]:
    """Sutherland-Hodgman clip of one triangle against the near plane."""

    verts = [tri.v0, tri.v1, tri.v2]
    out: list[Vertex] = []
    for i in range(3):
        a = verts[i]
        b = verts[(i + 1) % 3]
        a_in = a.pos[2] >= near
        b_in = b.pos[2] >= near
        if a_in:
            out.append(a)
        if a_in != b_in:
            t = (near - a.pos[2]) / (b.pos[2] - a.pos[2])
            out.append(a.lerp(b, t))
    if len(out) < 3:
        return []
    result: list[Triangle] = []
    for i in range(1, len(out) - 1):
        result.append(
            Triangle(
                out[0],
                out[i],
                out[i + 1],
                texture=tri.texture,
                color=tri.color,
                shade=tri.shade,
                alpha_test=tri.alpha_test,
                blend=tri.blend,
                depth_write=tri.depth_write,
                fog=tri.fog,
                name=tri.name,
            )
        )
    return result


def _render_triangle(
    tri: Triangle,
    frame: np.ndarray,
    zbuf: np.ndarray,
    camera: Camera,
    fog_color: np.ndarray,
    fog_start: float,
    fog_end: float,
) -> None:
    v0 = tri.v0
    v1 = tri.v1
    v2 = tri.v2

    s0 = camera.camera_to_screen(v0.pos)
    s1 = camera.camera_to_screen(v1.pos)
    s2 = camera.camera_to_screen(v2.pos)

    x0, y0, z0 = s0
    x1, y1, z1 = s1
    x2, y2, z2 = s2

    area = (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0)
    if abs(area) < 1e-9:
        return
    if area < 0:
        x1, x2 = x2, x1
        y1, y2 = y2, y1
        z1, z2 = z2, z1
        v1, v2 = v2, v1
        area = -area

    min_x = max(int(np.floor(min(x0, x1, x2))), 0)
    max_x = min(int(np.ceil(max(x0, x1, x2))), camera.width - 1)
    min_y = max(int(np.floor(min(y0, y1, y2))), 0)
    max_y = min(int(np.ceil(max(y0, y1, y2))), camera.height - 1)
    if min_x > max_x or min_y > max_y:
        return

    xs = np.arange(min_x, max_x + 1, dtype=np.float64)
    ys = np.arange(min_y, max_y + 1, dtype=np.float64)
    X, Y = np.meshgrid(xs, ys)

    # Edge functions.  The +0.5 pixel centers avoid cracks between adjacent
    # triangles.
    Xc = X + 0.5
    Yc = Y + 0.5
    w0 = (x2 - x1) * (Yc - y1) - (y2 - y1) * (Xc - x1)
    w1 = (x0 - x2) * (Yc - y2) - (y0 - y2) * (Xc - x2)
    w2 = (x1 - x0) * (Yc - y0) - (y1 - y0) * (Xc - x0)
    inside = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9)
    if not inside.any():
        return

    l0 = w0 / area
    l1 = w1 / area
    l2 = w2 / area

    invz0 = 1.0 / max(z0, 1e-9)
    invz1 = 1.0 / max(z1, 1e-9)
    invz2 = 1.0 / max(z2, 1e-9)
    invz = l0 * invz0 + l1 * invz1 + l2 * invz2
    z = 1.0 / np.maximum(invz, 1e-12)

    depth_ok = inside & (z < zbuf[min_y : max_y + 1, min_x : max_x + 1])
    if not depth_ok.any():
        return

    u = (l0 * v0.uv[0] * invz0 + l1 * v1.uv[0] * invz1 + l2 * v2.uv[0] * invz2) / np.maximum(invz, 1e-12)
    v = (l0 * v0.uv[1] * invz0 + l1 * v1.uv[1] * invz1 + l2 * v2.uv[1] * invz2) / np.maximum(invz, 1e-12)

    if tri.texture is not None:
        tex = tri.texture
        uu = u.copy()
        vv = v.copy()
        if tex.wrap_u:
            uu = np.mod(uu, 1.0)
        else:
            uu = np.clip(uu, 0.0, 1.0)
        if tex.wrap_v:
            vv = np.mod(vv, 1.0)
        else:
            vv = np.clip(vv, 0.0, 1.0)
        tx = np.clip((uu * tex.width).astype(np.int32), 0, tex.width - 1)
        ty = np.clip((vv * tex.height).astype(np.int32), 0, tex.height - 1)
        rgba = tex.pixels[ty, tx]
        rgb = rgba[..., :3].astype(np.float32) * tri.shade
        alpha = rgba[..., 3].astype(np.int16)
    else:
        base = np.asarray(tri.color, dtype=np.float32) * tri.shade
        rgb = np.broadcast_to(base, (*inside.shape, 3)).copy()
        alpha = np.full(inside.shape, 255, dtype=np.int16)

    if tri.alpha_test:
        depth_ok &= alpha >= 128

    if not depth_ok.any():
        return

    if tri.fog:
        fog_t = np.clip((z - fog_start) / max(fog_end - fog_start, 1e-6), 0.0, 1.0).astype(np.float32)
        rgb = rgb * (1.0 - fog_t[..., None]) + fog_color[None, None, :] * fog_t[..., None]

    rows = slice(min_y, max_y + 1)
    cols = slice(min_x, max_x + 1)
    dst = frame[rows, cols]
    if tri.blend:
        a = (alpha.astype(np.float32) / 255.0)[..., None]
        dst[depth_ok] = dst[depth_ok] * (1.0 - a[depth_ok]) + rgb[depth_ok] * a[depth_ok]
        # Blended surfaces (shadows) do not write depth.
    else:
        dst[depth_ok] = rgb[depth_ok]
        if tri.depth_write:
            zbuf[rows, cols][depth_ok] = z[depth_ok]


def render(
    triangles: list[Triangle],
    camera: Camera,
    background: np.ndarray,
    fog_color: tuple[int, int, int] = (18, 24, 40),
    fog_start: float = 650.0,
    fog_end: float = 1600.0,
) -> np.ndarray:
    """Render triangles to an RGB uint8 image."""

    if background.shape != (camera.height, camera.width, 3):
        raise ValueError(f"background shape {background.shape} does not match camera {camera.width}x{camera.height}")

    frame = background.astype(np.float32).copy()
    zbuf = np.full((camera.height, camera.width), np.inf, dtype=np.float32)
    fog = np.asarray(fog_color, dtype=np.float32)

    opaque = [t for t in triangles if not t.blend]
    blended = [t for t in triangles if t.blend]

    # Camera-space transform is shared by all triangles.  Keep the scene's
    # world-space vertices untouched so the same scene can be re-rendered from
    # another camera (the interactive viewer relies on this).
    for tri in opaque:
        cam_tri = _to_camera_space(tri, camera)
        for clipped in _clip_near(cam_tri, camera.near):
            _render_triangle(clipped, frame, zbuf, camera, fog, fog_start, fog_end)

    # Blended geometry is sorted back-to-front.  This is cheap because the
    # demo only uses it for contact shadows.
    def avg_z(t: Triangle) -> float:
        cam = _to_camera_space(t, camera)
        return float(np.mean([cam.v0.pos[2], cam.v1.pos[2], cam.v2.pos[2]]))

    for tri in sorted(blended, key=avg_z, reverse=True):
        cam_tri = _to_camera_space(tri, camera)
        for clipped in _clip_near(cam_tri, camera.near):
            _render_triangle(clipped, frame, zbuf, camera, fog, fog_start, fog_end)

    return np.clip(frame + 0.5, 0, 255).astype(np.uint8)
