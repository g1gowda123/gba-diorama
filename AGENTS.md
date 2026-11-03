# Agent handoff

This repo is an experimental 3D2D renderer lab for GBA-era games. M1 is done:
the mGBA GL layer-dump patch works, a real Emerald Route 101 frame renders
through the offline mesher, and menus/non-text modes fall back to a flat 2D
composite. M2 is in progress: scene signatures and a data-only TOML profile
loader are implemented; automatic profile discovery and the debug overlay are
still open. The next major milestone is the world atlas/parallax depth. Read
`README.md`, `docs/DESIGN.md`, and `docs/MGBA-HANDOFF.md` before changing
architecture.

## Commands

```bash
python -m pytest -q
python -m gba_diorama --out docs/images/demo.png
python -m gba_diorama --dump path/to/dump \
    --out docs/images/emerald-route101.png
python -m gba_diorama --dump path/to/dump --all-layers \
    --out /tmp/all-layers.png
python -m gba_diorama --dump path/to/dump --force-3d \
    --out /tmp/ui-3d.png
python -m gba_diorama --dump path/to/dump \
    --profile profiles/emerald-route101.toml --out /tmp/profile.png
python -m gba_diorama --yaw 18 --width 1920 --height 1080 --out /tmp/yaw18.png

# Launch the patched mGBA GL renderer (desktop GL, 2x, muted)
tools/run-mgba-diorama.sh /path/to/game.gba 4800
touch dumps/<game>/trigger   # capture the current frame on demand
```

Dependencies: Python 3.10+, `numpy`, `pillow`; tests use `pytest`.

## Invariants

1. **No game-specific constants in the core.** `gba.py`, `mesh.py`, `raster.py`,
   and `post.py` must not know Pokémon/Zelda layer roles. Roles belong in
   `profiles.py` / TOML profiles.
2. **`decode_text_bg()` is the emulator boundary.** Keep its signature and
   output shape `(H, W, 4) RGBA uint8` stable.
3. **`Scene.triangles` are world-space.** The renderer must not mutate them;
   `raster.render()` copies to camera space. Keep the regression test.
4. **Synthetic world = regression fixture.** It writes GBA-format tilemaps and
   decodes them through `gba.py`; don't replace it with hand-drawn images.
5. **Keep the 2D fallback.** The 3D path must never be the only way to render a
   scene.
6. **No ROMs/game assets in git.** Create synthetic fixtures instead.

## Current state (M1 + M2 profile loader)

- `gba.py`: 4bpp/8bpp text BG decode, palette, OAM entry decode. Affine/bitmap
  modes are rejected on purpose.
- `dump.py`: loads/exports the mGBA dump format, decodes tilemaps from VRAM,
  and provides a `DumpWorld` adapter. Ground selection scores BG1–BG3 by alpha
  coverage × tile variety, extrudes only layers in front of the ground, and
  crops visible OAM entries from the composited OBJ layer. An explicit
  `SceneProfile` can override the ground, layer roles, heights, and materials.
- `profiles.py`: `SceneSignature`, `ProfileMatch`, `LayerProfile`,
  `SceneProfile`, and `ProfileBook`; TOML profiles match mode/enabled/
  priorities/affine/screen sizes and optional game code.
- `inspect.py`: `python -m gba_diorama.inspect <dump> --out <dir>` writes layer
  PNGs.
- `synthetic.py`: 64×64 synthetic overworld, real GBA VRAM/tilemaps, decoded
  layers 1/2/3, heights/materials/sprites.
- `mesh.py`: greedy heightfield top faces, per-cell side walls, two-tier plinth,
  sprite billboards + shadows; non-square maps keep square pixels.
- `raster.py`: CPU z-buffer rasterizer with clipping, perspective UV, alpha
  test/blend, fog.
- `post.py`: bloom, tilt-shift, vignette, grade.
- `pipeline.py` / `__main__.py`: demo/dump cameras, 2D UI fallback, scene
  signatures, and CLI (`--dump`, `--atlas`, `--all-layers`, `--force-3d`,
  `--profile`).
- `atlas.py`: offline E1 scaffold; stitches BG layers from several dumps at
  their scroll offsets into a larger canvas and records per-layer coverage.
  VRAM invalidation and automatic sequence capture are still missing.
- `parallax.py`: E2 prototype; estimates per-layer scroll ratios and a
  confidence value from a dump sequence. Not yet wired into mesh heights.
- `tools/run-mgba-diorama.sh`: desktop-GL mGBA launcher (2× scale, muted).
- `profiles/emerald-route101.toml`: example profile matching the real Route 101
  signature (BG2 ground, BG3 backdrop ignored, BG1 canopy at a naive constant
  height).
- `tests/`: 37 tests covering decode, dump round-trip, profiles, atlas
  stitching/coverage, parallax, ground compositing, mesh, render, post,
  fallback, determinism.

## Next tasks (in order)

1. **World atlas sequence capture + invalidation.** `atlas.py` stitches
   offline dumps and records coverage, but it needs VRAM-change invalidation
   and an mGBA sequence mode before it can replace the sparse-overlay skip.
   `parallax.py` estimates E2 ratios and `--atlas` prints them; the next step
   is wiring those estimates into overlay heights/profiles with a confidence
   fallback.
2. **Profile discovery/debug UI.** Auto-load a profile directory, show the
   current signature and chosen profile, and add a hotkey/overlay to cycle layer
   roles and heights. Camera overrides (`distance`, `pitch_deg`, `yaw_deg`) are
   already implemented in `SceneProfile.camera`.
3. **mGBA `hd2d` pass.** Port the mesh/camera to GL, keep `_finalizeLayers()` as
   fallback, add resize/deinit hooks.
4. **Real OBJ sprites.** Decode 4bpp/8bpp OBJ tiles from OAM instead of cropping
   the composited OBJ layer.
5. **Height heuristics.** Implement E4: tile classification, canopy vs. building,
   and collision-learned heights.

## Style

- Python 3.10+, type hints on public functions, small dataclasses.
- No new runtime dependencies without a strong reason; numpy + pillow is the
  current budget.
- Keep the software renderer deterministic (fixed seed, no wall-clock time in
  output).
- Docs use the vocabulary "3D2D" for the technical goal, "HD-2D" for the
  presentation style.

## Troubleshooting

- `ModuleNotFoundError: gba_diorama` when running a script outside the repo:
  use `PYTHONPATH=.` or install with `pip install -e .`.
- If a render is all background, check the camera near/far and that
  `Scene.triangles` is non-empty.
- If BG layers look opaque/noisy, check the screen-base spacing. A 64×64 text
  BG needs four 0x800 screen blocks (0x2000 total); bases must be spaced by 4
  map slots, and the 5-bit BGxCNT field maxes out at 31. The synthetic fixture
  puts BG1's map at 20 (0xA000) because 32 is unaddressable.
- If sprites are missing, check `sprite.kind` matches a key in `sprite_tex`, or
  that the dump's `obj.rgba` has visible OAM entries.
- If a real dump renders as a flat checkerboard, the ground picker chose a
  low-detail backdrop. Check each BG's alpha coverage and unique 8×8 tiles, then
  add an explicit `--profile` override.
- If foreground layers become floating boxes, they are sparse detail layers
  being extruded. They are skipped by default in `render_dump`; use
  `--all-layers` only for debugging.
