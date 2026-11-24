"""gen3hd — a data-driven HD-2D runtime for Pokémon Gen 3 ROMs.

The old `gba_diorama` pipeline treated a 240x160 frame as the world and
guessed objects from colour.  This package starts from the opposite end:

    live GBA state (VRAM / OAM / palette / WRAM / IO)
      + ROM (map layout / tilesets / metatile definitions & attributes)
      -> semantic whole-map WorldModel
      -> renderer

No green mask, no rectangle detector, no atlas stitching.
"""

from .world import WorldModel, compile_world

__all__ = ["WorldModel", "compile_world"]
__version__ = "0.1.0"
