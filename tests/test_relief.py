import numpy as np

from gba_diorama.mesh import _organic_cutout
from gba_diorama.relief import (
    CanopyComponent,
    build_canopy_field,
    detect_canopies,
    make_side_view_card,
)


def _scene() -> np.ndarray:
    image = np.zeros((64, 64, 4), dtype=np.uint8)
    image[..., :3] = (112, 192, 160)
    image[..., 3] = 255
    # Two tree canopies joined by a one-pixel dark bridge.  Opening should
    # split them instead of producing one long hedge.
    image[8:24, 8:24, :3] = (40, 120, 40)
    image[8:24, 26:42, :3] = (40, 120, 40)
    image[16, 24:26, :3] = (30, 70, 20)
    return image


def test_detect_canopies_splits_touching_blobs():
    components = detect_canopies(_scene())
    assert len(components) == 2
    assert all(component.area >= 48 for component in components)


def test_canopy_field_has_volume_and_removes_tree_from_ground():
    image = _scene()
    components = detect_canopies(image)
    field = build_canopy_field(image, components)
    assert field is not None
    assert field.present.any()
    assert field.heights.max() > field.heights.min()
    # The tree art is moved into the cutout texture; the ground is filled
    # with surrounding grass so the raised canopy does not get drawn twice.
    assert tuple(field.ground[16, 16, :3]) == (112, 192, 160)
    assert field.texture[16, 16, 3] == 255
    assert field.texture[0, 0, 3] == 0


def test_organic_cutout_trims_card_corners():
    image = np.full((32, 32, 4), (40, 120, 40, 255), dtype=np.uint8)
    cutout = _organic_cutout(image)
    assert cutout[0, 0, 3] == 0
    assert cutout[0, -1, 3] == 0
    assert cutout[16, 16, 3] == 255
    assert cutout.shape == image.shape


def test_canopy_card_composes_actual_trunk_without_grass_gap():
    image = np.zeros((64, 64, 4), dtype=np.uint8)
    image[..., :3] = (112, 192, 160)
    image[..., 3] = 255
    image[8:24, 8:24, :3] = (40, 120, 40)
    # Grass gap between canopy and the small brown trunk.
    image[24:30, 8:24, :3] = (112, 192, 160)
    image[30:36, 14:18, :3] = (90, 60, 40)

    component = detect_canopies(image)[0]
    assert component.texture is not None
    # The gap is removed: the composed card is shorter than canopy + gap + trunk.
    assert component.bbox[3] < 36
    assert tuple(component.texture[-1, 7, :3]) == (90, 60, 40)
    assert component.texture[-1, 7, 3] == 255


def test_side_view_card_is_procedural_deterministic_and_uses_source_pixels():
    texture = np.zeros((20, 14, 4), dtype=np.uint8)
    texture[:14, :, :3] = (40, 120, 40)
    texture[:14, :, 3] = 255
    texture[14:20, 6:8, :3] = (90, 60, 40)
    texture[14:20, 6:8, 3] = 255
    mask = np.zeros((20, 14), dtype=bool)
    mask[:14] = True
    mask[14:20, 6:8] = True
    component = CanopyComponent(
        mask=mask,
        bbox=(8, 8, 22, 28),
        area=int(mask.sum()),
        texture=texture,
        canopy_height=14,
    )

    card = make_side_view_card(component)
    assert card is not None
    assert card.shape[0] > card.shape[1]
    assert card.shape[0] > 14
    assert card[..., 3].any()
    # The card is generated from the source palette, not a hardcoded sprite.
    assert (card[..., 1] > card[..., 0]).any()
    # Same bbox -> same card, so romhack/regeneration runs are reproducible.
    assert np.array_equal(card, make_side_view_card(component))

    other = CanopyComponent(
        mask=mask,
        bbox=(40, 40, 54, 60),
        area=int(mask.sum()),
        texture=texture,
        canopy_height=14,
    )
    assert not np.array_equal(card, make_side_view_card(other))


def test_side_view_card_keeps_a_multi_tile_tree_as_one_component():
    # Emerald trees are often 2x2 or 3x3 8px tiles.  Detection must operate on
    # the whole BG image, not one tile at a time.
    image = np.zeros((128, 128, 4), dtype=np.uint8)
    image[..., :3] = (112, 192, 160)
    image[..., 3] = 255
    image[16:64, 16:64, :3] = (40, 120, 40)  # 48x48 = 6x6 tiles
    image[64:72, 34:46, :3] = (90, 60, 40)

    components = detect_canopies(image)
    assert len(components) == 1
    component = components[0]
    assert component.bbox[2] - component.bbox[0] >= 48
    assert component.bbox[3] - component.bbox[1] >= 48
    card = make_side_view_card(component)
    assert card is not None
    assert card.shape[1] > 48
    assert card.shape[0] > 48
