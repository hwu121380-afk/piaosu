"""可选本地离线 OCR 降级通道（基于 rapidocr-onnxruntime）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_OCR_ENGINE: Any = None


def is_ocr_available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


def _get_engine():
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR

        _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


def ocr_image_path(path: Path) -> tuple[str, float]:
    """返回 (识别文本, 平均置信度)。未安装 rapidocr-onnxruntime 时抛出 RuntimeError。"""
    if not is_ocr_available():
        raise RuntimeError(
            "当前为纯图片/扫描件，但未安装可选本地 OCR 依赖。"
            "请运行 pip install rapidocr-onnxruntime Pillow 启用纯离线 OCR。"
        )
    engine = _get_engine()
    result, _ = engine(str(path))
    if not result:
        return "", 0.0
    lines = [item[1] for item in result if len(item) >= 3]
    scores = [float(item[2]) for item in result if len(item) >= 3]
    avg_conf = sum(scores) / len(scores) if scores else 0.0
    return "\n".join(lines), round(avg_conf, 4)


def ocr_image_bytes(image_bytes: bytes) -> tuple[str, float]:
    if not is_ocr_available():
        raise RuntimeError(
            "当前为扫描件 PDF，但未安装可选本地 OCR 依赖。"
            "请运行 pip install rapidocr-onnxruntime Pillow 启用纯离线 OCR。"
        )
    engine = _get_engine()
    result, _ = engine(image_bytes)
    if not result:
        return "", 0.0
    lines = [item[1] for item in result if len(item) >= 3]
    scores = [float(item[2]) for item in result if len(item) >= 3]
    avg_conf = sum(scores) / len(scores) if scores else 0.0
    return "\n".join(lines), round(avg_conf, 4)
