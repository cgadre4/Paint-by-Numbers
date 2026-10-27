"""Shared browser/API settings. Values are intentionally JSON serializable."""

DEFAULTS = dict(
    k=8,
    resolution_factor=1,
    bilateral_diameter=7,
    sigma_color=75,
    area_min=50,
    stroke_width=2,
    font_scale=1,
    color_space="lab",
    protect_eyes=True,
    canvas_width_cm=0,
    sizing_mode="physical",
    paper_size="a4",
    eye_detail="fine",
)
RANGES = dict(
    k=(3, 32),
    resolution_factor=(0.1, 1),
    bilateral_diameter=(5, 15),
    sigma_color=(25, 150),
    area_min=(10, 500),
    stroke_width=(1, 4),
    font_scale=(0.5, 2),
    canvas_width_cm=(0, 200),
)
PRESETS = {
    "simple": dict(DEFAULTS),
    "detailed": dict(
        DEFAULTS,
        k=24,
        area_min=20,
        bilateral_diameter=5,
        sigma_color=40,
        stroke_width=1,
    ),
}
