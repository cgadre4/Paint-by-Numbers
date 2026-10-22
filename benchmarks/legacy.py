"""Frozen v1.1 cleanup reference; used only by the benchmark, never the app."""
import cv2
import numpy as np


def legacy_cleanup_regions(labels, k, area_min):
    cleaned = labels.copy()
    for color_idx in range(k):
        mask = (cleaned == color_idx).astype(np.uint8)
        count, components, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        for component in range(1, count):
            if stats[component, cv2.CC_STAT_AREA] >= area_min:
                continue
            small = components == component
            dilated = cv2.dilate(small.astype(np.uint8), np.ones((5, 5), np.uint8), iterations=2)
            neighbors = cleaned[(dilated > 0) & ~small]
            neighbors = neighbors[neighbors != color_idx]
            if neighbors.size:
                cleaned[small] = np.bincount(neighbors).argmax()
    return cleaned
