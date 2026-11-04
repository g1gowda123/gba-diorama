# Map-format research: metatiles, BG layers, trees, and houses

**Status:** research note, no runtime changes.  
**Scope:** Pokémon Emerald (pokeemerald) map storage and how it maps to the
GBA BG layers used by the 3D2D renderer. The same model is the basis for the
optional offline map compiler in `docs/OFFLINE-MAP-COMPILER.md`.

This note records ground truth from the ROM data and Porymap/Porytiles, not
guesses from screen-space color thresholds. No ROM art is included.

## Short version

- Overworld maps are grids of **16×16 metatiles**. Each metatile is 2×2
  **8×8 tiles**.
- A metatile has a **layer type**: Normal, Covered, or Split. The layer type
  decides which GBA BG receives each of the two authored tile layers.
- On Route 101, the full trees and ground are on **BG2**. BG1 contains only
  sparse top-layer fringes. There are no Split metatiles on this map.
- On Littleroot Town, houses are split across **BG1 roof trim/overlay** and
  **BG2 walls + roof body**. House extraction must combine both layers.
- Therefore the renderer/compiler should use map/metatile data for layer
  semantics, and use pixel segmentation only to find object extents inside the
  already-correct layer.

## Data model

```text
map.json
  └─ layout id (e.g. LAYOUT_ROUTE101)
       ├─ width, height (in metatiles)
       ├─ primary tileset   (gTileset_General)
       ├─ secondary tileset (gTileset_Petalburg)
       ├─ border.bin
       └─ map.bin           (width*height 16-bit metatile ids)
```

- `map.bin`: one 16-bit metatile ID per block. The low 10 bits are the
  metatile index; bits 10–11 are collision, bits 12–15 are elevation.
- `metatiles.bin`: 16 bytes per metatile = 8 tile entries (2 layers × 4 tiles).
- `metatile_attributes.bin`: 16-bit attributes per metatile in Emerald.
  - bits 0–8: behavior
  - bits 9–11: terrain type
  - bits 12–13: layer type (`0xF000` mask in Porymap)
  - bits 14–15: unused/encounter type depending on project config
- `Metatile::LayerType` in Porymap:
  - `Normal = 0`
  - `Covered = 1`
  - `Split = 2`

## Layer-type → BG mapping

This is the critical table. It comes from Porymap's
`MapBlockBehavior.backgrounds` mapping (Porymap issue #92):

| Layer type | Authored layers | BG mapping | Notes |
|---|---|---|---|
| Normal (`MAP_BLOCK_BG_21`) | middle + top | layer 1 → **BG2**, layer 2 → **BG1** | "Layer 2 goes above NPCs." |
| Covered (`MAP_BLOCK_BG_32`) | bottom + middle | layer 1 → **BG3**, layer 2 → **BG2** | NPCs appear above both layers. |
| Split (`MAP_BLOCK_BG_31`) | bottom + top | layer 1 → **BG3**, layer 2 → **BG1** | Described as seemingly unused in base Emerald. |

Implications:

- A tree canopy can legitimately be on **BG1** (Normal top layer, above
  NPCs) or **BG2** (Normal middle or Covered middle). It is not universally
  one layer.
- A house roof can be split: roof trim/top on BG1, roof body/walls on BG2.
- Romhacks can change layer assignment, so profiles must carry per-layer
  detector hints and a 2D fallback.

## Route 101 ground truth

Source data:

- `LAYOUT_ROUTE101`: 20×20 metatiles = 400 blocks.
- Primary tileset: `gTileset_General` (512 metatiles).
- Secondary tileset: `gTileset_Petalburg` (144 metatiles).
- Blockdata: `data/layouts/Route101/map.bin`, 800 bytes (20×20×2).
- 31 unique metatile IDs on the map.
- Layer-type counts: **386 Normal, 14 Covered, 0 Split**.

Rendered from the actual tilesets and blockdata (not from the emulator
screen):

| Layer | Alpha pixels | Interpretation |
|---|---:|---|
| BG1 | 1,748 | sparse Normal top-layer fringes/grass details |
| BG2 | 100,437 | main ground + full multi-tile tree canopies |
| BG3 | 3,584 | exactly 14 Covered metatiles × 256 pixels |

The 14 Covered metatiles account exactly for the BG3 coverage. The big trees
are therefore **Normal middle-layer art on BG2**, not BG1 canopy art. This
matches the in-game dump, where BG2 carries the full canopies and BG1 has only
small foreground fringes.

Practical conclusion for Route 101: tree detection belongs on **BG2**, using
connected components over the full layer. BG1 billboard extraction is only for
the sparse fringe art and should not be the primary tree source.

## Littleroot houses

Littleroot Town uses the same 20×20 grid (400 blocks, 63 unique metatiles;
376 Normal, 24 Covered, 0 Split).

Rendered layer evidence:

- BG1 contains the **roof trim / top overlay**.
- BG2 contains the **walls and the roof body**.
- The composite is the full house; neither layer alone is the house.

House extraction should therefore:

1. build a combined BG1+BG2 footprint mask;
2. classify the upper/roof region vs the lower/wall region;
3. keep door/window decals from the source art;
4. extrude four wall quads and a roof plane/overhang as a depth-sorted mesh;
5. keep the collision footprint from the metatile grid.

This is the P5b section of `docs/OFFLINE-MAP-COMPILER.md`.

## Runtime map identity

For a Pokémon fork, the runtime map ID path is:

```text
gSaveBlock1Ptr->location.mapGroup
gSaveBlock1Ptr->location.mapNum
  → Overworld_GetMapHeaderByGroupAndId(group, num)
  → gMapHeader.layoutId
  → gSaveBlock1Ptr->mapLayoutId
```

The compiler cache should be keyed by `(rom_sha1, compiler_version,
profile_version)`, and map entries by the game's map/layout ID where possible.
Unknown maps fall back to the current 2D compositor.

## What this changes in the renderer/compiler

- Use metatile attributes and layer type to decide which BG is ground, which
  is canopy/roof, and which is foreground. Do not infer layer roles from HSV
  alone.
- Trees: detect full connected components on the correct world BG (BG2 for
  Route 101). Keep the profile's `detector`, `min_area`, and HSV parameters
  as fallbacks for romhacks.
- Houses: combine BG1+BG2, split roof/wall, and extrude as meshes rather than
  cards.
- Ground repair: prefer tile-aware replacement from surrounding metatiles over
  pixel inpainting; preserve palette/dithering.
- Keep a 2D fallback for unknown layer types, affine/bitmap modes, menus, and
  battles.

## Open questions

- Exact tree metatile IDs and behavior flags for Route 101 (and whether any
  tree metatiles are Covered rather than Normal).
- Exact house metatile IDs/roof-wall boundaries for Littleroot and other
  towns.
- Whether roof/wall layer conventions are consistent across all Emerald maps.
- How often romhacks use Split metatiles; base Emerald appears not to.
- Best runtime source for map ID without hardcoding game-specific RAM offsets
  (symbols, saveblock pointer, or a small per-game profile).
- Border/connection handling when a map extends beyond one 20×20 layout.

## Tools and references

- Porymap: map/tileset editor; source clone used at `/tmp/opencode/porymap`.
  - `include/core/metatile.h` — `Metatile::LayerType`.
  - `src/core/metatile.cpp` — attribute masks, including `0xF000` for Emerald
    layer type.
  - Porymap issue #92 — `MapBlockBehavior.backgrounds` mapping.
- Porytiles: overworld tileset compiler; authoring layers are
  `bottom.png`, `middle.png`, `top.png`, emitted as `metatiles.bin` and
  `metatile_attributes.bin`.
- pokeemerald:
  - `data/layouts/layouts.json`
  - `data/layouts/Route101/map.bin`
  - `data/maps/Route101/map.json`
  - `src/overworld.c` — map header/layout lookup.
- Research artifacts (ephemeral, not committed):
  `/tmp/opencode/pokeemerald-data/`, `/tmp/opencode/render_map.py`,
  `/tmp/opencode/render_littleroot.py`, `/tmp/opencode/map-composite.png`,
  `/tmp/opencode/littleroot-composite.png`.

## Method note

The earlier tree detector was tuned on screen pixels before the map format was
understood. That produced berry-tree-sized components and synthetic trunks.
The correct order is:

1. identify the map/layout/tileset/metatile layer type;
2. render the exact BG layers;
3. detect objects inside the correct layer;
4. only then tune color/shape heuristics for romhack fallback.

This note is the ground truth for the next renderer/compiler iteration.
