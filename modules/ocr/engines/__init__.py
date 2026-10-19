from __future__ import annotations

from .base import OcrEngine, OcrPage, OcrWord


def get_engine(name: str) -> OcrEngine:
    """Factory used by the CLI and benchmark. Optional engines import lazily."""
    name = name.lower()
    if name.startswith("tesseract"):
        from .tesseract_engine import TesseractEngine

        psm = int(name.split("psm")[1]) if "psm" in name else 6
        return TesseractEngine(psm=psm)
    if name in {"paddle", "paddleocr"}:
        from .paddle_engine import PaddleEngine

        return PaddleEngine()
    if name in {"vision", "apple_vision"}:
        from .vision_engine import VisionEngine

        return VisionEngine()
    raise ValueError(f"Unknown OCR engine: {name!r} (try tesseract, tesseract_psm4, paddle, vision)")


__all__ = ["OcrEngine", "OcrPage", "OcrWord", "get_engine"]
