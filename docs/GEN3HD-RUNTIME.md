# gen3hd runtime — live, data-driven HD-2D for Gen 3

**Status:** vertical slice working, presentation pass landed. Route 101 and
Littleroot Town compile from a live `romdev` session and render in Godot 4 as a
lit floating diorama — baked silhouette shadows, meshed houses, door light
pools, a cliff cross-section, particles and a deep stage gradient.

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
  per-pixel alpha keying and ground repair
  baked contact AO + projected silhouette shadows
  OAM sprite billboards
        │  world.json + ground.png + composite.png + bg1/2/3.png
        │  objects/*.png + sprites/*.png + ao.png + shadow_ground.png
        ▼
viewer3d (Godot 4.7, Forward+)
  fixed raised perspective camera; stage gradient instead of a flat void
  palette-true lit terrain with baked AO + cast silhouette shadows
  upright alpha-cutout cards with sun-side rim and base contact shade
  meshed houses by default (card mode available for arbitrary hacks)
  earthen cliff, grass fringe, vines and floating shards
  door light pools and warm point lights after dark
  drifting motes; optional DOF; edge-only tilt-shift; vignette
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

# render a still (golden hour, classic camera, no DOF)
godot --path viewer3d -- \
    --world="$PWD/out/gen3hd-world" \
    --shot="$PWD/out/gen3hd-route101.png" \
    --size=1600x900 --camera=classic --no-dof

# whole-map orthographic view
godot --path viewer3d -- \
    --world="$PWD/out/gen3hd-world" \
    --shot="$PWD/out/gen3hd-route101-map.png" \
    --size=1600x900 --camera=map --no-dof

# town capture (boots Route 101, walks south, saves a reusable state)
.venv/bin/python tools/littleroot_capture.py \
    --rom "Pokemon - Emerald Version (USA, Europe).gba" \
    --save roms/emerald-test/emerald.sav \
    --out out/gen3hd-littleroot --state out/littleroot.ss1

# town at dusk with meshed houses
godot --path viewer3d -- \
    --world="$PWD/out/gen3hd-littleroot" \
    --shot="$PWD/out/littleroot.png" \
    --size=1600x900 --camera=classic --buildings=mesh --no-dof

# night pass (window/door lights, cool grade, fireflies)
godot --path viewer3d -- \
    --world="$PWD/out/gen3hd-littleroot" \
    --shot="$PWD/out/littleroot-night.png" \
    --size=1600x900 --camera=classic --buildings=mesh --time=night --no-dof

# camera-sway turntable: 90 frames spanning 14 degrees, for a preview loop
godot --path viewer3d -- \
    --world="$PWD/out/gen3hd-world" \
    --size=800x450 --camera=classic --no-dof \
    --turntable=90 --turntable-dir="$PWD/out/preview-frames" --turntable-arc=14
ffmpeg -framerate 30 -i out/preview-frames/frame_%04d.png \
    -c:v libx264 -pix_fmt yuv420p -crf 18 docs/images/gen3hd-preview.mp4
# or one shot: tools/render-preview.sh out/gen3hd-world

# re-bake AO/shadows after tuning, without a live session
.venv/bin/python tools/rebake_presentation.py out/gen3hd-world out/gen3hd-littleroot

# regenerate the README images from captured worlds
tools/render-showcase.sh
```

Attach to an already-running session by dropping `--boot` and matching
`--session`.

### Screens need flat mode

A battle, menu or dialogue frame is a screen, not a place: lifting its tiles
produces floating UI boxes and a one-tile floor. `--flat` keeps the emulator's
own frame as one card, so BG0 (text boxes, menus) and OAM sprites are all in
the picture:

```bash
.venv/bin/python -m gen3hd capture --rom game.gba --out out/screen \
    --session gen3hd-live --flat
godot --path viewer3d -- --world="$PWD/out/screen" --shot=/tmp/screen.png
```

The card sits on the same rim, soil and underside as a terrain world, and
`world.json` records `"mode": "flat"` so nothing tries to re-lift it.

`--auto-flat` guesses the mode from BG0 coverage (a battle, menu or dialogue
draws its boxes there) and `world.json` records that number as `ui_coverage`.
It is a guess, not a rule: some overworld maps leave a BG0 layer enabled too,
so flat stays manual by default and the capture prints `mode=`/`ui=` every
time.

### Live link

A compiled world is static for one map; the camera scroll and the OAM sprite
table are what change. `gen3hd live` publishes those to `live.json` beside
`world.json` and the viewer polls it, so sprites walk on the diorama while the
emulator runs.

```bash
# publish at 10 Hz, 6 emulator frames per update, walking west
.venv/bin/python -m gen3hd live \
    --rom "Pokemon - Emerald Version (USA, Europe).gba" \
    --world="$PWD/out/gen3hd-world" --interval 0.1 --frames 6 --walk west

# view the live world with a camera that follows the player
godot --path viewer3d -- --world="$PWD/out/gen3hd-world" \
    --live="$PWD/out/gen3hd-world/live.json" --camera=play --follow
```

The link reads only OAM and the I/O registers per tick (two host calls), which
is what makes a usable rate possible: re-reading VRAM and re-cropping every
sprite each frame is far too slow and is not done. Sprite *textures* therefore
stay those of the captured frame, and only OAM slots present in `world.json`
are moved (a stale OAM entry cannot be proven empty without decoding its
tiles). `--walk` is a convenience for demos; without it the link steps frames
and an interactive session or playtest window drives the game.

### Settings

Every presentation switch lives in `viewer3d/settings.json`; a user disables a
feature by editing that file, not by remembering flags. Command-line flags are
applied after the file, so a one-off render can override it:

```bash
# shadows off, lanterns off, rim detail off, for a flat reference render
godot --path viewer3d -- --world="$PWD/out/gen3hd-world" \
    --shot=/tmp/flat.png --settings=my-settings.json --no-shadows
```

```json
{
  "camera": { "mode": "classic" },
  "time": "golden",
  "grade": "cinematic",
  "buildings": "mesh",
  "render": { "dof": true, "tilt_shift": true, "vignette": true, "bloom": true,
              "ssao": true, "fog": true, "particles": true, "dither": true,
              "sway": true, "letterbox": false },
  "shadows": { "cast": true, "contact": true, "baked": true, "strength": -1.0 },
  "island": { "cliff_detail": true, "fringe": true, "vines": true, "rocks": true,
              "shards": true, "rim_lip": true, "underside": true, "glow": true },
  "lights": { "celestial": true, "door": true, "windows": true, "lanterns": true },
  "live": { "path": "", "follow": false }
}
```

Allowed values: `camera.mode` is `classic`, `hero`, `play` or `map`; `time` is
`golden`, `day`, `dusk` or `night`; `grade` is `cinematic` or `none`;
`buildings` is `mesh` or `card`.

The three shadow paths are independent: `cast` is the real-time shadow map,
`contact` the soft quads under cards and sprites, `baked` the pre-baked ground
overlay (`strength` < 0 keeps the time preset's value). Unknown keys are
ignored and a missing key falls back to the built-in default, so an older file
keeps working after a switch is added.

### Viewer flags

| Flag | Meaning |
|---|---|
| `--world=<dir>` | exported world directory containing `world.json` |
| `--shot=<png>` | render one still offscreen through a SubViewport and exit |
| `--size=WxH` | offscreen render size (stills bypass the tiled window) |
| `--camera=classic` | whole-map raised perspective on a stage (default) |
| `--camera=hero` | low scenic camera near the map centre |
| `--camera=play` | low gameplay camera around the player |
| `--camera=map` | orthographic whole-map inspection view |
| `--buildings=mesh` | extrude detected houses (default) |
| `--buildings=card` | keep building facades as exact cutouts |
| `--time=golden` | golden-hour grade (default) |
| `--time=day` | brighter grade with weaker shadows |
| `--time=dusk` | warm sunset grade |
| `--time=night` | cool moonlight grade with door/window light and fireflies |
| `--no-dof` | disable the subtle far blur |
| `--no-tilt-shift` | disable the edge-only miniature blur |
| `--no-cliff-detail` | hide the grass fringe, vines, rocks and shards |
| `--no-particles` | hide drifting motes |
| `--no-shadows` | disable real-time shadows (baked ground overlays remain) |
| `--live=<json>` | poll a `gen3hd live` feed and move the sprite billboards |
| `--follow` | keep the `play`/`hero` camera on the live player sprite |
| `--settings=<json>` | settings file (default `viewer3d/settings.json`) |
| `--no-contact-shadows` | drop the soft quads under cards and sprites |
| `--no-baked-shadows` | drop the pre-baked ground shadow overlay |
| `--no-vignette` | drop the corner vignette |
| `--no-fringe` / `--no-vines` / `--no-rocks` / `--no-shards` | island rim detail |
| `--no-rim-lip` | drop the soil lip along the top edge |
| `--no-underside` | drop the jagged rock skirt under the slab |
| `--no-island-glow` | drop the cool pool under the slab |
| `--no-celestial` | hide the sun/moon disc |
| `--no-door-light` / `--no-window-lights` / `--no-lanterns` | dusk/night light sources |
| `--focus=x,z` | focus override in map tiles |
| `--pitch=`, `--yaw=`, `--distance=` | camera overrides |
| `--turntable=N` | render N frames while the camera sways, then exit |
| `--turntable-dir=<dir>` | output directory for turntable PNGs |
| `--turntable-arc=<deg>` | total sway angle in degrees (default 12) |
| `--letterbox` | add cinematic bars |
| `--no-dither` / `--force-dither` | player-behind-object dither control |

## Presentation system

### Terrain

The ground is the ROM's own composited tilemap. Two overlays are baked by
`gen3hd.world` and sampled by `shaders/terrain.gdshader`:

- `ao.png` — soft contact occlusion under every card footprint;
- `shadow_ground.png` — the card's alpha silhouette projected along the sun
  direction by its height, so a tree or house casts a readable shadow in the
  map's pixel grid.

The shader multiplies the authored palette by a cool shadow tint, adds a
warm-to-cool light sweep across the map, and a very small low-frequency value
noise break-up. A directional sun and cool camera-side fill supply real-time
shadows for meshes; the authored albedo remains the dominant colour.

Ledge behaviors (`MB_JUMP_*`) get a low 0.4-unit step and a soft contact strip
on the jump-down side, so a flat route reads as a walkable ledge instead of a
painted line. The step is presentation-only; collision is unchanged.

### Cards

Every tree, bush, flower and sprite is an upright alpha-cutout card. The card
shader adds only:

- a one-texel sun-side rim sampled from the alpha edge;
- a horizontal sun-to-shade gradient;
- a base contact darkening;
- an optional tiny wind sway (interactive only; stills are static).

Cards are deterministically varied in scale, tint and horizontal flip so a
treeline does not read as one repeated stamp.

### Houses

`annotate_buildings()` splits each facade at the strongest row-colour break
(the eave), detects gable versus flat roofs, and assigns depth. The viewer then
builds:

- a front facade from the authored wall band (transparent roof corners filled
  with each row's wall colour);
- textured side walls and a dark back wall;
- a pitched roof from the authored roof band, with an overhang and ridge;
- gable ends and a foundation strip;
- a door light pool and a warm point light after dark;
- warm window glows after dark: `detect_windows()` finds compact blue-glass
  components in the authored wall band, so lit windows are structural rather
  than hardcoded;
- contact and real-time shadows.

Arbitrary romhack buildings still work because the eave, width, height and
roof type come from the card texture, not from content IDs. `--buildings=card`
is the escape hatch for structures that do not look like houses.

### Floating island

The map edge is a cut-away diorama, not an infinite plane:

- the terrain side walls fall 1.85 units through a grass lip, soil strata and
  dark rock;
- a procedural grass fringe, hanging vines and embedded rocks break the cut
  face;
- a few detached rock shards float below the slab;
- a soft additive pool under the island and a deep vertical stage gradient
  replace the flat black void. The void is never tiled with a generated field.

The default `classic` framing includes the whole cliff, so the island reads as
a complete object rather than a slab cropped at the screen edge. A soft
sun/moon disc with a halo sits in the stage sky, camera-attached and behind the
island, so the diorama has a light source without becoming a landscape.

### Post

- Linear tonemapping, exposure 1.0, glow only above 1.05 so door lights and
  motes bloom while the palette stays stable.
- Subtle SSAO, exponential depth fog, gentle vignette.
- Edge-only tilt-shift in the classic camera: the centre stays texel-crisp.
- `--dof` adds a far-plane blur for the low gameplay cameras; off by default.

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

## Player-behind-object dither

When the player card is behind a nearer tree/building card, the viewer draws a
screen-door dither ghost of the player over the occluder, with a one-texel dark
rim. It is toggleable:

- `T` in the running viewer toggles the effect.
- `--no-dither` starts with it off.
- `--force-dither` forces the ghost on for stills.

Occlusion is a screen-space test against card rectangles; transparent gaps in a
card can still trigger it. It is intentionally limited to the player sprite for
now.

## Current limitations

- The Godot viewer renders a **compiled snapshot**; the live link moves only
  OAM sprites and the camera. A map change (new layout/tileset) still needs a
  new `capture`, and sprite textures stay frozen at the captured frame until
  the link can take a tile stream.
- The default `classic` camera is a fixed raised perspective framing the whole
  island and its cliff. `--camera=map` is an inspection view; `--camera=hero`
  and `--camera=play` are scenic/gameplay cameras that crop the map.
- Buildings default to the house mesher. `--buildings=card` keeps exact
  cutouts for romhack structures that are not house-shaped.
- Colour is deliberately palette-true: terrain and cards use the authored GBA
  albedo, with lighting and shadows layered on top. There is no ACES tonemap
  or global tint; the grade modes only change the sun/ambient/backdrop.
- Baked silhouette shadows follow one fixed sun direction (left to right, 38°
  elevation). Changing the sun angle would require re-baking
  `shadow_ground.png`; `tools/rebake_presentation.py` does this from an export.
- Isolated elevation sentinels (often 15) are flattened to the neighbouring
  median. Bridges spanning different elevations should be exempted.
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
