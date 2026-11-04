# gba-diorama

An experimental **3D2D** renderer lab for GBA-era games and romhacks.

The goal is not to upscale the final 240×160 frame. It is to take the GBA PPU's
already-separated background layers, reconstruct a small 3D diorama from them,
and composite 2D sprite art into that world — the "3D world, 2D characters"
look rather than a flat post-process.

![Synthetic diorama demo](docs/images/demo.png)

The image above is generated entirely by this repo from a synthetic
GBA-format world: four 4bpp text-BG tilemaps, a 64×64 metatile map, extruded
terrain, billboard characters, contact shadows, and a CPU post stack.

The first real result comes from an mGBA dump of Pokémon Emerald's Route 101:

![Emerald Route 101 as a 3D2D diorama](docs/images/emerald-route101.png)

The dump loader picked BG2 as the ground from layer content (BG3 is a
low-detail backdrop in this scene), composited the backdrop into transparent
holes, tightly cropped the player out of the OBJ layer to ground the sprite at its
feet, and rendered it as an upright billboard on a tilted plinth. It also detects
world props and trees across layers: multi-layer metatiles (e.g. BG1 elevated
treetops + BG2 trunks/bases) are unified before segmentation, and a procedural
detector can segment arbitrary custom ROM hack tilesets (such as autumn, snowy,
or alien palettes) by discovering dominant ground terrain without hardcoded
color constraints.

Each tree becomes an upright side-view card generated from its own canopy and
trunk pixels. A seeded RNG varies the crown proportions and overlapping lobes,
and the crown is stretched vertically so the top-down footprint reads as a
standing tree instead of a squat bush. Cards are capped to their source
footprint and rendered with three depth slices (a darker, slightly wider card
behind the main card for canopy thickness). The cleared footprint is filled
with small patches of the surrounding ground texture, preserving the map's
grass, path, and palette instead of leaving a flat glowing hole. No synthetic
trunk, sprite atlas, or hand-placed tree is used.

The layers are decoded independently before meshing, which is the part that
makes a 3D2D composite possible:

![Decoded BG layers](docs/images/layers.png)

## Why this exists

A GBA has four tiled background layers (BG0–BG3) plus an OBJ/sprite layer. An
emulator with a high-resolution renderer already has each of those layers as a
separate texture. That is the hard part of layer extraction, and it is already
solved by [mGBA](https://mgba.io/).

What is missing is a renderer that understands those layers as a *scene*:

- BG3 is usually the ground.
- BG2 is usually mid-height scenery.
- BG1 is usually foreground/canopy geometry that occludes sprites.
- BG0 is usually UI (textboxes, menus) and should stay flat.
- OBJ sprites are characters and props, which should become billboards.

Those roles are game- and scene-specific, so the core stays generic and the
roles live in **profiles** that can be edited and overridden per game, per
romhack, or per scene.

This is deliberately **not** a static recompilation. It has to work on arbitrary
ROMs and romhacks, so it only consumes PPU state: VRAM, OAM, palette, and the
BG/OBJ registers.

## Quick start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install numpy pillow pytest

python -m gba_diorama --out docs/images/demo.png
python -m pytest -q
```

Useful CLI options:

```bash
# Wider camera angle (degrees around the diorama)
python -m gba_diorama --yaw 18 --out /tmp/yaw18.png

# No bloom / tilt-shift / grading
python -m gba_diorama --no-post --out /tmp/raw.png

# Higher-resolution render
python -m gba_diorama --width 1920 --height 1080 --out /tmp/1080p.png

# Render a real mGBA dump produced by patches/mgba-diorama-dump.patch
python -m gba_diorama --dump path/to/dump --out /tmp/dump.png

# Stitch dumps captured at different scroll positions into a larger world atlas
python -m gba_diorama --atlas path/to/dump-a path/to/dump-b --out /tmp/atlas.png

# Show every BG overlay, even sparse foreground layers
python -m gba_diorama --dump path/to/dump --all-layers --out /tmp/all-layers.png

# Apply an explicit data-only scene profile
python -m gba_diorama --dump path/to/dump \
    --profile profiles/emerald-route101.toml --out /tmp/profile.png

# Force the 3D path for a UI scene (debugging only)
python -m gba_diorama --dump path/to/dump --force-3d --out /tmp/ui-3d.png

# Inspect a real mGBA dump
python -m gba_diorama.inspect path/to/dump --out /tmp/dump-layers
```

To capture a frame from mGBA's OpenGL renderer, apply
`patches/mgba-diorama-dump.patch` to mGBA `39a3d93`, build the Qt frontend, then:

```bash
tools/run-mgba-diorama.sh /path/to/game.gba 4800
# later, while the game is running:
touch dumps/<game>/trigger
```

The launcher selects desktop OpenGL (`QT_OPENGL=desktop`), enables the GL
renderer at 2× scale, and starts muted. It uses a permanent ROM path so mGBA
does not show the temporary-storage warning. The dump hook writes BG0–BG3,
OBJ, VRAM, palette, OAM, and registers; `trigger` captures on demand.

## How it works

```
mGBA PPU state (VRAM / OAM / palette / registers)
        │
        ▼
gba.py       decode BG0–BG3 to RGBA layer images + tilemap metadata
        │
        ▼
profiles     scene signature -> layer role, height, material, parallax
        │
        ▼
mesh.py      heightfields + canopy cards + plinth + sprite billboards
        │
        ▼
raster.py    3D rasterizer (z-buffer, alpha test, blend, fog)
        │
        ▼
post.py      bloom, tilt-shift, vignette, grade
```

The same `mesh.py` path is used by the synthetic lab world and, later, by the
emulator. The synthetic world is not a mock-up of the decoder; it writes real
GBA tilemaps into a 0x18000-byte VRAM buffer and decodes them back through
`gba.py`. That keeps the offline lab honest.

### Current layer model

| Layer | Typical role | Geometry |
|---|---|---|
| BG3 | ground | heightfield, fully opaque |
| BG2 | mid scenery / props | heightfield, alpha-tested cutouts |
| BG1 | foreground / canopy | heightfield or upright cutout cards |
| BG0 | UI / textbox | flat 2D overlay (not in the demo yet) |
| OBJ | characters / props | camera-facing billboards + shadows |

Those are priors, not rules. Route 101 uses BG2 as the ground because BG3 is a
low-detail backdrop; `DumpWorld` scores each layer by alpha coverage and tile
variety and only extrudes layers in front of the winner. The synthetic world
authors heights and materials directly. The next step is to infer them (see
`docs/EXPERIMENTS.md`).

### Coordinate system

World space is `(x, height, z)`, Y up. One world unit is one GBA BG pixel. The
synthetic map is 64×64 tiles (512×512 pixels). The camera looks down at roughly
25–30°, which is enough to read the diorama without exposing the missing
off-screen map data too badly.

### World atlas

A single dump only contains the 240×160 visible screen. Tilting the camera
exposes the missing edges. `gba_diorama/atlas.py` starts E1: it places each BG
layer at its scroll offset in a larger canvas and alpha-composites overlapping
frames. Feed it several dumps of the same scene captured at different scroll
positions:

```bash
python -m gba_diorama --atlas dumps/route-a dumps/route-b --out /tmp/atlas.png
```

The atlas is still offline. It records a per-layer coverage mask, and
`--atlas` prints the E2 scroll-ratio/confidence estimate for each layer, but it
does not yet invalidate regions when VRAM changes or capture a sequence
automatically; those are the next steps before it can replace the temporary
sparse-overlay skip.

## What works

- 4bpp and 8bpp text-BG decoding, per-tile palette banks, H/V flips, 32×32
  screen blocks, 256×256 / 512×256 / 256×512 / 512×512 maps.
- Palette decode and OAM entry decode primitives.
- A synthetic GBA-format overworld: terrain, plateau, pond, path, house,
  garden, fence, dock, trees, tall grass, flowers, rocks, bushes, and three
  characters.
- Heightfield meshing with greedy equal-height merging, per-cell side walls,
  material side textures, and a two-tier tabletop plinth.
- Camera-facing sprite billboards with leaning tops and radial contact shadows.
- Software 3D rasterizer: near-plane clipping, perspective-correct UVs,
  z-buffer, alpha test, alpha blending, fog.
- CPU post stack: bloom, tilt-shift DOF, vignette, grade.
- A loader/exporter for the mGBA dump format plus `python -m gba_diorama.inspect`
  to write layer PNGs from a dump.
- Real mGBA dump rendering: content-based ground selection, non-square world
  extents, and OBJ-layer sprites cropped from OAM entries.
- Canopy cards from BG2/BG1: foliage-color component detection, morphological
  splitting of touching trees, procedural multi-lobe side-view generation from
  actual canopy pixels, actual-trunk scaling, three depth slices per tree,
  ground fill-under, and contact shadows.
- A 2D fallback for menus/battles and non-text BG modes; `--force-3d` overrides
  it for debugging.
- Data-only TOML scene profiles: match a BG-mode/enabled/priority/affine/size
  signature, then assign ground/world/UI roles, heights, materials, and camera
  distance/pitch/yaw.
- Trigger-file dump capture, a desktop-GL launcher, and BG/OBJ orientation
  handling for the mGBA patch.
- Deterministic CLI and 43 tests.

## What is not done yet

- No in-emulator 3D pass yet. The mGBA dump hook exists as a patch; the lab
  renders both the synthetic world and real dumps offline.
- No affine BG, bitmap BG, windows, mosaic, or GBA alpha/brightness effects.
- Real sprites are cropped from the composited OBJ layer; OAM/OBJ tile decoding
  is still a primitive, not a full per-sprite renderer.
- Canopy cards are a foliage-color heuristic, not semantic understanding: they
  do not yet separate trees from green buildings. The side-view generator is
  deliberately procedural (seeded by each component bbox) and keeps the GBA
  palette, but the source art is top-down; the result reads as stylized pop-up
  sprites, not hand-painted Octopath foliage. Multi-tile trees are handled as
  one component, but dense forests still need better splitting and depth-card
  volume, and the tiny source trunks can be hard to read at near-edge angles.
- Sparse foreground layers are still skipped by default until height inference
  can tell tree canopies from buildings.
- Profiles are opt-in (`--profile`); there is no automatic profile discovery or
  debug overlay yet.
- The atlas is offline only; it does not yet invalidate changed VRAM or capture
  a sequence automatically. The camera stays mild so missing off-screen pixels
  are not obvious.
- The rasterizer is CPU-side and meant for iteration, not real-time emulator
  output.

## Repo map

```
gba_diorama/
  gba.py         GBA text-BG / palette / OAM decoding
  dump.py        load/export the mGBA dump format + DumpWorld adapter
  profiles.py    scene signatures + data-only TOML layer profiles
  inspect.py     dump -> per-layer PNGs
  synthetic.py   synthetic GBA-format world (the test fixture)
  relief.py      canopy detection, cutout field, ground fill-under
  mesh.py        heightfields, canopy cards, side walls, plinth, billboards
  raster.py      software 3D rasterizer
  post.py        bloom / tilt-shift / vignette / grade
  sprites.py     procedural pixel-art stand-ins
  pipeline.py    demo camera + render pipeline
  __main__.py    CLI
tests/           decoder, dump, mesh, raster, pipeline tests
profiles/
  emerald-route101.toml  example signature/profile override
docs/
  DESIGN.md      deeper architecture and rendering notes
  EXPERIMENTS.md experiments E1–E7, hypotheses and order
  MGBA-HANDOFF.md exact mGBA hooks, patch, and dump format
AGENTS.md        handoff notes for the next agent
```

## Roadmap

**M0 — offline lab (done)**
Synthetic GBA tilemaps → decoder → heightfield diorama → post stack.

**M1 — real layer dump (done)**
`patches/mgba-diorama-dump.patch` adds the opt-in dump hook to mGBA's GL
renderer. It exports BG0–BG3, OBJ, VRAM, palette, OAM, registers, and a frame
id. `dump.py` loads it, `inspect.py` writes per-layer PNGs, and
`python -m gba_diorama --dump` meshes a real frame. Validated on Emerald Route
101.

**M2 — profiles and fallback (in progress)**
Scene signatures (`mode`, enabled mask, priorities, affine mask, screen sizes)
and the TOML profile loader are implemented. `--profile` maps BG layers to
ground/world/UI roles, heights, and materials. The stock 2D composite handles
menus, battles, and affine/raster scenes. Profiles can also override camera
distance, pitch, and yaw. Remaining: automatic profile discovery and a debug
overlay.

**M3 — world atlas and parallax depth**
Stitch BG layers into a persistent world atlas as the camera scrolls. Estimate
per-layer parallax from scroll deltas and convert that to depth. Add a per-sprite
atlas from OAM.

**M4 — mGBA GL pass**
Port the mesh + camera math to a `hd2d` pass inside mGBA's GL renderer, keeping
the existing `_finalizeLayers` as fallback. Start with a fixed camera and a
single scene profile.

**M5 — inference and presentation**
Tile classification (opaque ground vs. cutout object), collision-learned
heightmaps, temporal accumulation, shadows/AO, optional orbit camera, widescreen
and overscan.

## mGBA integration in one paragraph

Fork mGBA. `GBAVideoGLRendererFinishFrame()` at
`src/gba/renderers/gl.c:1573` already calls `_finalizeLayers()` at
`src/gba/renderers/gl.c:1696`, which binds `bg[0..3].tex` plus
`layers[OBJ_COLOR]`, `layers[OBJ_FLAGS]`, `layers[WINDOW]`, and
`layers[BACKDROP]`. Replace/augment that finalize pass with the 3D scene pass.
`GBAVideoGLRendererSetScale()` at `gl.c:2149` is the resize hook. The renderer
already has `bg[i].x/y`, `screenBase`, `charBase`, `size`, `priority`, and
`affine`; the `GBAVideoRenderer` vtable exposes `palette`, `vram`, and `oam`.
See `docs/MGBA-HANDOFF.md` for the exact fields and the implemented dump patch.

## Design principles

1. **Game-agnostic core.** No Pokémon/Zelda constants in `gba.py`, `mesh.py`, or
   `raster.py`.
2. **Profiles are data, not code.** A romhack inherits its base game's profile
   and can override layer roles, heights, materials, and camera.
3. **Keep the 2D path.** Menus, battles, affine scenes, and unknown games must
   still render correctly through mGBA's stock composite.
4. **Infer, don't require.** Authored decomp data is a bonus, not a dependency.
5. **Pixel art stays pixel art.** Nearest filtering, integer-ish texel density,
   and restrained post FX; the 3D geometry can be smooth.

## Legal

This repo contains no ROMs, no game assets, and no Nintendo code. The synthetic
world and sprites are original placeholder art. Pokémon, Zelda, and GBA are
trademarks of Nintendo; this project is not affiliated with or endorsed by
Nintendo. Do not commit ROMs, saves, or extracted game assets.

The code is licensed under the Mozilla Public License 2.0 (MPL-2.0), which keeps
it compatible with a future mGBA fork.

## Handoff

If you are an agent continuing this work, read `AGENTS.md` first. The short
version:

- Run `python -m pytest -q` before and after changes.
- Keep `decode_text_bg()`'s API stable; it is the boundary between emulator and
  renderer.
- `Scene.triangles` are world-space and must not be mutated by the renderer.
- Add a test for every heuristic; the synthetic world is the regression fixture.
- Do not hardcode game-specific layer roles outside a profile.
