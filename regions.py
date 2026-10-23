"""Connected-region cleanup using shared edges and actual label-fit checks."""
import heapq
from functools import lru_cache

import cv2
import numpy as np

# Euclidean RGB distance normalized by the RGB cube diagonal. This conservative
# guard applies only to extra label-fit merges, not the user-selected area cutoff.
MAX_NUMBERLESS_COLOR_DISTANCE = 0.12
STRONG_BOUNDARY_DISTANCE = 0.2


@lru_cache(maxsize=1024)
def label_glyph(color_idx, scale):
    """Tight antialiased digit bitmap; include every nonzero edge pixel."""
    text = str(color_idx + 1)
    (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    glyph = np.zeros((th + baseline + 8, tw + 8), np.uint8)
    cv2.putText(glyph, text, (4, th + 4), cv2.FONT_HERSHEY_SIMPLEX,
                scale, 255, 1, cv2.LINE_AA)
    x, y, w, h = cv2.boundingRect(glyph)
    glyph = glyph[y:y+h, x:x+w].copy()
    glyph.flags.writeable = False
    return glyph


def label_dimensions(color_idx, stroke_width, scale):
    glyph = label_glyph(int(color_idx), float(scale))
    height, width = glyph.shape
    padding = stroke_width + 1
    return width + 2 * padding, height + 2 * padding, height, padding


def label_fits(region, color_idx, stroke_width, font_scale):
    """Use the same minimum font size and complete padded box as the renderer."""
    bw, bh, _, _ = label_dimensions(color_idx, stroke_width, min(font_scale, 0.3))
    if region.shape[0] < bh or region.shape[1] < bw or np.count_nonzero(region) < bw * bh:
        return False
    return bool(cv2.erode(region, np.ones((bh, bw), np.uint8),
                          borderType=cv2.BORDER_CONSTANT, borderValue=0).any())


def component_map(labels, k):
    """Four-connected components: a diagonal touch is not a paintable connection."""
    regions = np.empty(labels.shape, np.int32)
    statistics, colors = [], []
    offset = 0
    for color in range(k):
        mask = (labels == color).astype(np.uint8)
        count, components, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=4)
        if count == 1:
            continue
        selected = mask.astype(bool)
        regions[selected] = components[selected] - 1 + offset
        statistics.append(stats[1:])
        colors.extend([color] * (count - 1))
        offset += count - 1
    return regions, np.concatenate(statistics), np.asarray(colors, dtype=np.int32)


def cleanup_regions(labels, k, area_min, centers=None, stroke_width=1, font_scale=0.5):
    """Merge smallest regions first, only across a shared pixel edge.

    Regions below area_min always qualify. Additional unnumberable regions qualify
    only within a bounded area and when a similar-colored neighbor is available.
    Each merge removes a graph node, so cleanup cannot oscillate between colors.
    The input and palette IDs are preserved; unused palette entries are allowed.
    """
    regions, stats, colors = component_map(labels, k)
    count = len(stats)
    adjacency = [{} for _ in range(count)]
    # Group horizontal/vertical contacts once, rather than dilating a full image
    # for every speck. Encoded pairs keep temporary storage linear in pixel count.
    pairs = []
    for a, b in ((regions[:, :-1], regions[:, 1:]), (regions[:-1], regions[1:])):
        different = a != b
        left, right = a[different].astype(np.int64), b[different].astype(np.int64)
        pairs.append(np.minimum(left, right) * count + np.maximum(left, right))
    edges, lengths = np.unique(np.concatenate(pairs), return_counts=True)
    for encoded, length in zip(edges, lengths):
        a, b = divmod(int(encoded), count)
        adjacency[a][b] = adjacency[b][a] = int(length)

    areas = stats[:, cv2.CC_STAT_AREA].astype(np.int64)
    boxes = stats[:, :4].copy()
    versions = np.zeros(count, np.int64)
    queue = []
    distances = None
    if centers is not None:
        palette = np.asarray(centers, np.float32)
        distances = np.linalg.norm(palette[:, None] - palette[None, :], axis=2) / (255 * np.sqrt(3))
    limits = np.array([max(area_min * 4, np.prod(label_dimensions(c, stroke_width,
                        min(font_scale, 0.3))[:2]) * 4) for c in range(k)])

    def enqueue(node):
        if adjacency[node] and areas[node] <= limits[colors[node]]:
            heapq.heappush(queue, (int(areas[node]), node, int(versions[node])))

    def absorb(source, target):
        x, y, w, h = boxes[source]
        roi = regions[y:y+h, x:x+w]
        roi[roi == source] = target
        tx, ty, tw, th = boxes[target]
        left, top = min(x, tx), min(y, ty)
        boxes[target] = (left, top, max(x+w, tx+tw)-left, max(y+h, ty+th)-top)
        areas[target] += areas[source]
        areas[source] = 0
        adjacency[target].pop(source, None)
        for neighbor, length in adjacency[source].items():
            adjacency[neighbor].pop(source, None)
            if neighbor != target:
                combined = adjacency[target].get(neighbor, 0) + length
                adjacency[target][neighbor] = adjacency[neighbor][target] = combined
                versions[neighbor] += 1
                enqueue(neighbor)
        adjacency[source] = None
        versions[target] += 1

    for node in range(count):
        enqueue(node)
    while queue:
        _, node, version = heapq.heappop(queue)
        if adjacency[node] is None or version != versions[node] or not adjacency[node]:
            continue
        small = areas[node] < area_min
        if not small:
            x, y, w, h = boxes[node]
            if label_fits((regions[y:y+h, x:x+w] == node).astype(np.uint8),
                          int(colors[node]), stroke_width, font_scale):
                continue
        total_boundary = sum(adjacency[node].values())
        candidates = []
        for neighbor, boundary in adjacency[node].items():
            # Extra fit merges stay anchored to larger regions. Below the explicit
            # area cutoff, allow an equally small, closer-colored neighbor: forcing a
            # larger target can erase details beside a contrasting background.
            if not small and (areas[neighbor], -neighbor) < (areas[node], -node):
                continue
            distance = float(distances[colors[node], colors[neighbor]]) if distances is not None else 0.0
            if not small and (distances is None or distance > MAX_NUMBERLESS_COLOR_DISTANCE):
                continue
            # Do not remove an existing strong edge to a third region merely
            # to make space for a number. Explicit area_min merges still apply.
            if not small and any(
                distances[colors[node], colors[other]] >= STRONG_BOUNDARY_DISTANCE
                and distances[colors[neighbor], colors[other]] < STRONG_BOUNDARY_DISTANCE
                for other in adjacency[node]
            ):
                continue
            score = distance + 0.25 * (1 - boundary / total_boundary)
            candidates.append((score, -boundary, -int(areas[neighbor]), neighbor))
        if not candidates:
            continue
        target = min(candidates)[-1]
        absorb(node, target)
        # A new bridge may join multiple regions of the target color. Coalesce
        # them now so their area/fit is evaluated as one connected region.
        same_color = [n for n in adjacency[target] if colors[n] == colors[target]]
        for neighbor in same_color:
            absorb(neighbor, target)
        enqueue(target)
    return colors[regions].astype(labels.dtype, copy=False)
