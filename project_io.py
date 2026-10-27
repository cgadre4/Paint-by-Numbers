"""Versioned local project bundles with bounded, allowlisted decoding; no extraction."""

import hashlib
import io
import json
import math
import re
import stat
import zipfile

import cv2
import numpy as np
from PIL import Image

from editing import integer, rectangles, protection_mask
from settings import DEFAULTS, RANGES

FORMAT_VERSION = 1
RENDERER_VERSION = 1
MAX_BUNDLE = 128 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
MAX_DECODED = 96 * 1024 * 1024
MAX_HISTORY = 64 * 1024 * 1024
MAX_ACTIONS = 100
MAX_MANIFEST = 2 * 1024 * 1024


def png_bytes(array):
    ok, encoded = cv2.imencode(".png", array)
    if not ok:
        raise ValueError("Could not encode project pixels.")
    return encoded.tobytes()


def strict_json(data):
    def bad(value):
        raise ValueError("Non-finite JSON values are not allowed.")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key.")
            result[key] = value
        return result

    return json.loads(data, parse_constant=bad, object_pairs_hook=pairs)


def validate_params(params):
    if not isinstance(params, dict) or set(params) != set(DEFAULTS):
        raise ValueError("Unsupported project settings.")
    for key, (lo, hi) in RANGES.items():
        value = params[key]
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not lo <= value <= hi
        ):
            raise ValueError("Invalid setting: " + key)
        if (
            key
            in {"k", "bilateral_diameter", "sigma_color", "area_min", "stroke_width"}
            and type(value) is not int
        ):
            raise ValueError("Expected integer setting: " + key)
    if (
        params["color_space"] not in {"lab", "rgb"}
        or type(params["protect_eyes"]) is not bool
        or params["paper_size"] not in {"a4", "letter"}
        or params["sizing_mode"] not in {"physical", "pixels"}
        or 0 < params["canvas_width_cm"] < 5
    ):
        raise ValueError("Invalid project setting option.")


def decode_png(data, mode):
    with Image.open(io.BytesIO(data), formats=["PNG"]) as image:
        w, h = image.size
        if (
            not 0 < w * h <= 4_000_000
            or image.mode != mode
            or getattr(image, "n_frames", 1) != 1
            or image.getexif()
            or image.info.get("icc_profile")
        ):
            raise ValueError("Project PNG has invalid dimensions, mode, or metadata.")
        image.load()
        array = np.asarray(image).copy()
    return array[:, :, ::-1].copy() if mode == "RGB" else array


def pack_document(document, *, result=None, include_original=True):
    assets = {}
    decoded = {}
    identities = {}

    def asset(array):
        if id(array) in identities:
            return identities[id(array)]
        data = png_bytes(array)
        name = "assets/" + hashlib.sha256(data).hexdigest() + ".png"
        assets[name] = data
        decoded[name] = array.nbytes
        identities[id(array)] = name
        return name

    snapshots = []
    for state in document["states"]:
        snapshots.append(
            {
                **{
                    key: state[key]
                    for key in (
                        "params",
                        "manual",
                        "detected",
                        "disabled",
                        "faces",
                        "overrides",
                        "title",
                    )
                },
                "centers": state["centers"].tolist(),
                **{key: asset(state[key]) for key in ("source", "raw", "labels")},
            }
        )
    current_refs = {
        snapshots[document["cursor"]][key] for key in ("source", "raw", "labels")
    }
    history = sum(size for name, size in decoded.items() if name not in current_refs)
    if history > MAX_HISTORY or sum(decoded.values()) > MAX_DECODED:
        raise ValueError(
            "This operation exceeds the 64 MiB undo budget. Save and start a new project first."
        )
    manifest = dict(
        format=FORMAT_VERSION,
        renderer=RENDERER_VERSION,
        snapshots=snapshots,
        cursor=document["cursor"],
        pruned=document.get("pruned", 0),
        original=bool(include_original and document.get("original")),
    )
    encoded = json.dumps(manifest, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > MAX_MANIFEST:
        raise ValueError("Project history metadata is too large.")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", encoded)
        for name, data in assets.items():
            archive.writestr(name, data)
        if manifest["original"]:
            archive.writestr("original.bin", document["original"])
        if result:
            from exports import image_bytes

            archive.writestr("template.png", image_bytes(result["template_image"]))
            archive.writestr("preview.png", image_bytes(result["processed_image"]))
    data = output.getvalue()
    if len(data) > MAX_BUNDLE:
        raise ValueError("The project exceeds the 128 MiB file limit.")
    return data


def unpack_document(data):
    if not data or len(data) > MAX_BUNDLE:
        raise ValueError("Project files must be 128 MiB or smaller.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            names = [e.filename for e in entries]
            if (
                len(names) != len(set(names))
                or len(names) > 310
                or "manifest.json" not in names
                or sum(e.file_size for e in entries) > MAX_EXPANDED
            ):
                raise ValueError("Invalid or oversized project archive.")
            for entry in entries:
                if (
                    entry.flag_bits & 1
                    or entry.compress_type
                    not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    or stat.S_ISLNK(entry.external_attr >> 16)
                    or not (
                        entry.filename
                        in {
                            "manifest.json",
                            "original.bin",
                            "template.png",
                            "preview.png",
                        }
                        or re.fullmatch(r"assets/[a-f0-9]{64}\.png", entry.filename)
                    )
                ):
                    raise ValueError("Unexpected project archive member.")
                limit = (
                    MAX_MANIFEST
                    if entry.filename == "manifest.json"
                    else 20 * 1024 * 1024
                )
                if entry.file_size > limit:
                    raise ValueError("Oversized project archive member.")
            manifest = strict_json(archive.read("manifest.json"))
            if (
                not isinstance(manifest, dict)
                or set(manifest)
                != {"format", "renderer", "snapshots", "cursor", "pruned", "original"}
                or type(manifest["format"]) is not int
                or type(manifest["renderer"]) is not int
                or manifest["format"] != FORMAT_VERSION
                or manifest["renderer"] != RENDERER_VERSION
            ):
                raise ValueError("Unsupported project format or renderer version.")
            snapshots = manifest["snapshots"]
            if (
                not isinstance(snapshots, list)
                or not 1 <= len(snapshots) <= MAX_ACTIONS + 1
            ):
                raise ValueError("Invalid project history.")
            integer(manifest["cursor"], "History position", 0, len(snapshots) - 1)
            integer(manifest["pruned"], "Pruned history count", 0, 1_000_000)
            if type(manifest["original"]) is not bool or manifest["original"] != (
                "original.bin" in names
            ):
                raise ValueError("Invalid original-image declaration.")
            cache, used, decoded_size = {}, {"manifest.json"}, 0

            def pixels(name, mode):
                nonlocal decoded_size
                if (
                    not isinstance(name, str)
                    or not re.fullmatch(r"assets/[a-f0-9]{64}\.png", name)
                    or name not in names
                ):
                    raise ValueError("Missing project asset.")
                used.add(name)
                if (name, mode) not in cache:
                    encoded = archive.read(name)
                    if hashlib.sha256(encoded).hexdigest() != name[7:-4]:
                        raise ValueError("Project asset checksum mismatch.")
                    array = decode_png(encoded, mode)
                    decoded_size += array.nbytes
                    if decoded_size > MAX_DECODED:
                        raise ValueError("Decoded project history is too large.")
                    cache[name, mode] = array
                return cache[name, mode]

            states = []
            keys = {
                "params",
                "manual",
                "detected",
                "disabled",
                "faces",
                "overrides",
                "title",
                "centers",
                "source",
                "raw",
                "labels",
            }
            for saved in snapshots:
                if not isinstance(saved, dict) or set(saved) != keys:
                    raise ValueError("Invalid snapshot metadata.")
                state = dict(saved)
                validate_params(state["params"])
                state["manual"] = rectangles(state["manual"])
                if not isinstance(state["title"], str) or len(state["title"]) > 120:
                    raise ValueError("Invalid history title.")
                for key in ("raw", "labels"):
                    state[key] = pixels(state[key], "L")
                state["source"] = pixels(state["source"], "RGB")
                h, w = state["labels"].shape
                if state["raw"].shape != (h, w) or state["source"].shape != (h, w, 3):
                    raise ValueError("Project image dimensions do not agree.")
                k = state["params"]["k"]
                if state["labels"].max() >= k or state["raw"].max() >= k or h * w < k:
                    raise ValueError("Project palette indices are invalid.")
                centers = np.asarray(state["centers"])
                if (
                    centers.shape != (k, 3)
                    or centers.dtype.kind not in "fiu"
                    or not np.isfinite(centers).all()
                    or centers.min() < 0
                    or centers.max() > 255
                ):
                    raise ValueError("Invalid project palette.")
                state["centers"] = centers.astype(np.float32)
                if (
                    not isinstance(state["detected"], list)
                    or len(state["detected"]) > 10000
                ):
                    raise ValueError("Invalid detected eye boxes.")
                for box in state["detected"]:
                    if not isinstance(box, list) or len(box) != 4:
                        raise ValueError("Invalid detected eye box.")
                    x, y, bw, bh = box
                    integer(x, "Eye X", 0, w - 1)
                    integer(y, "Eye Y", 0, h - 1)
                    integer(bw, "Eye width", 1, w - x)
                    integer(bh, "Eye height", 1, h - y)
                if not isinstance(state["disabled"], list) or len(
                    set(state["disabled"])
                ) != len(state["disabled"]):
                    raise ValueError("Invalid disabled eye boxes.")
                for index in state["disabled"]:
                    integer(index, "Detected eye", 0, len(state["detected"]) - 1)
                integer(state["faces"], "Detected faces", 0, 10000)
                if (
                    not isinstance(state["overrides"], list)
                    or len(state["overrides"]) > 4096
                ):
                    raise ValueError("Too many manual numbers.")
                for override in state["overrides"]:
                    if not isinstance(override, dict) or set(override) != {
                        "anchor",
                        "center",
                    }:
                        raise ValueError("Invalid manual number.")
                    for key in ("anchor", "center"):
                        pair = override[key]
                        if not isinstance(pair, list) or len(pair) != 2:
                            raise ValueError("Invalid number coordinates.")
                        integer(pair[0], "Number X", 0, w - 1)
                        integer(pair[1], "Number Y", 0, h - 1)
                mask = protection_mask(state)
                if not np.array_equal(state["labels"][mask], state["raw"][mask]):
                    raise ValueError("Project modifies protected palette pixels.")
                states.append(state)
            for name in ("template.png", "preview.png"):
                if name in names:
                    used.add(name)
                    image = decode_png(archive.read(name), "RGB")
                    if image.shape != states[manifest["cursor"]]["source"].shape:
                        raise ValueError("Saved output dimensions do not agree.")
            original = None
            if manifest["original"]:
                used.add("original.bin")
                original = archive.read("original.bin")
                from pipeline import decode_image

                decode_image(
                    original, states[manifest["cursor"]]["params"], resize=True
                )
            if set(names) != used:
                raise ValueError("Unreferenced project assets are not allowed.")
            current = snapshots[manifest["cursor"]]
            current_names = {current[key] for key in ("source", "raw", "labels")}
            if (
                sum(
                    array.nbytes
                    for (name, mode), array in cache.items()
                    if name not in current_names
                )
                > MAX_HISTORY
            ):
                raise ValueError("Project exceeds the 64 MiB undo budget.")
            return dict(
                states=states,
                cursor=manifest["cursor"],
                original=original,
                pruned=manifest["pruned"],
            )
    except (
        OSError,
        KeyError,
        TypeError,
        OverflowError,
        zipfile.BadZipFile,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("The project file is damaged or unsupported.") from exc
