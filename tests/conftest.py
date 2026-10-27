import io
import time

import numpy as np
import pytest
from PIL import Image

DEFAULTS = dict(k=8, resolution_factor=1, bilateral_diameter=7, sigma_color=75,
                area_min=50, stroke_width=2, font_scale=1, color_space="lab", protect_eyes=True, canvas_width_cm=0, sizing_mode="physical", paper_size="a4", eye_detail="fine")


def sample_image(width=256, height=160):
    y, x = np.indices((height, width))
    pixels = np.stack(((x // 32 * 31) % 256, (y // 20 * 37) % 256,
                       ((x // 32 + y // 20) * 43) % 256), axis=-1).astype('uint8')
    stream = io.BytesIO()
    Image.fromarray(pixels).save(stream, format='PNG')
    return stream.getvalue()


def wait_for(callback, predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = callback()
        if predicate(result):
            return result
        time.sleep(0.05)
    raise AssertionError('Timed out waiting for expected state')


@pytest.fixture
def params():
    return DEFAULTS.copy()
