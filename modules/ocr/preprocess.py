"""OpenCV preprocessing. Every step is optional, parameterised and logged, because
preprocessing can hurt clean images. The benchmark compares configurations (Section 9).
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class PreprocessConfig:
    grayscale: bool = True
    upscale_min_width: int = 1600     # upscale pages narrower than this (0 = off)
    deskew: bool = True
    max_skew_deg: float = 10.0
    denoise: bool = False             # median blur; helps salt-and-pepper, hurts thin fonts
    binarize: bool = False            # adaptive threshold; Tesseract binarises internally

    @classmethod
    def none(cls) -> PreprocessConfig:
        return cls(grayscale=False, upscale_min_width=0, deskew=False)

    @classmethod
    def full(cls) -> PreprocessConfig:
        return cls(denoise=True, binarize=True)


PRESETS = {"none": PreprocessConfig.none(), "default": PreprocessConfig(), "full": PreprocessConfig.full()}


def estimate_skew_deg(gray: np.ndarray, max_deg: float = 10.0) -> float:
    """Estimate page skew with a projection-profile search (convention-independent).

    Rotates a down-scaled ink mask over candidate angles and keeps the angle whose row-sum
    profile has the highest variance (text lines align => sharp peaks). Returns the angle
    in degrees to rotate the image by (counter-clockwise positive, as in cv2.getRotationMatrix2D)
    to make text horizontal.
    """
    h, w = gray.shape[:2]
    scale = 800.0 / max(h, w) if max(h, w) > 800 else 1.0
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    _, ink = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if cv2.countNonZero(ink) < 50:
        return 0.0
    sh, sw = ink.shape
    centre = (sw / 2, sh / 2)

    def score(angle: float) -> float:
        m = cv2.getRotationMatrix2D(centre, angle, 1.0)
        rot = cv2.warpAffine(ink, m, (sw, sh), flags=cv2.INTER_NEAREST)
        return float(np.var(rot.sum(axis=1, dtype=np.float64)))

    coarse = np.arange(-max_deg, max_deg + 1e-9, 1.0)
    best = max(coarse, key=score)
    fine = np.arange(best - 1.0, best + 1.0 + 1e-9, 0.25)
    best = max(fine, key=score)
    return float(best)


def rotate(img: np.ndarray, angle: float) -> np.ndarray:
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    border = 255 if img.ndim == 2 else (255, 255, 255)
    return cv2.warpAffine(
        img, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=border
    )


def preprocess(img: np.ndarray, cfg: PreprocessConfig = PreprocessConfig()) -> tuple[np.ndarray, list[str]]:
    """Apply configured steps. Returns the processed image and a log of what was done."""
    log: list[str] = []
    out = img
    if cfg.upscale_min_width and out.shape[1] < cfg.upscale_min_width:
        factor = cfg.upscale_min_width / out.shape[1]
        out = cv2.resize(out, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)
        log.append(f"upscale x{factor:.2f}")
    if cfg.grayscale and out.ndim == 3:
        out = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
        log.append("grayscale")
    if cfg.deskew:
        gray = out if out.ndim == 2 else cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
        angle = estimate_skew_deg(gray, cfg.max_skew_deg)
        if abs(angle) >= 0.3:
            out = rotate(out, angle)
            log.append(f"deskew {angle:+.2f}deg")
        else:
            log.append("deskew skipped (<0.3deg)")
    if cfg.denoise:
        out = cv2.medianBlur(out, 3)
        log.append("median3")
    if cfg.binarize:
        gray = out if out.ndim == 2 else cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
        out = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15
        )
        log.append("adaptive_threshold")
    return out, log
