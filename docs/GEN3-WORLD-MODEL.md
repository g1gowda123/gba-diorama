# Gen 3 world model — the next architecture

**Status:** investigation complete, implementation not started.
**Date:** 2026-11-24.
**Probe:** `tools/probe_gen3_state.py` (validated against
`roms/emerald-test/emerald.ss1` + the local Emerald ROM).

This document records why the current screen-space/heuristic pipeline is the
wrong foundation for the stated goal ("every GBA Pokémon game/romhack looks at
least as good as Black 2's semi-3D"), and the data-driven replacement that the
engine already makes possible.

---

## 1. TL;DR

Stop treating the flattened 240×160 frame (or a 256×256 screen crop) as the
world and guessing trees by colour. A Gen 3 Pokémon savestate gives us the
**entire map grid** — metatile ID, collision, elevation — plus VRAM, palette,
OAM, and both work RAMs. The ROM gives the **tileset/metatile definitions** and
**per-metatile attributes** (behavior, layer type, terrain in FRLG). That is a
semantic world model: terrain, water, ledges, tall grass, solid props, buildings,
and sprite positions are all explicit.

The new pipeline should be:

```
mGBA savestate (or dump)          ROM (user-owned)
  VRAM/PALETTE/OAM/IO/WRAM   +   MapLayout/Tileset/metatile defs/attrs
                 \               /
                  Gen3 world compiler
                         |
             WorldModel (whole map, semantic layers)
                         |
             3D mesher + camera + post
                         |
                  screenshot / mGBA GL pass
```

The existing pixel heuristics (`relief.py` green HSV, `detect_structures`
rectangle fill, inpainting, atlas stitching) become a **fallback** for unknown
games/scenes, not the main path.

---

## 2. What is brittle today

- `dump.py::DumpWorld.from_dump()` only sees a **screen-sized** texture. Even
  when VRAM is present it prefers `bg*.rgba` (the 240×160 composite) over the
  full tilemap. The dump then needs an atlas to recover off-screen pixels.
- Trees are found by a green HSV range, then stood up as cards and the ground is
  inpainted. A romhack with autumn/snow/alien palettes, green roofs, or
  non-green trees breaks it. `profiles/` can tune thresholds, but that does not
  generalize.
- Buildings are found by "large rectangle far from the dominant colour", then
  split at an eave. Water, fences, signs, rocks, stairs, and multi-tile props
  have no robust path.
- The camera frames a 240×160/256×256 slab, so the result is a tabletop diorama
  floating in space with a stone plinth. Black 2's look is a **full-bleed low
  camera over the actual map**, not a miniature.
- Atlas stitching, parallax confidence, and VRAM-hash invalidation are solving
  a problem created by throwing away the full-map data in the first place.

Do not spend more effort tuning those heuristics. Keep them only as fallback.

---

## 3. What the engine already tells us

From pret/pokeemerald and pret/pokefirered:

- **Map grid** (`include/global.fieldmap.h`): each 16-bit cell is
  `metatile_id:10 | collision:2 | elevation:4`. `MAP_OFFSET = 7`; the live grid
  is `gBackupMapLayout` / `VMap` (width/height include a 7-metatile border).
- **Layer type** per metatile (`METATILE_LAYER_TYPE_*`):
  - `NORMAL`: bottom 2×2 tiles → BG2, top 2×2 → BG1.
  - `COVERED`: bottom → BG3, top → BG2.
  - `SPLIT`: bottom → BG3, top → BG1.
  This is exactly `field_camera.c::DrawMetatile()`. It tells us which layer owns
  which pixels, so we never have to guess what "BG1" means.
- **Behavior** per metatile (`constants/metatile_behaviors.h`): tall grass,
  long grass, pond/deep/ocean water, waterfall, sand, ice, ash, ledges/jumps,
  impassable directions, stairs, doors, currents, etc. This is the semantic
  classification that colour heuristics were trying to approximate.
- **Elevation** per cell. In the example map almost everything is 3 (default
  ground), with 0 for the border; 15 means multi-level. Water is usually 0/1.
- **Tileset/metatile definitions** in ROM: 8 × u16 per metatile (bottom 2×2 +
  top 2×2); each entry is `tile:10 | hflip:1 | vflip:1 | palette_bank:4`.
  The current map's tile graphics are already loaded in VRAM, so no LZ77
  decompression is needed offline.
- **Sprite data**: OAM entries reference tiles in VRAM and palettes; DISPCNT
  selects 1D/2D mapping and the OBJ char base. Decoding per-sprite art from OAM
  is both more correct and more robust than cropping the composited OBJ layer.
  (The probe already decodes 4 sprites from the example state.)

### Per-game format differences (important)

| | Emerald / Ruby / Sapphire | FireRed / LeafGreen |
|---|---|---|
| `struct Tileset` field order | `... metatiles @0x0C, metatileAttributes @0x10, callback @0x14` | `... metatiles @0x0C, callback @0x10, metatileAttributes @0x14` |
| Attribute size | `u16` | `u32` |
| Behavior bits | 0–7 | 0–8 |
| Terrain type | — | bits 9–13 |
| Layer type | bits 12–15 | bits 29–30 |
| `struct MapLayout` | 0x18 bytes | 0x1A bytes (+ borderWidth/Height @0x18/0x19) |
| Backup map struct | `{s32 width; s32 height; u16 *map;}` | `{s32 Xsize; s32 Ysize; u16 *map;}` |

Ruby/Sapphire should be verified against pret/pokeruby, but they use the same
16-bit attribute model as Emerald. Romhacks inherit the base engine; a hack can
change maps/tilesets without changing these structures, which is exactly why
this path generalizes.

### Locating the data without hardcoded addresses

The probe does this structurally:

1. Scan IWRAM for `{width, height, u16 *map}` where `map` points into EWRAM,
   `width*height <= 10240`, and >98% of the pointed-to cells unpack to
   `metatile_id < 1024`. This finds `gBackupMapLayout`/`VMap`.
2. Scan EWRAM for a ROM pointer whose `MapLayout` dimensions equal
   `(backup_width - 15, backup_height - 14)` and whose ROM map data matches the
   backup interior at the 7,7 offset with ≥98% equality. This finds
   `gMapHeader.mapLayout` and rejects other maps with the same dimensions.
3. Parse the two tilesets, metatile definitions, and attribute arrays.

No symbols or fixed EWRAM addresses are required. The same code can run inside
mGBA, where the ROM and both work RAMs are already in memory.

---

## 4. Verified extraction from this repo's savestate

`roms/emerald-test/emerald.ss1` is an mGBA PNG savestate (480×320 thumbnail)
with a zlib-compressed `gbAs` chunk of 397,312 bytes. Decoded offsets match
mGBA's `GBASerializedState`:

- `0x00400–0x007FF` I/O
- `0x00800–0x00BFF` palette
- `0x00C00–0x00FFF` OAM
- `0x01000–0x18FFF` VRAM
- `0x19000–0x20FFF` IWRAM
- `0x21000–0x60FFF` EWRAM

Example state (`frame 1927`, `POKEMON EMER`, `BPEE`):

- DISPCNT `0x7F60` (mode 0, all BGs + OBJ, 1D OBJ mapping)
- BG1CNT `0x1D41`, BG2CNT `0x1C42`, BG3CNT `0x1E43`; scroll `(0, 40)`
- `gBackupMapLayout` at **IWRAM 0x5DC0**: width 35, height 34, map pointer
  `0x02032318`; interior map is **20×20 metatiles** at +7,+7.
- `gMapHeader` at **EWRAM 0x37318**; `mapLayout = 0x083EBC64`
  (ROM offset `0x3EBC64`), 20×20; map data `0x083EB944`; primary tileset
  `0x083DF704`; secondary tileset `0x083DF71C`.
- 31 unique metatiles; collision `0:237, 1:163`; elevation `0:162, 3:238`;
  layer type `NORMAL:386, COVERED:14`; behaviors `0:296, 2:91 (tall grass),
  59:12 (MB_JUMP_SOUTH), 62:1`.
- 4 OAM sprites decoded (player + NPC + effects); full map is 320×320 px and
  matches the live frame's layout.

Reproduce:

```bash
python tools/probe_gen3_state.py \
    --state roms/emerald-test/emerald.ss1 \
    --rom "Pokemon - Emerald Version (USA, Europe).gba" \
    --out /tmp/gen3-probe
```

Outputs full-map `bg1.png`/`bg2.png`/`bg3.png` at 16 px/metatile, `sprites.png`,
and `summary.json`.

---

## 5. Proposed module layout

Keep the existing core rules intact (`decode_text_bg`, world-space triangles,
deterministic output, no assets in git). Add:

```
gba_diorama/
  state.py         PpuState: vram/palette/oam/io/iwram/ewram + dump adapter
  savestate.py     mGBA PNG savestate reader (and, later, VBA/mGBA raw variants)
  gen3/
    __init__.py
    rom.py         ROM header, MapLayout, Tileset, metatile defs/attrs,
                   per-game format adapter (RSE vs FRLG vs unknown)
    locate.py      structural gBackupMapLayout + gMapHeader finder
    world.py       WorldModel builder: full-map layers, per-cell semantics,
                   primitives (terrain/water/ledge/grass/objects), sprites
    obj.py         OAM decode + grouping into sprite billboards
  mesh3d.py        WorldModel -> triangles (or extend mesh.py)
  pipeline.py      add --savestate/--rom paths; keep --dump fallback
```

`gen3/` is allowed to know Pokémon Gen 3 formats; that is the point. The core
`gba.py`, `raster.py`, `post.py` stay game-agnostic. The old `DumpWorld` path
stays as the generic fallback.

### WorldModel (sketch)

```python
@dataclass
class WorldModel:
    width_px: int
    height_px: int                    # 16 * map metatiles
    ground: np.ndarray                # RGBA, middle/bottom composite
    top: np.ndarray                   # RGBA, top-layer art
    overview: np.ndarray              # full composite for reference
    collision: np.ndarray             # (H, W) uint8 per metatile
    elevation: np.ndarray             # (H, W) uint8
    layer_type: np.ndarray            # (H, W) uint8
    behavior: np.ndarray              # (H, W) uint8
    metatile_id: np.ndarray           # (H, W) uint16
    sprites: list[SpriteBillboard]    # texture, world x/z, size, priority
    camera_focus: tuple[float, float] # player/camera world position
    game: str                         # "emerald"/"frlg"/...
```

With this, the mesh builder never looks at colours:

- **Terrain**: one quad per metatile (or merged runs) using its bottom/top art.
  Height = `elevation - 3` scaled, plus water/ledge rules.
- **Water**: behavior in the water set → lower flat surface, optional animated
  UV offset, no collision walls.
- **Ledges**: jump behaviors → a 1-metatile step with side walls and a drop.
- **Tall/long grass**: low cutout cards or a shader overlay; never a wall.
- **Objects**: connected components of `collision == 1`:
  - 1×1..2×2 with transparent top art → tree/rock/sign/prop billboard;
  - large filled rectangle → building walls + gable roof;
  - thin 1-cell-wide runs → fence/rail;
  - impassable-direction behaviors → cliff/ledge sides.
  The component's top art is the cutout; the collision footprint is the ground
  contact/shadow. No colour thresholds, no inpainting.
- **Sprites**: OAM billboards at screen→world position (or `gObjectEvents`
  coords if we parse it), grounded at the sprite's feet, with contact shadows.
- **Buildings**: use the top-layer art for facade/roof split at the detected
  eave, but the *footprint* comes from collision, not a colour blob.

### Camera / presentation

- Frame the whole current map (or a gameplay-sized window around the player)
  with a low pitch (~18–28°) and a distance that fills the viewport.
- No stone plinth for real maps; add a soft vignette/fog fade at map edges, or
  draw the neighbor connection if the ROM has one.
- Depth fog and a warm key light + cool ambient; contact shadows under every
  object; slight AO band where collision meets ground.
- Keep nearest-neighbour texels, integer-ish texel density, restrained bloom.

---

## 6. Implementation order

1. **Data layer** (no visual change): `state.py`, `savestate.py`, `gen3/rom.py`,
   `gen3/locate.py`, `gen3/world.py`; expose `--savestate`/`--rom`; write the
   full-map layers. This is the probe, promoted into the package.
2. **First 3D render**: full-map terrain + water + ledges, camera framing the
   map, sprites from OAM, no object lifting yet. Verify against the 2D composite.
3. **Collision objects**: connected components, tree/prop billboards, building
   extrusion, fences, tall grass. Delete the green/rectangle path from the
   default Gen 3 route (keep as fallback).
4. **Presentation**: shadows, AO, water animation, lighting, camera polish,
   final output at 1080p.
5. **Runtime**: port the same world compiler into the mGBA GL pass; keep the
   existing finalize as fallback. Build the mesh only when map/tileset state
   changes.
6. **Connections/atlas**: if the player is near a map edge, parse
   `MapConnections` and compose neighbor maps; this replaces screen stitching.

### Acceptance criteria

- The whole map renders from one savestate with no missing edges.
- No green/rectangle constants in the Gen 3 path; classification comes from
  collision/elevation/layer type/behavior.
- On Route 101: trees stand at collision cells, the ledge reads as a step, tall
  grass is low, the player/NPC stand on the correct cells with shadows.
- Screenshot at 1280×720 is visually comparable to a low-angle Black 2
  overworld, not a tabletop slab.
- `--dump` without ROM still uses the generic path and still works.

---

## 7. Open questions / verify next

- Verify Ruby/Sapphire `Tileset` and attribute layout against pret/pokeruby.
- FRLG adapter: 32-bit attributes and `MapLayout` +0x18 border fields. Add a
  game-family detector from `game_code` plus structural validation.
- Object grouping from OAM: player/NPC sprites can be multiple OAM entries.
  Either merge overlapping entries by proximity or parse `gObjectEvents`
  (64 × 0x24 bytes) for authoritative world coordinates.
- Animated tiles (water) use the current VRAM frame; a sequence or timer-based
  frame set is needed for animation. For a still render the current frame is
  fine.
- Map connections: parse `MapConnections` so the camera can leave a map without
  a hard edge.
- The probe's full-map composition should get a pixel regression against the
  live VRAM window (the current evidence is a visual match; the earlier
  scratch alignment comparison was inconclusive because the VRAM window is a
  ring buffer and may include border cells).
- Do not commit ROMs, saves, or extracted art. The probe reads user-provided
  files only.
