# Optional path: offline map compiler / prebaked object atlas

**Status:** proposal, not on the current critical path.  
**Purpose:** let another agent pick up a prebake pipeline without changing the
existing runtime contract. No code is wired to this yet.

## Short version

Compile each ROM once, offline:

1. enumerate maps and render their BG layers at native resolution;
2. detect/cut objects (trees, houses, rocks, signs, etc.);
3. repair the ground underneath with surrounding tiles;
4. generate a 2.5D representation (cards for foliage, boxes for buildings);
5. store an atlas + per-map manifest in a ROM-keyed cache.

At runtime the emulator just loads the manifest for the current map and renders
the stored objects. No ML, no per-frame detection, no map walking.

The compiler is **map-data-first**, not ML-first. For Pokémon-style games the
layout, tilesets, metatiles, and layer types already tell us which BG is ground
and which is canopy/roof. The deterministic path must work end-to-end with zero
ML; ML is only an optional offline assistant for ambiguous romhack art.

This is a **content pipeline**, not a runtime feature. It can be implemented
without changing the current `gba_diorama` renderer or mGBA patch.

## Why this path exists

- GBA tilemaps have no semantic object list. Trees and houses are painted into
  BG layers as metatiles/tiles.
- Runtime segmentation is fragile, slow, and hard to make consistent across
  romhacks.
- A one-time compile can use heavier tools (including ML) and can be manually
  corrected.
- The result is small, cacheable, and reusable across emulator sessions.
- The current renderer already consumes object-like data (canopy components,
  cards, sprites, profiles); a compiler can feed it instead of re-deriving
  objects from each dump.

## Non-goals

- Do not distribute extracted ROM art or compiled atlases.
- Do not make ML a runtime dependency.
- Do not replace the existing 2D fallback for menus/battles/unknown maps.
- Do not require a specific emulator for cache generation; the cache is a
  sidecar keyed by ROM hash.

## Pipeline

### P0. Enumerate maps

For Pokémon-style games:

- parse map headers/layouts/tilesets/metatiles from the ROM;
- decode each metatile's layer type and map it to BG1/BG2/BG3
  (Normal: middle→BG2, top→BG1; Covered: bottom→BG3, middle→BG2;
  Split: bottom→BG3, top→BG1);
- render each map's BG layers offline at native resolution;
- record map ID, dimensions, tileset IDs, layer roles.

For arbitrary GBA games/romhacks:

- use emulator layer dumps while scrolling and stitch a world atlas, or
- parse tilemaps/metatiles from VRAM and map data.

The compiler must not depend on the player having visited the map.

### P1. Separate ground from objects

Prefer tile-level evidence over screen-level pixels:

- cluster repeated 8x8 tiles by palette/index;
- classify tiles as ground/grass/path/water/roof/wall/tree/rock/etc.;
- use layer role, alpha coverage, tile frequency, edges, and connected
  components;
- treat large connected structures as candidate objects;
- detect rectangular roof/wall regions for buildings.

This can be deterministic. Start with tile/metatile clustering, layer role,
and connected components; add a small tile classifier only if a romhack corpus
shows the classical path failing. ML is an optimization, not a prerequisite.

Verified ground truth: Route 101's full trees are on **BG2** (386 Normal / 14
Covered / 0 Split; BG1 is only sparse fringes). Littleroot houses split across
**BG1 roof trim + BG2 walls/roof body**.

### P2. Detect object instances

- trees/foliage: multi-tile connected components over the full world BG
  (BG2 for Route 101), not per 8×8 tile;
- houses: rectangle/roof-wall detection on the metatile grid, combining the
  BG1 roof trim with the BG2 walls/roof body;
- rocks/signs/fences: template or tile-classifier matches;
- optional ML assist for ambiguous regions:
  - SAM / SAM 2 masks;
  - Grounding DINO + SAM labels;
  - only offline, only for uncertain cases.

Store confidence and a `needs_review` flag.

### P3. Extract cutouts and metadata

Each object record should carry:

```text
asset_id
kind                 tree | house | rock | sign | fence | ...
layer                BG index
bbox                 source pixel bounds
anchor               bottom-center/trunk base in source coords
footprint            ground rectangle
height               world units
z / depth offset
collision            optional polygon/rect
frames               optional animation frames
confidence
```

The cutout is RGBA, palette-preserving, with transparent pixels outside the
object mask.

### P4. Repair the ground

Do **tile-aware replacement**, not blur or generative inpainting:

- find surrounding ground metatiles with compatible tile IDs/palette;
- copy/repeat those tiles into the object footprint;
- preserve dithering and palette;
- add a darkened undergrowth/shadow patch where appropriate.

For objects that overlap, clear only the visible footprint; keep the rest of
the ground intact.

### P5. Generate the 2.5D representation

- foliage: upright camera-facing billboard of the authored cutout + 2-3 depth
  slices, using the actual pixels. Do not synthesize a new crown or stretch the
  source; GBA tree art is already a slightly slanted view with the trunk drawn
  in;
- houses: box extrusion from the footprint/roof rectangle (front/side walls +
  roof plane), not a single flat card;
- rocks/signs: cards or small boxes;
- ground: repaired tilemap plus optional ledge/path relief.

The runtime can still use the existing `mesh.py` card/box primitives.

### P5b. Structures and houses

Houses should not be popped up as one flat card. They need a small extruded
mesh so the roof, front wall, and side wall read correctly from the diorama
camera.

Detection:

- find a rectangular footprint of opaque building tiles;
- classify roof tiles vs wall tiles by palette/color/tile identity;
- find the wall rows (usually the lower part of the footprint);
- find door/window tiles and keep them as front-wall decals;
- use the roof outline for overhang and the footprint for collision.

Geometry:

- **footprint**: axis-aligned rectangle on the ground;
- **walls**: four vertical quads, height = number of wall rows x tile height;
- **front wall**: the actual wall texture, cropped from the source;
- **side walls**: use actual side-wall tiles when present; otherwise extend the
  wall palette/tile pattern along the footprint depth (no invented detail);
- **roof**: a raised quad or shallow pitched roof at wall height, extending
  slightly past the walls for an overhang;
- **door/window**: keep as decals on the front wall; do not cut them out as
  separate objects unless they animate;
- **shadow**: a soft footprint shadow plus a darker strip under the roof
  overhang.

Do not stretch one wall texture around the whole box. Build each face from its
own source region where possible; if a side face has no source art, use the
nearest wall color/tile pattern so the result still matches the GBA palette.

Suggested structure fields:

```text
kind = "house"
footprint            [x0, y0, x1, y1]
wall_height          world units
roof_height          world units
roof_overhang        world units
wall_mask            source mask for front wall
roof_mask            source mask for roof
door/window rects    optional decals
collision            footprint rectangle
```

Occlusion:

- houses are depth-sorted meshes, not billboards;
- the player and NPCs should be able to stand in front of or behind a house
  using normal z-buffer depth;
- the roof must write depth, but the wall/door decals should not z-fight;
- keep the house base anchored at the footprint's front edge, like tree cards.

For multi-tile houses, detect the whole footprint first, then split roof/wall.
Do not detect individual 8x8 tiles as separate houses.

Runtime prototype: `relief.detect_structures()` implements the rectangle
segmentation and eave split; `mesh._add_structures()` builds the walls, two
gable roof slopes, gable ends, overhang, and contact shadow. It currently uses
the facade's edge strips for side/back walls, so dedicated side-wall tiles are
still an open improvement. The compiler can reuse the same detector and store
the resulting textures/geometry in the per-map manifest.

### P6. Pack and store

Suggested cache layout:

```text
cache/
  <rom-sha1>/
    version.txt
    manifest.json
    atlas.ktx2          # or PNG/WebP
    maps/
      <map-id>.bin
    corrections/
      <map-id>.json
```

Key:

```text
(rom_sha1, compiler_version, detector/profile_version)
```

Invalidate the cache when any key component changes. A romhack update gets a
new ROM hash and a new cache.

## Runtime contract

The renderer should not know about Pokémon or about the compiler.

On map load:

1. determine the current map ID:
   - Pokémon: read the map ID from game RAM;
   - generic: match a layer/tilemap signature;
2. look up `maps/<map-id>.bin`;
3. if present, build the 3D scene from the manifest;
4. if missing/unknown/unsupported, use the existing 2D composite fallback.

Lazy-load atlas pages per map or tileset. Do not load the entire game atlas at
startup.

The runtime consumer is the custom GPU 3D pass described in
`docs/EXPERIMENTS.md` (E8), not the offline CPU renderer. The CPU path remains
the reference implementation and test oracle.

Suggested manifest shape:

```json
{
  "rom_sha1": "...",
  "compiler_version": 1,
  "map_id": "Route101",
  "width": 480,
  "height": 320,
  "tilesets": ["gTileset_General", "gTileset_Petalburg"],
  "ground": {"layer": 2, "texture": "maps/Route101-ground.ktx2"},
  "objects": [
    {
      "asset_id": "tree-0001",
      "kind": "tree",
      "layer": 2,
      "bbox": [322, 122, 382, 208],
      "anchor": [352, 207],
      "footprint": [322, 122, 382, 208],
      "height": 86,
      "z": 0,
      "confidence": 0.91
    }
  ]
}
```

## Compile cost

For a Pokémon-sized game (a few hundred map layouts):

| Step | Expected cost |
|---|---|
| Map/tilemap parse + layer render | seconds to a few minutes |
| Tile clustering / components / rectangles | seconds to a few minutes |
| Cutouts + tile-aware ground repair | seconds to a few minutes |
| Atlas packing + manifests | seconds |
| Optional SAM/ML labeling | minutes on GPU; much slower on CPU |
| Runtime load per map | milliseconds |

The classical/tile path should finish in under a few minutes for a whole game.
ML should be optional and limited to ambiguous regions; do not run a large
segmentation model over every screen unless the quality gain is worth it.

## Emulator cache vs saves

These are separate:

- **Battery save (`.sav`)** — portable across emulators.
- **Savestate (`.ss1`/`.state`)** — emulator/version-specific; not a reliable
  cross-emulator format.
- **Compiled map cache** — derived content keyed by ROM hash; independent of
  both saves and savestates.

A stock 2D emulator can ignore the cache entirely. A compatible fork can read
it from a sidecar directory or a user cache directory. The cache must never be
embedded in a savestate.

## Romhack strategy

- Compile per ROM; never assume vanilla offsets or art.
- Keep detector parameters in profiles (HSV ranges, min area, alpha threshold,
  tile-classifier labels).
- Use a generic fallback detector for unknown maps.
- Allow manual corrections to be stored per map and merged into the manifest.
- If a map is not compiled, render 2D; do not fail the game.

## ML guidance

The compiler must work without ML. For Pokémon-style maps, map data and
deterministic tile/component logic are sufficient for the base games and most
romhacks.

Use ML only as an optional offline assistant when deterministic extraction is
ambiguous:

- SAM/SAM 2 for masks;
- Grounding DINO/CLIP for labels;
- a small U-Net/DeepLab/tile classifier for tile roles.

Do not add ML to the runtime and do not require it for the MVP. Avoid
generative inpainting for pixel art; it destroys the palette and dithering.
Tile-aware patch synthesis is faster and looks correct.

## MVP

One map, two object classes:

1. parse the map/metatiles offline;
2. detect tree components and house rectangles;
3. cut out objects and tile-repair the ground;
4. write atlas + manifest;
5. render trees as cards and houses as boxes;
6. load the manifest in the lab renderer;
7. compare against the current auto-detected render.

Acceptance criteria:

- compile completes without playing the game;
- compile and render work with zero ML;
- no runtime ML;
- cache is keyed by ROM hash and version;
- unknown maps fall back to 2D;
- tree cards and house boxes use actual source pixels;
- cache size and compile time are reported;
- tests cover manifest parsing, cache invalidation, and fallback.

## Open questions

- Best way to read the current map ID in the target game/fork.
- Whether to store side-view variants or generate cards at runtime.
- Collision representation for houses/objects.
- Animated objects (flowers, water, weather) and frame storage.
- Atlas format: KTX2 vs PNG/WebP; GPU upload path.
- Manual-correction UI (map editor overlay vs JSON patches).
- Legal policy for any precomputed data shipped with profiles.

## Relationship to current code

Reusable pieces:

- `gba_diorama/dump.py` — layer loading and world adapter;
- `gba_diorama/relief.py` — component detection, cutouts, cards;
- `gba_diorama/mesh.py` — cards, boxes, plinth, sprites;
- `gba_diorama/profiles.py` — per-game/scene configuration;
- `tools/run-mgba-diorama.sh` — local capture workflow.

The compiler can be a separate tool (for example
`tools/prebake_maps.py`) that writes the cache; the runtime only needs a
manifest loader and the existing mesh/render path. No current runtime behavior
needs to change for the optional path to be explored.
