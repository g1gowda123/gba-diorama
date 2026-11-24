# gen3hd runtime — live, data-driven HD-2D for Gen 3

**Status:** first vertical slice working. Route 101 compiles from a live
`romdev` session and renders in Godot 4.

This is the fresh path. It replaces the old `gba_diorama` screen-space
pipeline (green HSV masks, rectangle detection, atlas stitching, CPU
rasterizer, tabletop plinth) as the primary Gen 3 renderer.

## Pipeline

```
user ROM + battery save
        │
        ▼
romdev (libretro mGBA, local HTTP server)
  loadMedia, input, frame, memory
        │  live VRAM / PALETTE / OAM / IWRAM / EWRAM / IO
        ▼
gen3hd.gen3
  locate.py   structural gBackupMapLayout + gMapHeader finder
  rom.py      ROM header, MapLayout, Tileset, metatile defs/attrs,
              RSE-vs-FRLG family detection
        │
        ▼
gen3hd.world
  full-map BG1/BG2/BG3 composition from the ROM's own tilesets
  semantic grid: metatile / collision / elevation / layer type / behaviour
  object cutouts: repeated 2x2 metatile blocks on solid cells + buildings
  tile-aware ground repair
  OAM sprite billboards
        │  world.json + ground.png + objects/*.png + sprites/*.png
        ▼
viewer3d (Godot 4.7, Forward+)
  heightfield terrain + side walls
  alpha-cutout object cards + contact shadows
  sky, sun/fill light, fog, SSAO, glow, DOF, vignette
```

No savestate and no mGBA patch are required. `romdev` runs the cartridge and
exposes the same PPU/WRAM state the engine has.

## Commands

```bash
# one-time
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -e '.[dev]'

# romdev MCP server (already installed as a systemd user service)
systemctl --user start romdev-mcp

# compile the live map (boots ROM + save, drives the title menu)
.venv/bin/python -m gen3hd capture \
    --rom "Pokemon - Emerald Version (USA, Europe).gba" \
    --save roms/emerald-test/emerald.sav \
    --session gen3hd-live --boot \
    --out out/gen3hd-world

# render a still
godot --path viewer3d -- \
    --world="$PWD/out/gen3hd-world" \
    --shot="$PWD/out/gen3hd-route101.png"
```

Attach to an already-running session by dropping `--boot` and matching
`--session`.

## Romhack and tileset support

The compiler reads everything from the ROM the user supplied:

- map layout dimensions, map blockdata, collision/elevation bits;
- primary and secondary tileset pointers;
- 8 x u16 metatile definitions and the metatile attribute array;
- layer type per metatile, which assigns its bottom/top 2x2 tiles to
  BG1/BG2/BG3 exactly like `field_camera.c::DrawMetatile()`;
- behaviour IDs for ledges, water, tall grass, etc.

There are no vanilla tile IDs, map IDs, or EWRAM/IWRAM addresses in the
compiler. A hack with custom maps, custom tilesets, and moved data still
compiles as long as it keeps the Gen 3 engine structures.

### RSE vs FRLG

`gen3hd.gen3.rom` detects the family structurally and uses the header code as a
tie-breaker:

| | RSE | FRLG |
|---|---|---|
| Tileset attribute pointer | `+0x10` | `+0x14` |
| Metatile attribute width | `u16` | `u32` |
| Layer type bits | 12–15 | 29–30 |
| Behaviour bits | 0–7 | 0–8 |

Hacks based on Ruby/Sapphire/Emerald or FireRed/LeafGreen inherit the matching
format. A hack with an unrecognised game code is still parsed by structural
scoring of its unique metatiles.

### What "fangame" means here

- **GBA romhacks / engine hacks** (binary patches, new maps/tilesets, custom
  scripts): supported by this path, because the ROM still contains Gen 3
  structures and the game runs in mGBA.
- **RPG Maker XP / Pokémon Essentials / Godot / Unity fangames**: not GBA
  ROMs. They need a separate importer; this runtime cannot boot them.

## Current limitations

- The Godot viewer renders a **compiled snapshot**, not the live emulator
  window yet. The next step is a stream loop: `romdev` frame → updated
  sprites/OAM → Godot.
- The camera is currently **orthographic and fits the whole map**, so the
  result reads as a complete floating diorama rather than a full-bleed
  in-game camera. A perspective gameplay camera that follows the player is
  the next framing pass.
- Object discovery is tuned on Route 101: repeated 2x2 tree cards, 1x1 props,
  and large rectangular buildings. Complex multi-part props and irregular
  buildings need more template classes.
- Buildings are detected but not yet meshed as walls + gable roofs in the
  Godot viewer; they currently render as cards.
- Ledges are flattened rather than extruded as steps.
- Sprites use OAM screen position + BG2 scroll. Parsing `gObjectEvents` would
  give authoritative world coordinates and animation state.
- Water is not animated and has no separate surface material.
- Runtime map identity / neighbor connections are not used yet.

## Why this replaces the old path

The old pipeline only saw a 240x160 frame (or a stitched atlas), guessed trees
by a green HSV range, guessed buildings by large rectangles, and rendered a
floating slab. Gen 3 already tells us the map, collision, elevation, layer
type, behaviour, tilesets, and sprite table. The new path uses that data; the
old `gba_diorama` code remains as a generic fallback for non-Pokémon dumps.
