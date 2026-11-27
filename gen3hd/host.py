"""Live GBA host backed by the local romdev tool server.

romdev runs a libretro mGBA core and exposes a session-scoped HTTP API.  This
client is intentionally thin: one session id, one loaded ROM, and raw region
reads.  It is the runtime boundary for the first milestone; a libretro or
mGBA-native backend can implement the same interface later.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

from .gen3.locate import LocateError, find_backup_layout
from .state import GbaSnapshot

DEFAULT_BASE = "http://127.0.0.1:7331"
DEFAULT_SESSION = "gen3hd-live"

REGIONS = {
    "video_ram": 0x18000,
    "gba_palette": 0x400,
    "gba_oam": 0x400,
    "gba_io_regs": 0x400,
    "gba_iwram": 0x8000,
    "system_ram": 0x40000,
}


class RomdevError(RuntimeError):
    """A romdev tool call failed."""


class RomdevHost:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE,
        session: str = DEFAULT_SESSION,
        *,
        timeout: float = 300.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session
        self.timeout = timeout
        self.frame_count = 0
        self.loaded_path: str | None = None

    # -- transport ---------------------------------------------------------

    def call(self, tool: str, **arguments):
        request = urllib.request.Request(
            f"{self.base_url}/tool/{tool}",
            data=json.dumps(arguments).encode(),
            headers={
                "content-type": "application/json",
                "x-romdev-session": self.session,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise RomdevError(f"{tool} failed ({exc.code}): {body[:1500]}") from exc
        except urllib.error.URLError as exc:
            raise RomdevError(
                f"romdev is not reachable at {self.base_url}; start it with "
                f"`npx romdevtools` (systemd user service: romdev-mcp)"
            ) from exc

    # -- lifecycle ---------------------------------------------------------

    def load(self, rom_path: str | Path) -> dict:
        path = str(Path(rom_path).resolve())
        result = self.call("loadMedia", platform="gba", path=path)
        self.loaded_path = path
        self.frame_count = 0
        return result

    def import_sram(self, save_path: str | Path) -> dict:
        path = Path(save_path)
        payload = path.read_bytes()
        # mGBA writes a 16-byte RTC footer on some .sav files; the cartridge
        # SRAM image itself is 128 KiB.  Trim rather than reject.
        if len(payload) == 131088:
            trimmed = path.with_suffix(".128k.sav")
            trimmed.write_bytes(payload[:131072])
            path = trimmed
        return self.call("state", op="importSram", path=str(path.resolve()))

    def reset(self, hard: bool = True) -> dict:
        self.frame_count = 0
        return self.call("host", op="reset", hard=hard)

    def step(self, frames: int = 1) -> dict:
        result = self.call("frame", op="step", count=int(frames))
        self.frame_count = int(result.get("frameCount", self.frame_count + frames))
        return result

    def press(self, button: str, frames: int = 6) -> dict:
        result = self.call("input", op="press", button=button, frames=frames)
        self.frame_count = int(result.get("frameCount", self.frame_count))
        return result

    def screenshot(self, path: str | Path) -> dict:
        return self.call("frame", op="screenshot", path=str(Path(path).resolve()))

    def save_state(self, path: str | Path) -> dict:
        return self.call("state", op="save", path=str(Path(path).resolve()))

    def load_state(self, path: str | Path) -> dict:
        return self.call("state", op="load", path=str(Path(path).resolve()))

    # -- memory ------------------------------------------------------------

    def read_region(self, region: str, length: int | None = None, offset: int = 0) -> bytes:
        size = length if length is not None else REGIONS[region]
        cache = Path("/home/g1/.hermes/cache/scratch/gen3hd-regions")
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / f"{region}.bin"
        self.call(
            "memory",
            op="read",
            region=region,
            offset=offset,
            length=size,
            outputPath=str(target),
            inline=False,
        )
        return target.read_bytes()

    def snapshot(self, frame: int | None = None) -> GbaSnapshot:
        vram = np.frombuffer(self.read_region("video_ram"), dtype=np.uint8)
        palette = np.frombuffer(self.read_region("gba_palette"), dtype=np.uint8)
        oam = np.frombuffer(self.read_region("gba_oam"), dtype=np.uint8)
        io = np.frombuffer(self.read_region("gba_io_regs"), dtype="<u2")
        iwram = np.frombuffer(self.read_region("gba_iwram"), dtype=np.uint8)
        ewram = np.frombuffer(self.read_region("system_ram"), dtype=np.uint8)
        return GbaSnapshot(
            vram=vram,
            palette=palette,
            oam=oam,
            io=io,
            iwram=iwram,
            ewram=ewram,
            frame=self.frame_count if frame is None else frame,
        )

    # -- boot --------------------------------------------------------------

    def boot_to_overworld(self, save_path: str | Path, rom_path: str | Path) -> None:
        """Load a Gen 3 ROM + battery save and drive the title menu.

        The timings are deliberately generous: the game waits for input on the
        title/menu screens, so overshooting an animation is harmless.  If the
        save is absent we stop at the menu and let the caller decide.
        """

        self.load(rom_path)
        # An empty path is "no save": Path("") is "." on POSIX and would pass
        # exists(), then fail reading a directory.
        if str(save_path) and Path(save_path).is_file():
            self.import_sram(save_path)
        self.reset()
        self.step(2200)   # Game Freak / legendary intro
        self.step(1200)   # logo -> PRESS START
        for _ in range(4):
            self.press("start")
            self.step(200)
        for _ in range(10):
            self.press("a")
            self.step(180)
            if self._has_map():
                return
        raise RomdevError("could not reach an overworld with a live map layout")

    def _has_map(self) -> bool:
        try:
            iwram = np.frombuffer(self.read_region("gba_iwram"), dtype=np.uint8)
            ewram = np.frombuffer(self.read_region("system_ram"), dtype=np.uint8)
            find_backup_layout(iwram, ewram)
            return True
        except (LocateError, RomdevError):
            return False

    def wait_ready(self, seconds: float = 30.0) -> None:
        deadline = time.time() + seconds
        last: Exception | None = None
        while time.time() < deadline:
            try:
                self.call("catalog", op="status")
                return
            except RomdevError as exc:
                last = exc
                time.sleep(0.5)
        raise RomdevError(f"romdev did not become ready: {last}")
