"""Procedural image fixtures, generated locally with a fixed seed (not photos)."""
import random

import cv2
import numpy as np


def synthetic_cases():
    rng = random.Random(1729)
    h, w = 256, 384
    flat = np.full((h, w, 3), (235, 235, 235), np.uint8)
    cv2.rectangle(flat, (18, 25), (155, 224), (80, 160, 220), -1)
    cv2.circle(flat, (270, 124), 91, (190, 100, 65), -1)
    cv2.circle(flat, (270, 124), 38, (235, 235, 235), -1)
    yield 'flat_shapes', flat

    landscape = np.zeros((h, w, 3), np.uint8)
    landscape[:] = np.linspace((230, 180, 110), (200, 210, 215), h)[:, None, :]
    cv2.circle(landscape, (290, 54), 27, (130, 225, 250), -1)
    cv2.fillPoly(landscape, [np.array([[0, 170], [85, 60], [180, 170], [270, 90], [383, 175], [383, 255], [0, 255]])], (95, 125, 110))
    cv2.ellipse(landscape, (190, 270), (260, 100), 0, 180, 360, (80, 145, 85), -1)
    landscape = np.clip(landscape.astype(float) + np.fromiter((rng.gauss(0, 3) for _ in range(landscape.size)), dtype=float).reshape(landscape.shape), 0, 255).astype(np.uint8)
    yield 'landscape_gradient', landscape

    portrait = np.full((h, w, 3), (190, 195, 210), np.uint8)
    cv2.ellipse(portrait, (192, 120), (78, 110), 0, 0, 360, (45, 55, 70), -1)
    cv2.ellipse(portrait, (192, 134), (60, 85), 0, 0, 360, (140, 180, 220), -1)
    cv2.ellipse(portrait, (165, 116), (12, 6), 0, 0, 360, (35, 40, 45), -1)
    cv2.ellipse(portrait, (219, 116), (12, 6), 0, 0, 360, (35, 40, 45), -1)
    cv2.line(portrait, (177, 175), (208, 175), (85, 90, 150), 4)
    cv2.line(portrait, (191, 130), (184, 155), (115, 150, 185), 3)
    yield 'portrait_details', portrait

    foliage = np.full((h, w, 3), (75, 105, 70), np.uint8)
    for _ in range(180):
        center = (int(rng.randrange(w)), int(rng.randrange(h)))
        color = tuple(rng.randrange(a, b) for a, b in zip((30, 75, 25), (110, 180, 125)))
        cv2.ellipse(foliage, center, (int(rng.randrange(3, 18)), int(rng.randrange(2, 9))),
                    int(rng.randrange(180)), 0, 360, color, -1)
    yield 'textured_foliage', foliage

    speckles = flat.copy()
    for _ in range(350):
        x, y = int(rng.randrange(w)), int(rng.randrange(h))
        cv2.circle(speckles, (x, y), int(rng.randrange(1, 4)), (55, 85, 125), -1)
    yield 'speckled_shapes', speckles

    lines = np.full((h, w, 3), (225, 225, 225), np.uint8)
    for i in range(12):
        color = (210, 215, 220) if i % 2 else (25, 30, 35)
        cv2.line(lines, (15, 15+i*20), (365, 15+i*20), color, 3 if i % 2 else 5)
    yield 'thin_contrast_lines', lines
