"""Build docs/images/layers.png: the three decoded BG layers plus the final frame."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from gba_diorama.pipeline import render_demo
from gba_diorama.synthetic import build_synthetic_world


def _on_checker(image: np.ndarray, size: int = 320) -> Image.Image:
    rgba = Image.fromarray(image, "RGBA").resize((size, size), Image.Resampling.NEAREST)
    bg = Image.new("RGBA", (size, size), (22, 24, 34, 255))
    draw = ImageDraw.Draw(bg)
    step = 16
    for y in range(0, size, step):
        for x in range(0, size, step):
            if (x // step + y // step) % 2 == 0:
                draw.rectangle([x, y, x + step - 1, y + step - 1], fill=(28, 31, 43, 255))
    return Image.alpha_composite(bg, rgba).convert("RGB")


def main() -> int:
    out = Path("docs/images/layers.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    world = build_synthetic_world(7)
    demo = render_demo(640, 360, seed=7, post=True).image
    demo_img = Image.fromarray(demo).resize((640, 360), Image.Resampling.LANCZOS)

    cells = [
        ("BG3  ground", _on_checker(world.layers[3])),
        ("BG2  mid / props", _on_checker(world.layers[2])),
        ("BG1  top / canopy", _on_checker(world.layers[1])),
    ]
    sheet = Image.new("RGB", (960, 320 + 360), (12, 14, 22))
    for i, (label, img) in enumerate(cells):
        sheet.paste(img, (i * 320, 0))
        ImageDraw.Draw(sheet).text((i * 320 + 10, 8), label, fill=(235, 235, 245))
    sheet.paste(demo_img, (160, 320))
    ImageDraw.Draw(sheet).text((170, 328), "composited 3D2D frame", fill=(235, 235, 245))
    sheet.save(out)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
