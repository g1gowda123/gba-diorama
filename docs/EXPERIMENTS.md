# Experiments

The project is organized as a set of experiments, not a single big-bang
renderer. Each experiment should be judged on a real game frame, not only the
synthetic world.

Status legend: **done** / **prototype** / **next** / **later**.

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
- Extrude opaque tiles to boxes or a heightfield.
- Alpha-test transparent pixels so cutouts remain cutouts.

**Metrics**

- decode correctness against mGBA screenshots (pixel diff)
- triangle count and mesh build time for a full map
- visual quality on Emerald, FireRed, and at least one romhack

**Risks:** 8bpp tile ordering; 2D OBJ tile mapping; mosaic; per-scanline affine.

**Status:** prototype. `gba.py` decodes text BGs; `synthetic.py` exercises the
same path. Affine/bitmap modes are deliberately rejected.

## E4 — Height heuristics

**Question:** Can we assign per-cell heights/materials without per-game data?

**Method**

- Start with priors: BG3=0, BG2=4–8, BG1=12–24, BG0=UI.
- Compute opacity, edge density, tile frequency, and color continuity.
- Classify cells as ground / low prop / tall prop / UI.
- Assign a confidence; low confidence falls back to flat.
- Cache meshes per unique tile/metatile.

**Metrics**

- human preference on 20 representative scenes
- percentage of cells classified with high confidence
- regression against the synthetic ground truth

**Risks:** over-extruding flat decoration; misclassifying water; battle scenes
with different layer semantics.

**Status:** later. The synthetic world currently authors heights directly so the
meshing/raster path can be developed independently.

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
billboards and shadows.

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

## E7 — Diorama presentation

**Question:** What camera/lighting/post recipe makes the 3D2D read as a miniature
diorama rather than a tilted screenshot?

**Method**

- Camera: 15–30° pitch, slight yaw, 35–45° FOV.
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
version; the screenshot in the README is the reference.

## Recommended order

1. **E1 + E2 + E7**: real layer dump, atlas, parallax depth, presentation. This
   gives a compelling real-game demo with a fixed camera.
2. **E3 + E4**: full-map meshing and heuristics, so the camera can move and the
   renderer works on more scenes.
3. **E5**: real OAM/OBJ sprites and shadows.
4. **E6**: optional collision learning.

## Evaluation harness

Every experiment should produce:

- a deterministic input (dump directory + frame id)
- a rendered PNG at a fixed resolution
- a short metrics JSON (triangle count, atlas coverage, confidence histogram)
- a side-by-side with the stock mGBA composite

Keep the dumps out of git if they contain ROM-derived art. Commit only synthetic
fixtures and metrics.
