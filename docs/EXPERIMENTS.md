# Experiments

The project is organized as a set of experiments, not a single big-bang
renderer. Each experiment should be judged on a real game frame, not only the
synthetic world.

Status legend: **done** / **prototype** / **next** / **later**.

## E0 — Map-aware layer semantics

**Question:** Can we determine which BG is ground, canopy, roof, or UI from the
game's map data instead of tuning screen-space color thresholds?

**Method**

- Parse the map header/layout, primary and secondary tilesets, and
  `map.bin` blockdata.
- Decode each metatile's 16-bit attributes and layer type:
  Normal (`MAP_BLOCK_BG_21`), Covered (`MAP_BLOCK_BG_32`), or Split
  (`MAP_BLOCK_BG_31`).
- Map the authored layers to GBA BGs:
  - Normal: middle → BG2, top → BG1
  - Covered: bottom → BG3, middle → BG2
  - Split: bottom → BG3, top → BG1
- Render the exact BG layers offline, then use pixels only to find object
  extents inside the already-correct layer.

**Metrics**

- layer-role accuracy vs. a hand-checked map
- tree/house extraction accuracy on vanilla maps and romhacks
- how often a 2D fallback is needed

**Risks:** romhacks with nonstandard metatile data; maps that use Split
metatiles; games that do not use Pokémon-style map structures.

**Status:** research done. `docs/MAP-FORMAT-RESEARCH.md` records the verified
Porymap/Porytiles/pokeemerald mapping and the Route 101 / Littleroot ground
truth. Next is the offline compiler P0/P1: parse map data, select the correct
BG, then run deterministic object extraction.

## E1 — Temporal world atlas

**Question:** Can we accumulate screen-space BG textures into a persistent world
atlas so a tilted camera does not expose missing pixels?

**Method**

- Allocate an atlas per BG layer at the map size (256×256 or 512×512).
- Each frame, blit the current screen-space BG texture into the atlas at the
  layer's scroll offset.
- Maintain a coverage mask; never sample uncovered texels as opaque.
- Invalidate regions on VRAM/tilemap writes; keep a generation counter.
- At uncovered edges, fade to fog/clouds instead of a hard cut.

**Metrics**

- fraction of screen covered by valid atlas texels at 15°/25°/35° tilt
- visual popping when scrolling
- atlas memory and update cost per frame

**Risks:** scrolling backgrounds with wrap-around; affine layers; VRAM changes
every frame in some effects.

**Status:** prototype. `atlas.py` stitches offline dumps at their scroll
offsets, alpha-composites overlaps, and records a per-layer coverage mask;
VRAM invalidation and mGBA sequence capture are still missing.

## E2 — Auto-depth from parallax

**Question:** Can layer depth be inferred from how each layer scrolls relative
to a base layer?

**Method**

- Track `BGxHOFS/BGxVOFS` (or the renderer's `bg[i].x/y`) per frame.
- Choose the ground layer as the reference; compute `p = dx_i / dx_ref`.
- `p ≈ 1` → same plane; `p < 1` → farther; `p > 1` → closer; `p ≈ 0` → sky/UI.
- For affine layers, use the matrix scale/rotation as an additional cue.
- Smooth over time and expose a confidence value.

**Metrics**

- correlation between inferred depth and hand-authored depth on Emerald/FireRed
- stability during camera pans and map transitions

**Risks:** independently animated clouds/water; layers that scroll in discrete
steps; cutscenes.

**Status:** prototype. `parallax.py` estimates per-layer scroll ratios and a
confidence value from a dump sequence; it is not yet fed back into mesh heights
or profile selection.

## E3 — Generic tilemap decoder + voxel terrain

**Question:** Can a hardware-standard text-BG decoder produce a usable
heightfield for any GBA game/romhack?

**Method**

- Decode screen entries: tile index, H/V flip, palette bank.
- Decode char data: 4bpp/8bpp, 8×8 tiles.
- Build 256×256 / 512×512 map images from the 32×32 screen blocks.
- Decode Pokémon map/layout/metatile data when available, so the correct BG
  is chosen before meshing.
- Extrude opaque tiles to boxes or a heightfield.
- Alpha-test transparent pixels so cutouts remain cutouts.

**Metrics**

- decode correctness against mGBA screenshots (pixel diff)
- triangle count and mesh build time for a full map
- visual quality on Emerald, FireRed, and at least one romhack

**Risks:** 8bpp tile ordering; 2D OBJ tile mapping; mosaic; per-scanline affine.

**Status:** prototype. `gba.py` decodes text BGs; `synthetic.py` exercises the
same path; the research scripts render Route 101 and Littleroot from real
layout/metatile data. Affine/bitmap modes are deliberately rejected.

## E4 — Map-aware object extraction and 2.5D generation

**Question:** Can we extract trees, houses, and props deterministically from the
correct BG layer, then turn them into Octopath-style cards/meshes without ML?

**Method**

- Use E0 to select the world BG (Route 101 trees are BG2 middle-layer art, not
  BG1).
- Trees: connected components over the full BG image, then cut the actual
  canopy + trunk pixels and stand that authored cutout up as a camera-facing
  billboard. Do not synthesize a new crown or stretch the source: the game's
  tree art is already a slightly slanted view, so the trunk is part of the
  authored pixels.
- Houses: combine BG1 roof trim + BG2 walls/roof body, split roof vs wall,
  extrude four walls plus a roof plane.
- Clear the source footprint with tile-aware ground replacement, not
  generative inpainting.
- Add contact shadows and 2–3 depth slices per tree card.
- Keep detector parameters in profiles for romhack fallback.

**Metrics**

- human preference on 20 representative scenes
- percentage of objects extracted with high confidence
- overlap/empty-footprint rate in dense forests
- regression against the synthetic ground truth

**Risks:** over-extruding flat decoration; misclassifying water; battle scenes
with different layer semantics; romhack tilesets that break color heuristics.

**Status:** prototype. `relief.py` detects multi-tile green canopies and
composes the authored canopy+trunk cutout; `mesh.py` stands it up as a
camera-facing billboard with depth slices and shadows. The approved tree look
is the raw cutout (`docs/images/example-camera-billboard.png`); the committed
default still uses the older procedural side-view generator until the
`card_mode` switch lands. The current Route 101 render uses BG2 for trees.
`detect_structures()` and `mesh._add_structures()` implement the runtime P5b
house path: rectangular non-terrain blobs are split at the eave and extruded
as walls plus a gable roof with an overhang. Validated on an offline
Littleroot BG dump; a real in-game dump is still needed. No ML is required for
this path; ML remains an optional offline fallback for ambiguous romhack art.

## E5 — Sprite slabs + shadows

**Question:** Do billboarded OAM sprites with leaning tops and contact shadows
read as 2D characters in a 3D world?

**Method**

- Decode OAM entries and OBJ tiles to RGBA.
- Place a camera-facing billboard at the sprite's world position.
- Lean the top away from the camera by 5–12% of height.
- Project a blurred radial shadow onto the ground.
- Sort by OAM priority and world depth.

**Metrics**

- perceived grounding (shadow contact, lean)
- sorting errors when sprites overlap
- performance with 64+ sprites

**Risks:** sprites that are meant to lie flat (items, effects); affine sprites;
large bosses that should not be billboards.

**Status:** prototype. `sprites.py` provides stand-ins; `mesh.py` builds the
billboards and shadows. The dump path currently crops the composited OBJ layer;
real OAM/OBJ tile decoding is still later.

## E6 — Collision-learned heightmap

**Question:** Can the renderer learn which tiles are walkable/blocked from player
movement, without reading game code?

**Method**

- Identify the player sprite by input correlation and screen-center proximity.
- Convert its screen position to a world tile using the ground layer's scroll.
- Mark visited tiles as walkable; infer blocked tiles from failed movement.
- Over time, build a walkability/height map; extrude blocked tiles.

**Metrics**

- coverage after N minutes of play
- false blocked/walkable rate vs. a decomp collision map
- visual improvement over E4 priors

**Risks:** ledges, water, NPCs, scripted movement, save-state changes. This is
the most experimental idea; keep it optional.

**Status:** later. It is a good "novel approach" bet but not on the critical path.
For Pokémon maps, the layout/collision data already provides most of this, so
E6 is mainly a fallback for games without accessible map metadata.

## E7 — Diorama presentation

**Question:** What camera/lighting/post recipe makes the 3D2D read as a miniature
diorama rather than a tilted screenshot?

**Method**

- Camera: 20–35° pitch, slight yaw, 35–45° FOV.
- Plinth: two-tier stone base; the map reads as a physical object.
- Lighting: key light from upper-left; side walls shaded by normal.
- Shadows: contact shadows for sprites; optional AO at wall bases.
- Post: bloom, tilt-shift, vignette, gentle grade.
- Optional slow orbit for menus/title screens.

**Metrics**

- side-by-side preference vs. flat composite
- readability of UI/text at the chosen tilt
- stability of the look across scenes

**Risks:** overdoing blur/bloom; hiding pixel art; motion sickness from orbit.

**Status:** prototype. `pipeline.py` + `post.py` implement the fixed-camera
version; the Route 101 profile uses 35° pitch, 480 distance, 8° yaw, canopy
cards, and three depth slices per tree. The screenshot in the README is the
reference.

## E8 — Custom GPU 3D pass in the emulator

**Question:** What runtime renderer is needed to show the 3D2D scene inside the
emulator at full speed, instead of rendering offline PNGs?

**Method**

- Fork mGBA's GL renderer; keep its 2D path as the fallback.
- After the normal layer finalize, upload the BG/OBJ textures and the compiled
  map manifest to GPU textures.
- Draw a single 3D scene into an offscreen FBO:
  - ground plane from the repaired tilemap;
  - tree cards with alpha test and 2–3 depth slices;
  - house wall/roof meshes with depth write;
  - OBJ billboards and contact shadows;
  - fog, directional tint, and optional bloom/tilt-shift post.
- Use one camera/profile per scene; preserve a 2D fallback for menus, battles,
  affine/bitmap modes, and maps without a compiled manifest.
- Keep the CPU `raster.py` renderer as the reference implementation for
  correctness tests and visual comparisons.

**Metrics**

- frame time at 1080p and 4K on the target GPU
- draw calls and VRAM per map
- visual parity with the CPU reference
- fallback correctness on menus/battles/unknown maps

**Risks:** GL/GLES context and FBO lifetime in mGBA; shader portability;
transparent sorting; emulator UI overlays; driver-specific readback behavior.

**Status:** next. No in-emulator 3D pass exists yet. The mGBA dump hook proves
we can access the per-layer FBOs, and the CPU renderer already defines the
scene/camera/mesh contract that the GPU pass should consume.

## Recommended order

1. **E0**: map-aware layer semantics. Research is done; next is the offline
   compiler's map parser.
2. **E3 + E4**: map-aware layer rendering, deterministic tree/house extraction,
   cards/meshes, and tile-aware ground repair. This is the correctness core.
3. **E8**: custom GPU 3D pass in the emulator, fed by the compiled manifest.
4. **E1 + E2**: world atlas and parallax depth once the camera can move beyond
   one screen.
5. **E7**: presentation polish (camera, shadows, bloom, tilt-shift, grade).
6. **E5**: real OAM/OBJ decoding and sprite sorting.
7. **E6**: optional collision learning for games without usable map metadata.

## ML policy

- The critical path must work with **zero ML**: map data → layer selection →
  deterministic object extraction → cards/meshes → GPU pass.
- No ML at runtime.
- ML is an optional offline fallback for ambiguous romhack art or games without
  accessible map metadata. It must never be required to compile or render the
  base games.
- Prefer tile/metatile classifiers and manual corrections over generative
  inpainting; preserve the GBA palette and dithering.

## Evaluation harness

Every experiment should produce:

- a deterministic input (dump directory + frame id)
- a rendered PNG at a fixed resolution
- a short metrics JSON (triangle count, atlas coverage, confidence histogram)
- a side-by-side with the stock mGBA composite

Keep the dumps out of git if they contain ROM-derived art. Commit only synthetic
fixtures and metrics.
