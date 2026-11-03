import numpy as np

from gba_diorama.pipeline import render_demo


def test_render_demo_without_post():
    result = render_demo(320, 180, seed=7, post=False)
    assert result.image.shape == (180, 320, 3)
    assert result.image.dtype == np.uint8
    assert result.image.std() > 4.0
    assert len(result.scene.triangles) > 500


def test_render_demo_with_post_is_deterministic():
    a = render_demo(256, 144, seed=7, post=True)
    b = render_demo(256, 144, seed=7, post=True)
    assert np.array_equal(a.image, b.image)
