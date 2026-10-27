"""Pure project operations. Palette indices and four-connected geometry are canonical."""

import copy
import math

import cv2
import numpy as np

from canvas_size import painting_geometry
from facial import eye_mask
from regions import component_map, label_dimensions

MAX_BOXES = 128


def rectangles(value):
    if not isinstance(value, list) or len(value) > MAX_BOXES:
        raise ValueError("Use at most 128 protection rectangles.")
    result = []
    for box in value:
        if (
            not isinstance(box, list)
            or len(box) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
        ):
            raise ValueError(
                "Protection rectangles need four finite normalized coordinates."
            )
        x, y, w, h = box
        if (
            x < 0
            or y < 0
            or x >= 1
            or y >= 1
            or w <= 0
            or h <= 0
            or x + w > 1.0000001
            or y + h > 1.0000001
        ):
            raise ValueError("Protection rectangles must stay inside the image.")
        result.append([float(v) for v in box])
    return result


def pixel_boxes(shape, manual):
    h, w = shape[:2]
    result = []
    for x, y, bw, bh in rectangles(manual):
        left, top = math.floor(x * w), math.floor(y * h)
        right, bottom = (
            min(w, max(left + 1, math.ceil((x + bw) * w))),
            min(h, max(top + 1, math.ceil((y + bh) * h))),
        )
        result.append([left, top, right - left, bottom - top])
    return result


def protection_boxes(state):
    return [
        box for i, box in enumerate(state["detected"]) if i not in state["disabled"]
    ] + pixel_boxes(state["labels"].shape, state["manual"])


def protection_mask(state):
    return eye_mask(state["labels"].shape, protection_boxes(state))


def region_data(state):
    return component_map(state["labels"], state["params"]["k"])


def validate_numbers(state):
    if not state["overrides"]:
        return
    regions, _, colors = region_data(state)
    h, w = regions.shape
    geometry = painting_geometry(w, h, state["params"])
    scale = geometry["minimum_font_scale"]
    if scale is None:
        scale = min(geometry["font_scale"], 0.3)
    protected = protection_mask(state)
    seen = set()
    for override in state["overrides"]:
        ax, ay = override["anchor"]
        cx, cy = override["center"]
        region = int(regions[ay, ax])
        bw, bh, _, _ = label_dimensions(
            int(colors[region]), geometry["stroke_width"], scale
        )
        left, top = cx - bw // 2, cy - bh // 2
        if (
            region in seen
            or left < 0
            or top < 0
            or left + bw > w
            or top + bh > h
            or not np.all(regions[top : top + bh, left : left + bw] == region)
            or protected[top : top + bh, left : left + bw].any()
        ):
            raise ValueError(
                "A saved manual number is outside its region or protected geometry."
            )
        seen.add(region)


def integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}.")
    return value


def apply_edit(state, command):
    """Return a new state; never run automatic cleanup after a finishing edit."""
    kind = command.get("type")
    if kind not in {"recolor", "merge", "number"}:
        raise ValueError("Unknown edit command.")
    regions, stats, colors = region_data(state)
    region = integer(command.get("region"), "Region", 0, len(stats) - 1)
    selected = regions == region
    result = dict(state, overrides=copy.deepcopy(state["overrides"]))
    if kind in {"merge", "recolor"}:
        if kind == "merge":
            target = integer(command.get("target"), "Neighbor", 0, len(stats) - 1)
            halo = cv2.dilate(
                selected.astype(np.uint8),
                np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], np.uint8),
            ).astype(bool)
            if target == region or not np.any((regions == target) & halo):
                raise ValueError("Merge requires a neighbor sharing a pixel edge.")
            color = int(colors[target])
        else:
            color = (
                integer(command.get("color"), "Palette number", 1, state["params"]["k"])
                - 1
            )
        if np.any(selected & protection_mask(state)) and color != int(colors[region]):
            raise ValueError(
                "This region contains protected pixels. Remove its protection and regenerate before recoloring."
            )
        if color == int(colors[region]):
            raise ValueError("Choose a different palette color.")
        result["labels"] = state["labels"].copy()
        result["labels"][selected] = color
        # Number overrides touching changed topology are dropped, not relocated silently.
        touching = cv2.dilate(
            selected.astype(np.uint8),
            np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], np.uint8),
        ).astype(bool)
        changed_regions = {region} | set(
            np.unique(regions[touching & (state["labels"] == color)]).tolist()
        )
        result["overrides"] = [
            o
            for o in result["overrides"]
            if int(regions[o["anchor"][1], o["anchor"][0]]) not in changed_regions
        ]
    else:
        h, w = state["labels"].shape
        cx = integer(command.get("x"), "Number X", 0, w - 1)
        cy = integer(command.get("y"), "Number Y", 0, h - 1)
        geometry = painting_geometry(w, h, state["params"])
        scale = geometry["minimum_font_scale"]
        if scale is None:
            scale = min(geometry["font_scale"], 0.3)
        bw, bh, _, _ = label_dimensions(
            int(colors[region]), geometry["stroke_width"], scale
        )
        left, top = cx - bw // 2, cy - bh // 2
        if (
            left < 0
            or top < 0
            or left + bw > w
            or top + bh > h
            or not np.all(regions[top : top + bh, left : left + bw] == region)
            or protection_mask(state)[top : top + bh, left : left + bw].any()
        ):
            raise ValueError(
                "The complete readable number box must fit inside this region and outside protected eyes."
            )
        result["overrides"] = [
            o
            for o in result["overrides"]
            if int(regions[o["anchor"][1], o["anchor"][0]]) != region
        ]
        result["overrides"].append({"anchor": [cx, cy], "center": [cx, cy]})
    return result
