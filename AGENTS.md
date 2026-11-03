# Agent handoff

This repo is an experimental 3D2D renderer lab for GBA-era games. The current
milestone is an offline synthetic demo; the next milestone is a real mGBA layer
dump. Read `README.md`, `docs/DESIGN.md`, and `docs/MGBA-HANDOFF.md` before
changing architecture.

## Commands

```bash
python -m pytest -q
python -m gba_diorama --out docs/images/demo.png
python -m gba_diorama --yaw 18 --width 1920 --height 1080 --out /tmp/yaw18.png
```

Dependencies: Python 3.10+, `numpy`, `pillow`; tests use `pytest`.

## Invariants

1. **No game-specific constants in the core.** `gba.py`, `mesh.py`, `raster.py`,
   and `post.py` must not know Pokémon/Zelda layer roles. Roles belong in
   profiles (currently documented, not implemented).
2. **`decode_text_bg()` is the emulator boundary.** Keep its signature and
   output shape `(H, W, 4) RGBA uint8` stable.
3. **`Scene.triangles` are world-space.** The renderer must not mutate them;
   `raster.render()` copies to camera space. Keep the regression test.
4. **Synthetic world = regression fixture.** It writes GBA-format tilemaps and
   decodes them through `gba.py`; don't replace it with hand-drawn images.
5. **Keep the 2D fallback.** The 3D path must never be the only way to render a
   scene.
6. **No ROMs/game assets in git.** Create synthetic fixtures instead.

## Current state (M0)

- `gba.py`: 4bpp/8bpp text BG decode, palette, OAM entry decode. Affine/bitmap
  modes are rejected on purpose.
- `dump.py`: loads/exports the mGBA dump format, decodes tilemaps from VRAM,
  and provides a `DumpWorld` adapter.
- `inspect.py`: `python -m gba_diorama.inspect <dump> --out <dir>` writes layer
  PNGs.
- `synthetic.py`: 64×64 synthetic overworld, real GBA VRAM/tilemaps, decoded
  layers 1/2/3, heights/materials/sprites.
- `mesh.py`: greedy heightfield top faces, per-cell side walls, two-tier plinth,
  sprite billboards + shadows.
- `raster.py`: CPU z-buffer rasterizer with clipping, perspective UV, alpha
  test/blend, fog.
- `post.py`: bloom, tilt-shift, vignette, grade.
- `pipeline.py` / `__main__.py`: camera + CLI.
- `tests/`: 15 tests covering decode, dump round-trip, mesh, render, post,
  determinism.

## Next tasks (in order)

1. **Profile system.** Add a data-only TOML profile loader with match rules
   (game code + scene signature). Implement the schema in `docs/DESIGN.md`.
2. **Parallax depth / world atlas.** Implement E1/E2 in
   `docs/EXPERIMENTS.md`; expose a confidence value and fallback. This is what
   turns a 512×512 synthetic map into a real screen-space dump.
3. **mGBA dump hook.** Patch `GBAVideoGLRendererFinishFrame()` behind
   `GBA_DIORAMA_DUMP`; capture the layer textures and registers.
4. **mGBA `hd2d` pass.** Port the mesh/camera to GL, keep `_finalizeLayers()` as
   fallback, add resize/deinit hooks.
5. **Real OBJ sprites.** Decode 4bpp/8bpp OBJ tiles from OAM and replace the
   procedural stand-ins.
6. **Height heuristics.** Implement E4 once real dumps are available.

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
- If sprites are missing, check `sprite.kind` matches a key in `sprite_tex`.
