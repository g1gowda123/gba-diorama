# Design notes

This document records the architecture behind `gba-diorama` and the reasoning
that led to it. It is meant to be read before changing the renderer.

## 1. The core bet

A GBA frame is not one image. The PPU composes:

- four tiled backgrounds, BG0–BG3
- an OBJ/sprite layer
- windows (WIN0, WIN1, OBJ window)
- color effects (alpha blending, brightness up/down)
- a backdrop color

mGBA's OpenGL renderer already renders BG0–BG3 into separate framebuffers and
keeps the OBJ color, OBJ flags, window mask, and backdrop in separate textures.
It then runs a finalize shader that reproduces the GBA priority/window/blend
rules. That means **layer extraction is a solved problem**; the interesting work
is interpreting the layers as a scene.

The core bet is therefore:

> A generic renderer can infer enough 3D structure from the PPU's layer images
> and registers to produce a convincing 3D2D diorama, without game-specific ROM
> patches.

That is what makes romhacks work: the renderer consumes the same state the PPU
does, not decomp symbols or a hash-gated executable.

## 2. What "3D2D" means here

"HD-2D" is a presentation style: 2D sprite art in a 3D world, with depth of
field, bloom, and strong lighting. "3D2D" is the more precise term for the
technical goal:

- **3D world:** ground planes, cliffs, buildings, trees, props with real depth.
- **2D art:** characters and many props remain pixel-art billboards.
- **2D UI:** menus, textboxes, and battle HUDs stay flat and readable.

The renderer is not a post-process shader. A RetroArch/slang shader only sees the
final 240×160 composite; by then the layers are already flattened. This has to
live inside the emulator core.

## 3. Layer roles

The GBA's layer priorities are scene-dependent. In Pokémon Emerald/FireRed's
overworld (BG mode 0, four text BGs), the decomp shows:

- BG0: UI/textbox (`src/menu.c:84`, `src/menu.c:155`); `WINOUT` enables only BG0
  outside the window.
- BG1: top/foreground world layer; metatiles drawn here "cover object event
  sprites" (`src/field_camera.c:246`).
- BG2: middle world layer.
- BG3: bottom/ground layer.
- OBJ: characters, NPCs, props.

Battle scenes use the same mode but different priorities (BG0=0, BG1=0, BG2=1,
BG3=3 in `src/battle_bg.c:123`), which is exactly why the core cannot hardcode
"BG1 is always foreground." The scene signature must include priorities.

The synthetic demo uses `{3: ground, 2: mid, 1: top}` and leaves BG0 for a future
flat UI overlay.

## 4. Scene signature and profiles

A profile is a data record, not code. The loader lives in
`gba_diorama/profiles.py`; the supported TOML shape is:

```toml
[profile.emerald.overworld]
match = { mode = 0, enabled = 0b1111, priorities = [0, 1, 2, 3] }
layers = [
  { bg = 3, role = "ground", height = 0.0, material = "ground" },
  { bg = 2, role = "world",  height = 6.0, material = "object" },
  { bg = 1, role = "world",  height = 18.0, material = "object" },
  { bg = 0, role = "ui",     height = 0.0, flat = true },
]
camera = { pitch_deg = 28.0, yaw_deg = 0.0, distance = 600.0 }

[profile.emerald.battle]
match = { mode = 0, enabled = 0b1111, priorities = [0, 0, 1, 3] }
layers = [
  { bg = 3, role = "ground", height = 0.0 },
  { bg = 2, role = "world",  height = 8.0 },
  { bg = 1, role = "world",  height = 8.0 },
  { bg = 0, role = "ui",     flat = true },
]
```

Matching order implemented by `ProfileBook.match()`:

1. Game code (`mGameInfo.code`, e.g. `BPEE`) + scene signature.
2. Signature-only profile (romhacks and dumps without game info).
3. Generic fallback: choose the ground from alpha coverage × tile variety,
   stack layers in front of it, keep BG0 UI flat, mild camera.

An exact ROM-hash override is still a future extension. The current mGBA dump
hook does not write `mGameInfo`; `--profile` therefore usually selects by
signature.

`SceneProfile.camera` supports `pitch_deg`, `yaw_deg`, and `distance`; the
offline `dump_camera()` uses them when present. Profiles should be
hot-reloadable while the emulator runs. A debug overlay should let the user
cycle layer roles and heights live; that is faster than guessing from
screenshots.

## 5. Reconstructing geometry

### 5.1 Screen-space cards (MVP)

The simplest 3D2D look treats each layer as a textured card at a different
depth. It requires no tilemap decoding and works for any game:

- BG3 at depth 0, BG2 at depth 1, BG1 at depth 2, OBJ at depth 1.5.
- Camera tilt + parallax + post FX.

This is a good first visual, but it has two problems:

- Tilting reveals missing off-screen pixels at layer edges.
- Layers are flat; cliffs and buildings do not have real sides.

It is useful as a fallback and as an A/B reference, not the final architecture.

### 5.2 World-space heightfields (current lab)

Each text BG is decoded to a full tilemap image (256×256 or 512×512). The
renderer builds a heightfield:

- One height per present tile/cell.
- Present cells become top faces, using the decoded BG image as the texture.
- Adjacent equal-height cells merge into greedy rectangles to cut triangles.
- Where a cell is higher than its neighbor, emit a side wall from the neighbor
  height to the cell height.
- Side walls use a material texture chosen by the profile/heuristic (dirt,
  cliff, trunk, wall, rock, wood, water, grass, stone).
- The map sits on a two-tier plinth so it reads as a physical diorama.

This is what `mesh.py` implements. The synthetic world authors the heights and
materials; a real integration will infer them.

#### Canopy cards (prototype)

`relief.py` adds a generic card heuristic for tree canopies that are painted
into the ground layer rather than living on a separate BG:

- Convert the composited ground to HSV and select saturated green pixels.
- Morphologically open the mask to break one-pixel outlines that connect
  adjacent trees, then take 4-connected components.
- Extend each component through a narrow band of connected dark pixels to pick
  up the trunk/outline, without letting dark ground shadows merge neighbors.
- Compose the canopy pixels and the actual trunk pixels without the grass gap,
  so the card keeps the game's own tree colors and proportions.
- Build a cutout texture from the component pixels and fill the corresponding
  ground pixels from a bright ring of surrounding grass, so the tree is not
  drawn twice and the fill does not leave a flat teal rectangle.
- Stand each component up as a camera-facing side-view card. A deterministic
  RNG seeded by the component bbox varies the crown proportions and the number,
  placement, scale, and mirroring of a central mass plus 3-5 satellite lobes. All opaque pixels
  still come from the source canopy/trunk art; there is no sprite atlas or
  hand-placed tree. Multi-tile trees are handled as one connected component
  because detection runs on the full BG image, not per 8×8 tile.
- Use the component boxes for contact shadows; BG1 billboard layers use the
  same card path.

The result is deliberately a pop-up sprite-card look, not a heightfield. The
source art is top-down, so the card reads as a stylized 2.5D tree rather than a
hand-drawn side view. This is a visual prior, not a semantic classifier; it
currently only recognizes green foliage, and buildings/rocks/non-green props
still need the general E4 classifier.

### 5.3 World atlas (prototype)

`atlas.py` implements the offline half: it places each BG layer at its scroll
offset in a larger canvas, composites overlapping dumps, and records a
per-layer coverage mask. The persistent in-emulator version still needs VRAM
invalidation and sequence capture.

mGBA's BG textures are screen-space (240×160). A tilted camera exposes their
edges. The fix is a persistent **world atlas**:

- Each frame, copy the visible BG texture into a larger atlas at the layer's
  scroll offset.
- Keep a coverage mask of which atlas texels have ever been observed.
- Invalidate regions when VRAM/tilemap changes.
- At unknown edges, fade to fog/clouds rather than showing a hard rectangle.

For 256×256 maps, a 256×256 or 512×512 atlas per layer is enough. For 512×512
maps, use 512×512. The atlas is independent of output resolution, so it can be
rendered at 1× and sampled at 4–8×.

This also enables parallax-based depth: if layer `i` scrolls by `dx_i` and the
base layer by `dx`, then `p = dx_i / dx` approximates its depth (`p=1` same
plane, `p<1` farther, `p>1` closer, `p=0` sky/UI). Affine matrices give
scale/rotation directly.

## 6. Tile classification

The hardest inference problem is deciding what a tile *is*. Candidate features:

- **Opacity:** fully opaque, smooth regions are likely ground; cutouts with
  transparent borders are likely objects.
- **Edge density:** high edge density and isolated islands suggest props/trees;
  low density suggests terrain.
- **Tile frequency:** common tiles are terrain; rare tiles are objects.
- **Color continuity with the layer below:** a tile whose color matches the
  ground is likely ground decoration.
- **Priority/mode:** BG3 ground, BG1 foreground are strong priors in top-down
  games, but not universal.
- **Temporal stability:** UI and parallax layers move differently from the world.

The output is a per-cell `(height, material, confidence)` tuple. Low-confidence
cells should fall back to a flat card or the stock 2D composite rather than
producing a bad extrusion.

## 7. Sprites

OAM gives position, size, tile index, palette bank, flips, priority, and affine
parameters. For the first version:

- Decode each OBJ into an RGBA texture (4bpp/8bpp, 1D/2D mapping).
- Place a camera-facing billboard at the sprite's world position.
- Lean the top away from the camera by a few percent of its height, so it reads
  as a standing slab rather than a flat sticker.
- Project a blurred radial shadow onto the ground below.
- Sort by OAM priority and world depth; the GBA's per-scanline sprite limits are
  intentionally ignored for the 3D pass.

The synthetic `sprites.py` is a stand-in for this path.

## 8. Rendering and post

`raster.py` is a small CPU rasterizer:

- near-plane clipping
- screen-space edge functions
- perspective-correct UV interpolation
- z-buffer
- alpha test for cutouts
- alpha blend for shadows
- distance fog

It is intentionally simple so experiments are reproducible. The eventual mGBA
port uses the same mesh data with OpenGL shaders.

Post stack (`post.py`):

- bloom on highlights
- tilt-shift blur on the top/bottom bands
- vignette
- gentle warm-highlight/cool-shadow grade

Keep pixel art nearest-filtered. Render at 4–8× the GBA resolution and decouple
atlas resolution from output resolution.

## 9. Correctness and fallback

The 3D pass is an art direction layer, not a replacement for GBA semantics.
Scenes with effects that depend on the final 2D composite should use the stock
path:

- affine/raster effects (mode 1/2, mode 7-style scenes)
- bitmap modes 3/4/5
- windows and alpha/brightness effects that the 3D pass does not model
- menus, textboxes, and battle HUDs
- unknown games/scenes with low classification confidence

The fallback can be per-layer: flat BG0 UI over a 3D world is fine; a full 2D
fallback is required for title screens and effect-heavy scenes.

## 10. Open questions

- How much of the world atlas can be built before VRAM changes invalidate it?
- Can parallax estimate depth reliably in scenes with independently scrolling
  clouds/water, or does it need per-layer confidence?
- How to handle sprites that are partially occluded by BG1 foreground without
  losing the "sprite sits between BG1 and BG2" ordering?
- What is the minimum profile data that makes a romhack look good without
  per-hack authoring?
- Can collision learning (E6) produce a useful heightmap from player movement
  alone, or is it too slow for a demo?
