# mGBA handoff

This is the concrete integration plan for turning the offline lab into an mGBA
feature. It assumes the mGBA source tree at commit `39a3d93` ("OpenGL: Add float
framebuffer support option for shaders"), which is the revision the initial
research was done against. Line numbers will drift; function names are the
stable anchors.

**Status:** the Phase 1 dump hook is implemented in
`patches/mgba-diorama-dump.patch` and has been built against mGBA Qt at
`39a3d93`. It writes real Emerald frames that `gba_diorama.dump` can load.

## 1. Why mGBA

mGBA's OpenGL renderer already produces exactly the inputs a 3D2D pass needs:

- `renderer->bg[0..3].tex` — one high-resolution texture per background layer
- `renderer->layers[GBA_GL_TEX_OBJ_COLOR]` — OBJ color
- `renderer->layers[GBA_GL_TEX_OBJ_FLAGS]` — per-pixel OBJ flags
- `renderer->layers[GBA_GL_TEX_OBJ_DEPTH]` — per-pixel OBJ depth
- `renderer->layers[GBA_GL_TEX_WINDOW]` — window mask
- `renderer->layers[GBA_GL_TEX_BACKDROP]` — backdrop/priority info
- `renderer->paletteTex` — palette
- `renderer->vramTex` — VRAM
- `renderer->sprites[128]` — decoded OAM/sprite state
- `renderer->dispcnt`, `blda/bldb/bldy`, `mosaic`, window registers

The finalize shader already composites those layers with GBA priority, windows,
alpha blending, and brightness. The 3D pass replaces/augments only the final
composite; it does not need to rewrite the PPU.

## 2. Exact hook points

| What | Where | Notes |
|---|---|---|
| Per-frame finish | `GBAVideoGLRendererFinishFrame()` at `src/gba/renderers/gl.c:1573` | Calls `_drawScanlines()` then `_finalizeLayers()` |
| 2D composite | `_finalizeLayers()` at `src/gba/renderers/gl.c:1696` | The main replacement/augmentation point |
| Finalize shader | `static const char* const _finalize` at `gl.c:572` | Samples `layers[5]` (BG0–BG3 + OBJ), flags, window, backdrop, palette |
| Framebuffer setup | `_initFramebuffers()` at `gl.c:752` | Per-BG FBOs/textures at `240*scale × 160*scale` |
| Scale/resize | `GBAVideoGLRendererSetScale()` at `gl.c:2149` | Reallocate 3D FBOs here |
| Sprite draw | `GBAVideoGLRendererDrawSprite()` near `gl.c:1765` | Uses `renderer->sprites[]` |
| Renderer struct | `include/mgba/internal/gba/renderers/gl.h` | `bg[4]`, `layers[...]`, `sprites[128]`, `scale` |
| BG struct | `gl.h` | `fbo`, `tex`, `index`, `priority`, `charBase`, `screenBase`, `size`, `x`, `y`, `affine` |
| Sprite struct | `include/mgba/internal/gba/renderers/common.h` | `{ struct GBAObj obj; int16_t y, endY, cycles; int8_t index; }` |
| Game identity | `include/mgba/core/interface.h:35` | `mGameInfo { title[17], system[4], code[5], maker[3], version }` |
| Scale config | `src/gba/core.c:448-470`, `:752`, `:1658` | `videoScale` 1–16; Qt spinbox max 16 |

`GBAVideoRenderer`'s vtable exposes `palette`, `vram`, `oam`, and `cache`
pointers, so renderer-level code can decode tilemaps without game-specific
symbols.

## 3. Implemented dump format

The patch writes one directory, once, after `_finalizeLayers()` and before the
renderer clears its per-frame state:

```
meta.json
vram.bin          0x18000 bytes
palette.bin       0x400 bytes
oam.bin           0x400 bytes
bg0.rgba          RGBA8, 240*scale x 160*scale, top-down
bg1.rgba
bg2.rgba
bg3.rgba
obj.rgba
```

`meta.json` (actual output):

```json
{
  "format": "gba-diorama-dump",
  "version": 1,
  "frame": 120,
  "scale": 2,
  "screen": [240, 160],
  "layer_size": [480, 320],
  "flip_y": false,
  "dispcnt": 320,
  "mode": 0,
  "blda": 0, "bldb": 0, "bldy": 0,
  "bg": [
    {"index": 0, "enabled": true, "priority": 0, "char_base": 0,
     "screen_base": 7168, "size": 0, "bpp": 4, "scroll": [0, 0],
     "affine": false}
  ]
}
```

Implementation notes:

- Enable with `GBA_DIORAMA_DUMP_DIR=/path/to/out`. Optional
  `GBA_DIORAMA_DUMP_FRAME=120` selects the frame (default 120) and the hook
  writes once, then stops.
- `GBA_DIORAMA_DUMP_DIR` must already exist. Use `mkdir -p` first.
- Readback uses `glReadPixels` from each layer FBO, not `glGetTexImage`, so the
  same code works on GLES3. Rows are flipped to top-down; `flip_y` is false.
- Clear stale GL errors before readback and check
  `glCheckFramebufferStatus()`; otherwise the first layer can be dropped.
- RGBA8 is enough for the lab. The lab's `Texture` expects uint8.
- obj flags/depth, window, and backdrop are not dumped yet. Add them when the
  compositor needs them.
- Do not commit dumps containing ROM-derived tiles. Use them locally; commit only
  synthetic fixtures and metrics.

## 4. Integration phases

### Phase 1 — dump only (done)

- The dump hook is in `GBAVideoGLRendererFinishFrame()` via
  `patches/mgba-diorama-dump.patch`.
- `gba_diorama/dump.py` loads the format, decodes tilemaps from VRAM when a
  layer image is absent, and `gba_diorama.inspect` writes per-layer PNGs.
- The synthetic dump round-trip is covered by tests. Real Emerald Route 101 was
  captured at 2x scale and rendered offline.
- Decoded VRAM matches the dumped BG0 exactly. BG1 differs on ~2.2% of pixels
  (scroll/edge sampling); BG2/BG3 mismatch when meta byte addresses are treated
  as BGCNT fields. Correct conversion is `char_base = meta_char_base >> 13`,
  `screen_base = meta_screen_base >> 10`.

### Phase 2 — offline 3D render (done)

- `python -m gba_diorama --dump <dir>` loads a real dump, picks the ground layer
  by alpha coverage × tile variety, meshes non-square world extents, and crops
  visible OAM entries from the OBJ layer.
- Route 101 output: `docs/images/emerald-route101.png`.
- Sparse overlay layers are skipped by default until height inference can
  distinguish canopies from buildings; `--all-layers` forces the naive version.

### Phase 3 — `hd2d` pass in mGBA

- Add a `hd2d` struct to `GBAVideoGLRenderer` (`gl.h`), with its own FBO,
  shaders, mesh buffers, and profile state.
- Allocate/resize in `_initFramebuffers()` and `GBAVideoGLRendererSetScale()`.
- In `_finalizeLayers()`, choose `hd2d` or the stock finalize based on scene
  signature/fallback.
- Render BG0/UI flat on top; keep text readable.
- Free resources in the renderer's deinit/reset path.
- Keep `getPixels`/screenshot output working.

### Phase 4 — profiles and debug UI

- Add a debug overlay: current scene signature, layer roles, heights, confidence,
  fallback reason.
- Hot-reload profile TOML.
- Add hotkeys to cycle layer roles/heights and toggle 2D fallback.
- Log a one-line scene signature on change so users can report unsupported
  scenes.

## 5. Scene signature

Use a compact tuple:

```
(bg_mode, enabled_mask, priority_tuple, affine_mask, screen_size_tuple)
```

Examples:

- Emerald overworld: `(0, 0b1111, (0,1,2,3), 0, (0,0,0,0))`
- Emerald battle: `(0, 0b1111, (0,0,1,3), 0, (0,0,0,0))`
- Emerald title: `(1, ..., (...,), 0b0100, ...)`

The signature should be derived from `renderer->dispcnt` and the BG control
registers, not from game code. `mGameInfo.code` selects the profile family;
romhacks inherit the base code unless a hash/name override exists.

## 6. Fallback rules

Use the stock 2D composite when:

- BG mode is 1/2 (affine) or 3/4/5 (bitmap), unless a specialized path exists.
- The scene signature has no profile and generic confidence is low.
- Windows/blending/alpha effects dominate the frame (menus, transitions,
  weather, battle intros).
- The user presses the "2D" hotkey.

A useful intermediate: render BG3/BG2/BG1/OBJ in 3D, then draw BG0/UI and the
window mask flat on top with the stock rules.

## 7. Build and run

```bash
git clone https://github.com/mgba-emu/mgba
cd mgba
git checkout 39a3d93
git apply /path/to/gba-diorama/patches/mgba-diorama-dump.patch

cmake -S . -B build -G Ninja \
  -DBUILD_QT=ON -DBUILD_SDL=OFF \
  -DBUILD_GL=ON -DBUILD_GLES3=ON -DUSE_EPOXY=ON \
  -DSKIP_GIT=ON -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build build -j"$(nproc)"
```

`SKIP_GIT=ON` avoids a CMake failure when the checkout has no release tags.

The GL renderer is only active in the Qt frontend, not the default SDL one.
Enable **Configure → Emulation → Video → OpenGL** (`hwaccelVideo=1`) and set
**Video scale** to 2–4 for development (1–16 supported). Then:

```bash
mkdir -p /tmp/emerald-dump
GBA_DIORAMA_DUMP_DIR=/tmp/emerald-dump \
GBA_DIORAMA_DUMP_FRAME=120 \
build/qt/mgba-qt -C hwaccelVideo=1 -C videoScale=2 /path/to/emerald.gba
```

Inspect the result:

```bash
python3 -m gba_diorama.inspect /tmp/emerald-dump --out /tmp/emerald-layers
```

For a self-contained smoke-test ROM with no copyrighted assets, run
`python3 tools/make_test_rom.py /tmp/diorama-test.gba` and boot that instead.

## 8. Performance notes

- Do not port the CPU rasterizer. Port the mesh builder + camera math; use GL
  for rasterization.
- Build the mesh only when the layer/tilemap state changes, not every frame.
- The world atlas should update incrementally; a full 512×512 re-upload per frame
  is unnecessary.
- Start at 2× scale and 1280×720 output; measure before optimizing.
- The current synthetic scene is ~3.5k triangles. Real maps will be larger, but
  greedy merging and per-tile caching keep it manageable.

## 9. Legal / licensing

- mGBA is MPL-2.0 (file-level copyleft). This repo is MPL-2.0 so code can move
  between them.
- Do not distribute ROMs, saves, or extracted game art.
- Keep synthetic fixtures in git; keep real dumps out.
