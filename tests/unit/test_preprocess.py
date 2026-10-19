import cv2
import numpy as np

from modules.ocr.preprocess import PRESETS, PreprocessConfig, estimate_skew_deg, preprocess, rotate


def _text_page(w=900, h=1200) -> np.ndarray:
    img = np.full((h, w, 3), 255, np.uint8)
    for i, y in enumerate(range(150, h - 150, 55)):
        cv2.putText(img, f"Line {i} Description 12345.67 Qty {i}", (80, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2, cv2.LINE_AA)
    return img


def test_skew_estimate_recovers_known_rotation():
    page = _text_page()
    for true in (-4.0, -1.5, 2.0, 3.5):
        skewed = rotate(page, true)
        est = estimate_skew_deg(cv2.cvtColor(skewed, cv2.COLOR_BGR2GRAY))
        assert abs(est + true) <= 0.75, (true, est)     # estimate is the corrective angle


def test_straight_page_not_rotated():
    out, log = preprocess(_text_page(), PreprocessConfig(upscale_min_width=0))
    assert any("deskew skipped" in s for s in log)
    assert out.ndim == 2


def test_deskew_reduces_skew():
    skewed = rotate(_text_page(), 3.0)
    fixed, _ = preprocess(skewed, PreprocessConfig(upscale_min_width=0))
    assert abs(estimate_skew_deg(fixed)) < 0.75


def test_upscale_and_log():
    small = _text_page(w=800, h=1000)
    out, log = preprocess(small, PRESETS["default"])
    assert out.shape[1] >= 1600 and any(s.startswith("upscale") for s in log)


def test_none_preset_is_identity():
    page = _text_page()
    out, log = preprocess(page, PRESETS["none"])
    assert log == [] and np.array_equal(out, page)


def test_full_preset_binarises():
    out, log = preprocess(_text_page(), PRESETS["full"])
    assert set(np.unique(out)) <= {0, 255} and "adaptive_threshold" in log
