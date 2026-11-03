# mGBA handoff

This is the concrete integration plan for turning the offline lab into an mGBA
feature. It assumes the mGBA source tree at commit `39a3d93` ("OpenGL: Add float
framebuffer support option for shaders"), which is the revision the initial
research was done against. Line numbers will drift; function names are the
stable anchors.

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

## 3. Proposed dump format

Add an opt-in dump behind an environment variable, e.g.
`GBA_DIORAMA_DUMP=/path/to/out`, so normal users pay nothing.

Dump once per N frames, or on a hotkey. Capture **before** `_finalizeLayers()`
so the layer textures are still raw.

```
frame_000001/
  meta.json
  vram.bin          0x18000 bytes
  palette.bin       0x400 bytes
  oam.bin           0x400 bytes
  bg0.rgba          RGBA8, width×height (usually 240*scale × 160*scale)
  bg1.rgba
  bg2.rgba
  bg3.rgba
  obj.rgba
  obj_flags.bin     raw flags (RGBA8I or uint32 per pixel; document it)
  window.bin
  backdrop.bin
```

`meta.json`:

```json
{
  "frame": 1,
  "game": {"title": "POKEMON EMER", "code": "BPEE", "maker": "01", "version": 0},
  "scale": 2,
  "screen": [240, 160],
  "layer_size": [480, 320],
  "dispcnt": "0x1F40",
  "bgcnt": ["0x0000", "0x0101", "0x0202", "0x0303"],
  "bg_hofs": [0, 0, 0, 0],
  "bg_vofs": [0, 0, 0, 0],
  "bg_affine": [[1,0,0,1], [1,0,0,1], [1,0,0,1], [1,0,0,1]],
  "bldcnt": "0x0000",
  "bldalpha": "0x0000",
  "bldy": "0x0000",
  "mosaic": "0x0000",
  "winin": "0x0000",
  "winout": "0x0000"
}
```

Implementation notes:

- `bg0.rgba`…`bg3.rgba` may be raw RGBA8 or PNG. Raw files need `layer_size`
  in `meta.json`; PNGs carry their own dimensions. The lab loader supports both.
- `glGetTexImage` on the layer textures is the least invasive readback. It
  returns the texture at its actual size (`240*scale` × `160*scale`).
- `glReadPixels` from each BG FBO is an alternative; bind the FBO and read.
- GL textures are usually bottom-up. Flip rows before writing `.rgba`, or record
  `"flip_y": true` in `meta.json` and flip in the loader.
- RGBA8 is enough for the lab. If mGBA is using float framebuffers, convert to
  RGBA8 on readback; the lab's `Texture` expects uint8.
- Do not commit dumps containing ROM-derived tiles. Use them locally; commit only
  synthetic fixtures and metrics.

## 4. Integration phases

### Phase 1 — dump only

- Add the dump hook to `GBAVideoGLRendererFinishFrame()`.
- Add a `gba_diorama.dump` loader in the lab (not written yet). It should load
  `meta.json`, decode the raw BG tilemaps through `gba.py`, and compare the
  decoded layers against the dumped `.rgba` files.
- This validates the decoder against mGBA before any 3D work.

### Phase 2 — offline 3D render

- Load a real dump into the lab.
- Build the mesh from the decoded layers and profile.
- Render side-by-side with the stock composite.
- Iterate on camera, heights, materials, and post without touching mGBA.

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
cmake -S . -B build -DBUILD_QT=ON -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build build -j"$(nproc)"
```

`BUILD_GL` and `BUILD_GLES3` default on. In the Qt frontend, enable
**Configure → Emulation → Video → OpenGL** (`hwaccelVideo`) and set
**Video scale** to 2–4 for development (1–16 supported).

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
