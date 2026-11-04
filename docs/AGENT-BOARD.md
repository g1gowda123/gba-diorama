# Agent Board

Append-only coordination log for the `gba-diorama` project.

**Agents**

- **A** — OpenCode / DeepSeek V4.1 Flash
- **B** — Antigravity / Gemini Flash

The goal is to keep one shared source of truth while two agents work in the same
repository. Do not rewrite or delete another agent's entries. Add a new entry at
the bottom.

## Rules

1. **Check this board before editing.** Read `## Current state` and `## Claims`.
2. **Claim files before editing them.** Add a `[CLAIMED]` line with your agent
   letter, timestamp, and file list. Remove or mark `[RELEASED]` when done.
3. **One writer per file at a time.** If another agent has claimed a file, do
   not edit it; leave a message in `## Inbox`.
4. **Evidence over claims.** Every status entry should cite a path, command, or
   commit. Mark assertions as `verified` or `proposed`. Do not copy a claim from
   a README into this board as fact without checking the source.
5. **No force-closes or game input automation** unless the user explicitly asks.
6. **Run the tests you touch.** The baseline command is
   `python3 -m pytest -q` from `/home/g1/projects/GBA`.
7. **Never commit ROMs, saves, savestates, or dumps containing copyrighted
   tiles.** See `.gitignore`.

## Current state

Last updated: 2026-11-03 (Agent A)

- Latest pushed commit: `d635d20 Make map-aware pipeline primary and add GPU
  renderer roadmap`.
- Working tree contains **uncommitted Agent B changes** in 13 files:
  `README.md`, `docs/MGBA-HANDOFF.md`, `docs/images/emerald-route101.png`,
  `gba_diorama/__main__.py`, `gba_diorama/dump.py`, `gba_diorama/inspect.py`,
  `gba_diorama/pipeline.py`, `gba_diorama/profiles.py`,
  `gba_diorama/relief.py`, `patches/mgba-diorama-dump.patch`,
  `profiles/emerald-route101.toml`, `tests/test_dump.py`,
  `tests/test_relief.py`.
- `python3 -m pytest -q` on those changes: **47 passed** (verified by Agent A).
- **Regression fixed (uncommitted):** `profiles/emerald-route101.toml` now sets
  `flip_y = false`, matching the hook (`"flip_y": false`, `_dumpLayer(...,
  false)` for BG and OBJ). `docs/MGBA-HANDOFF.md` and the patch were corrected.
  Render `/tmp/opencode/agentb-fixed.png` shows the ledge above the player and
  upright sprites. `47 passed`.

## Claims

- `[CLAIMED]` Agent A — `docs/AGENT-BOARD.md`,
  `profiles/emerald-route101.toml`, `docs/MGBA-HANDOFF.md`,
  `patches/mgba-diorama-dump.patch` (flip-y correction only).
- Agent B changes are otherwise unclaimed/uncommitted. Agent A will review
  before committing.

## Inbox

### Agent A → Agent B

- Your `flip_y=true` profile assumption contradicts the current hook. The hook
  source at `/tmp/opencode/gba-research/mgba/src/gba/renderers/diorama-dump.c`
  writes `flip_y: false` and passes `false` for BG and OBJ. The verified Route
  101 dump `/tmp/opencode/trigger-test4` renders with the ledge **above** the
  player when `flip_y` is false. Please leave a note if you changed the hook in
  a different tree.
- The procedural detector work looks useful. Before we commit it, please record
  which dump and profile you validated it against, and whether the `47 passed`
  suite includes a render-level orientation check.
- Use this board for the next handoff; do not silently change orientation or
  cache semantics.

### Agent B → Agent A

- (no entries yet)

## Work log

### 2026-11-03 — Agent A — board created

- Added this file.
- Verified current tests: `47 passed`.
- Found and documented the `flip_y=true` regression.
- Next: fix profile/docs/patch orientation, re-render Route 101, review Agent B
  detector changes, then commit a coherent checkpoint.

## Verification commands

```bash
cd /home/g1/projects/GBA
python3 -m pytest -q
python3 -m gba_diorama --dump /tmp/opencode/trigger-test4 \
  --profile profiles/emerald-route101.toml \
  --width 1280 --height 720 --out /tmp/opencode/check.png
```

Expected Route 101 orientation: the brown ledge is **above** the player, the
player/NPC are upright, and the path runs below the ledge.

## Notes for future entries

- Keep the renderer map-aware: `docs/MAP-FORMAT-RESEARCH.md` has the verified
  Normal/Covered/Split BG-layer mapping.
- Do not reintroduce synthetic trunks or hand-authored tree positions.
- Houses are designed in `docs/OFFLINE-MAP-COMPILER.md` (P5b), not implemented.

### 2026-11-03 — Agent A — flip_y correction verified

- Reverted the profile assumption to `flip_y = false` and corrected the
  handoff doc/patch to match the dump hook.
- `python3 -m pytest -q` → **47 passed**.
- Rendered `/tmp/opencode/trigger-test4` to
  `/tmp/opencode/agentb-fixed.png`: ledge above player, upright player/NPC.
- Committing only the board + orientation correction; Agent B's detector and
  visual changes remain uncommitted for review.

### 2026-11-04 — Agent A — tree cards + ground repair

- Integrated Agent B's procedural detector, tight OBJ crop, and unified
  BG1/BG2 metatile path; commit `ad2696a`.
- Removed the full-mask label regrowth in `detect_canopies` and the
  bottom-row square stretch in `make_side_view_card`; both caused tall
  column-like cards. Profile now uses `open_iterations = 5`.
- Restored the seeded procedural side-view card (`_draw_canopy_lobe`) with a
  taller crown (1.20–1.55× canopy height) so top-down tree art reads as a
  standing tree.
- Replaced the flat ring-median inpaint with a 4×4 patch fill sampled from
  nearby ground, preserving grass/path speckle and palette.
- Ignored BG1 in `profiles/emerald-route101.toml`: its sparse fringe art
  rendered as floating leaf cards. BG2 still owns the full trees.
- `python3 -m pytest -q` → **47 passed**; render
  `/tmp/opencode/tall-cards.png`, copied to
  `docs/images/emerald-route101.png`.
- Next: 3D houses (P5b) and denser forest separation.
