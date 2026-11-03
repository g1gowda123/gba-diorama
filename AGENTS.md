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
- `synthetic.py`: 64×64 synthetic overworld, real GBA VRAM/tilemaps, decoded
  layers 1/2/3, heights/materials/sprites.
- `mesh.py`: greedy heightfield top faces, per-cell side walls, two-tier plinth,
  sprite billboards + shadows.
- `raster.py`: CPU z-buffer rasterizer with clipping, perspective UV, alpha
  test/blend, fog.
- `post.py`: bloom, tilt-shift, vignette, grade.
- `pipeline.py` / `__main__.py`: camera + CLI.
- `tests/`: 10 tests covering decode, mesh, render, post, determinism.

## Next tasks (in order)

1. **Dump loader.** Add `gba_diorama/dump.py` that reads the format in
   `docs/MGBA-HANDOFF.md` and returns the same `SyntheticWorld`-like interface
   (layers, present masks, heights, materials, sprites). Keep it independent of
   mGBA headers.
2. **Real layer inspector.** Add `python -m gba_diorama.inspect <dump>` that
   writes `bg0.png`…`bg3.png`, `obj.png`, `window.png`, and a decoded-vs-dumped
   diff. This validates the decoder before 3D work.
3. **Profile system.** Add a data-only TOML profile loader with match rules
   (game code + scene signature). Implement the schema in `docs/DESIGN.md`.
4. **Parallax depth / world atlas.** Implement E1/E2 in
   `docs/EXPERIMENTS.md`; expose a confidence value and fallback.
5. **mGBA dump hook.** Patch `GBAVideoGLRendererFinishFrame()` behind
   `GBA_DIORAMA_DUMP`; capture the layer textures and registers.
6. **mGBA `hd2d` pass.** Port the mesh/camera to GL, keep `_finalizeLayers()` as
   fallback, add resize/deinit hooks.

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
  map slots. This bit the synthetic fixture once.
- If sprites are missing, check `sprite.kind` matches a key in `sprite_tex`.
