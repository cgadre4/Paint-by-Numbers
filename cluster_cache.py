"""Bounded, non-pickle color-analysis cache validation."""
import zipfile
import math

import numpy as np


def load_clusters(path, k, expected_shape=None, *, eyes=False):
    """Return validated arrays, or None when an optional cache needs rebuilding."""
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            names = {e.filename for e in entries}
            allowed = {"labels.npy", "centers.npy", "protection.npy", "boxes.npy", "face_count.npy"}
            if (len(entries) != len(names) or not names <= allowed
                    or sum(e.file_size for e in entries) > 80_000_000):
                return None
            # Validate headers before NumPy can allocate from claimed shapes.
            for entry in entries:
                with archive.open(entry) as stream:
                    version = np.lib.format.read_magic(stream)
                    if version == (1, 0):
                        shape, _, dtype = np.lib.format.read_array_header_1_0(stream)
                    elif version == (2, 0):
                        shape, _, dtype = np.lib.format.read_array_header_2_0(stream)
                    else:
                        return None
                    if (len(shape) > 2 or any(d < 0 for d in shape) or dtype.hasobject
                            or math.prod(shape) * dtype.itemsize != entry.file_size - stream.tell()):
                        return None
        with np.load(path, allow_pickle=False) as saved:
            labels, centers = saved["labels"], saved["centers"]
            if (labels.ndim != 2 or not 0 < labels.size <= 4_000_000
                    or min(labels.shape) < 1 or labels.dtype.kind not in "iu"
                    or labels.min() < 0 or labels.max() >= k
                    or expected_shape is not None and labels.shape != expected_shape
                    or centers.shape != (k, 3) or centers.dtype.kind not in "fiu"
                    or not np.isfinite(centers).all() or centers.min() < 0 or centers.max() > 255):
                return None
            if not eyes:
                return labels, centers
            protection, boxes, count = saved["protection"], saved["boxes"], saved["face_count"]
            if (protection.shape != labels.shape or protection.dtype != np.bool_
                    or boxes.ndim != 2 or boxes.shape[1] != 4 or boxes.dtype.kind not in "iu"
                    or len(boxes) > 10000 or count.shape != () or count.dtype.kind not in "iu"
                    or not 0 <= int(count) <= 10000):
                return None
            h, w = labels.shape
            if len(boxes) and (np.any(boxes[:, :2] < 0) or np.any(boxes[:, 2:] <= 0)
                              or np.any(boxes[:, 0].astype(np.int64) + boxes[:, 2] > w)
                              or np.any(boxes[:, 1].astype(np.int64) + boxes[:, 3] > h)):
                return None
            expected = np.zeros(labels.shape, bool)
            for x, y, bw, bh in boxes:
                expected[y:y+bh, x:x+bw] = True
            if not np.array_equal(expected, protection):
                return None
            return labels, centers, protection, boxes.tolist(), int(count)
    except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile):
        return None
