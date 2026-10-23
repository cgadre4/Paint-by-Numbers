"""Offline, best-effort human eye protection using OpenCV's bundled cascades."""
from pathlib import Path

import cv2
import numpy as np


def detect_eyes(image):
    """Return processing-coordinate eye boxes and count of detected frontal faces.

    Detection is bounded to a 960px side and uses only the upper face. These are
    feature locations, not identities; profile/occluded/small faces may be missed.
    """
    height, width = image.shape[:2]
    factor = min(1.0, 960 / max(height, width))
    small = cv2.resize(image, (max(1, round(width*factor)), max(1, round(height*factor))),
                       interpolation=cv2.INTER_AREA) if factor < 1 else image
    gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
    root = Path(cv2.data.haarcascades)
    face = cv2.CascadeClassifier(str(root/'haarcascade_frontalface_default.xml'))
    eye = cv2.CascadeClassifier(str(root/'haarcascade_eye_tree_eyeglasses.xml'))
    if face.empty() or eye.empty():
        raise RuntimeError("The bundled eye detector is unavailable. Reinstall OpenCV or turn off eye protection.")
    faces = face.detectMultiScale(gray, 1.1, 5, minSize=(40, 40))
    boxes = []
    for x, y, w, h in faces:
        eyes = eye.detectMultiScale(gray[y:y+int(h*.65), x:x+w], 1.05, 4,
                                   minSize=(max(8, w//10), max(8, h//10)),
                                   maxSize=(max(8, w//2), max(8, h//2)))
        for ex, ey, ew, eh in eyes:
            left, top = max(0, int((x+ex)/factor)), max(0, int((y+ey)/factor))
            right = min(width, int(np.ceil((x+ex+ew)/factor)))
            bottom = min(height, int(np.ceil((y+ey+eh)/factor)))
            if right > left and bottom > top:
                boxes.append([left, top, right-left, bottom-top])
    return sorted(boxes), len(faces)


def eye_mask(shape, boxes):
    mask = np.zeros(shape[:2], dtype=bool)
    for x, y, w, h in boxes:
        mask[max(0, y):min(shape[0], y+h), max(0, x):min(shape[1], x+w)] = True
    return mask


def preserve_eye_pixels(source, filtered, mask):
    """Use mild edge-preserving filtering inside eyes, avoiding raw sensor speckle."""
    if not mask.any():
        return filtered
    result = filtered.copy()
    gentle = cv2.bilateralFilter(source, 3, 25, 25)
    result[mask] = gentle[mask]
    return result
