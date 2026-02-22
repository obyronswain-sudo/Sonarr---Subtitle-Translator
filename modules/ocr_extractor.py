"""
OCR Extractor — PGS (Blu-ray) and VobSub image-based subtitle extraction.

Pipeline:
  MKV with PGS → mkvextract (.sup) → SUP parser → OpenCV pre-processing
  → Tesseract OCR → .srt output

Requirements (optional — installed via requirements-full.txt):
  pytesseract >= 0.3.10
  Pillow >= 10.0.0
  opencv-python >= 4.8.0
  tesseract-ocr (system binary)
"""
from __future__ import annotations

import os
import struct
import subprocess
from pathlib import Path
from typing import Optional


def _check_dependencies() -> tuple[bool, str]:
    """Return (available, reason) for OCR dependencies."""
    try:
        import pytesseract
        import cv2  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError as e:
        return False, f"Missing Python package: {e}"

    try:
        import pytesseract
        pytesseract.get_tesseract_version()
    except Exception as e:
        return False, f"Tesseract binary not found or not working: {e}"

    return True, "OK"


def is_ocr_available() -> bool:
    ok, _ = _check_dependencies()
    return ok


# ─── SUP (PGS) Parser ────────────────────────────────────────────────────────

_PGS_MAGIC = b"PG"
_SEG_PDS = 0x14   # Palette Definition Segment
_SEG_ODS = 0x15   # Object Definition Segment
_SEG_PCS = 0x16   # Presentation Composition Segment
_SEG_WDS = 0x17   # Window Definition Segment
_SEG_END = 0x80   # End of Display Set


class _PGSFrame:
    """One display frame from a PGS .sup file."""
    __slots__ = ("pts_ms", "width", "height", "rle_data", "palette")

    def __init__(self):
        self.pts_ms: int = 0
        self.width: int = 0
        self.height: int = 0
        self.rle_data: bytes = b""
        self.palette: dict[int, tuple[int, int, int, int]] = {}  # idx → (R,G,B,A)


def _yuv_to_rgb(y: int, cb: int, cr: int) -> tuple[int, int, int]:
    r = int(y + 1.402 * (cr - 128))
    g = int(y - 0.344136 * (cb - 128) - 0.714136 * (cr - 128))
    b = int(y + 1.772 * (cb - 128))
    return max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))


def _decode_rle(data: bytes, width: int, height: int) -> list[list[int]]:
    """Decode PGS RLE to a 2-D list of palette indices."""
    pixels = []
    row: list[int] = []
    i = 0
    while i < len(data):
        b = data[i]; i += 1
        if b != 0:
            row.append(b)
        else:
            if i >= len(data):
                break
            flags = data[i]; i += 1
            if flags == 0:
                pixels.append(row[:width])
                row = []
            elif flags & 0xC0 == 0x40:
                count = ((flags & 0x3F) << 8) | data[i]; i += 1
                row.extend([0] * count)
            elif flags & 0xC0 == 0x80:
                count = flags & 0x3F
                color = data[i]; i += 1
                row.extend([color] * count)
            elif flags & 0xC0 == 0xC0:
                count = ((flags & 0x3F) << 8) | data[i]; i += 1
                color = data[i]; i += 1
                row.extend([color] * count)
            else:
                row.extend([0] * (flags & 0x3F))
    if row:
        pixels.append(row[:width])
    while len(pixels) < height:
        pixels.append([0] * width)
    return pixels


def _parse_sup(sup_path: str) -> list[_PGSFrame]:
    """Parse a .sup file and return a list of PGS frames."""
    frames: list[_PGSFrame] = []
    current: Optional[_PGSFrame] = None
    rle_buf = bytearray()
    obj_first = True

    with open(sup_path, "rb") as f:
        data = f.read()

    pos = 0
    while pos + 13 <= len(data):
        magic = data[pos:pos+2]
        if magic != _PGS_MAGIC:
            pos += 1
            continue

        pts_raw = struct.unpack_from(">I", data, pos + 2)[0]
        pts_ms = pts_raw // 90  # 90 kHz clock → ms
        seg_type = data[pos + 10]
        seg_len = struct.unpack_from(">H", data, pos + 11)[0]
        seg_data = data[pos + 13: pos + 13 + seg_len]
        pos += 13 + seg_len

        if seg_type == _SEG_PCS:
            current = _PGSFrame()
            current.pts_ms = pts_ms
            if len(seg_data) >= 4:
                current.width = struct.unpack_from(">H", seg_data, 0)[0]
                current.height = struct.unpack_from(">H", seg_data, 2)[0]
            rle_buf = bytearray()
            obj_first = True

        elif seg_type == _SEG_PDS and current is not None:
            i = 2
            while i + 4 < len(seg_data):
                idx = seg_data[i]
                y = seg_data[i+1]; cb = seg_data[i+2]; cr = seg_data[i+3]; alpha = seg_data[i+4]
                r, g, b = _yuv_to_rgb(y, cb, cr)
                current.palette[idx] = (r, g, b, alpha)
                i += 5

        elif seg_type == _SEG_ODS and current is not None:
            if len(seg_data) < 7:
                continue
            flags = seg_data[3]
            if flags & 0x80:  # first in sequence
                obj_w = struct.unpack_from(">H", seg_data, 7)[0] if len(seg_data) > 8 else 0
                obj_h = struct.unpack_from(">H", seg_data, 9)[0] if len(seg_data) > 10 else 0
                if obj_w:
                    current.width = obj_w
                if obj_h:
                    current.height = obj_h
                rle_buf = bytearray(seg_data[11:])
                obj_first = False
            else:
                rle_buf.extend(seg_data[4:])

        elif seg_type == _SEG_END and current is not None:
            if rle_buf and current.width and current.height:
                current.rle_data = bytes(rle_buf)
                frames.append(current)
            current = None

    return frames


def _frame_to_pil(frame: _PGSFrame):
    """Convert a PGSFrame to a PIL RGBA image."""
    from PIL import Image
    pixels = _decode_rle(frame.rle_data, frame.width, frame.height)
    img = Image.new("RGBA", (frame.width, frame.height), (0, 0, 0, 0))
    for y, row in enumerate(pixels):
        for x, idx in enumerate(row):
            color = frame.palette.get(idx, (0, 0, 0, 0))
            img.putpixel((x, y), color)
    return img


def _preprocess_for_ocr(pil_img):
    """Apply OpenCV pre-processing to improve OCR accuracy."""
    import cv2
    import numpy as np
    from PIL import Image

    rgba = np.array(pil_img.convert("RGBA"))
    alpha = rgba[:, :, 3]
    rgb = rgba[:, :, :3]

    # White background composite
    bg = np.full_like(rgb, 255)
    mask = alpha[:, :, np.newaxis] / 255.0
    composited = (rgb * mask + bg * (1 - mask)).astype(np.uint8)

    gray = cv2.cvtColor(composited, cv2.COLOR_RGB2GRAY)

    # Adaptive threshold
    binary = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        11, 2,
    )

    # Morphological cleanup
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    cleaned = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    # Scale up for better OCR
    h, w = cleaned.shape
    scale = max(1.0, 800 / max(w, 1))
    if scale > 1.0:
        cleaned = cv2.resize(cleaned, (int(w * scale), int(h * scale)),
                             interpolation=cv2.INTER_CUBIC)

    # Invert back to black-on-white
    result = cv2.bitwise_not(cleaned)
    return Image.fromarray(result)


# ─── Main OCR Extractor ───────────────────────────────────────────────────────

class PGSOCRExtractor:
    """Extracts text from PGS (.sup) subtitle files using OCR."""

    def __init__(self, logger=None, tesseract_lang: str = "eng"):
        self.logger = logger
        self.tesseract_lang = tesseract_lang

    def _log(self, level: str, msg: str):
        if self.logger:
            self.logger.log(level, msg)

    def extract_pgs_to_srt(
        self,
        sup_file: str,
        output_srt: str,
        progress_callback=None,
    ) -> bool:
        """
        Convert a .sup PGS file to a .srt subtitle file via OCR.

        Returns True on success, False on failure.
        """
        ok, reason = _check_dependencies()
        if not ok:
            self._log("error", f"OCR unavailable: {reason}")
            return False

        import pytesseract

        self._log("info", f"Starting PGS OCR: {sup_file}")
        try:
            frames = _parse_sup(sup_file)
        except Exception as e:
            self._log("error", f"Failed to parse SUP file: {e}")
            return False

        if not frames:
            self._log("warning", "No frames found in SUP file")
            return False

        self._log("info", f"Parsed {len(frames)} PGS frames")

        entries = []
        for i, frame in enumerate(frames):
            if progress_callback:
                progress_callback(int(i / len(frames) * 100))

            try:
                pil_img = _frame_to_pil(frame)
                if pil_img.size[0] < 10 or pil_img.size[1] < 5:
                    continue

                processed = _preprocess_for_ocr(pil_img)
                text = pytesseract.image_to_string(
                    processed,
                    lang=self.tesseract_lang,
                    config="--psm 6 --oem 3",
                ).strip()

                if not text:
                    continue

                # Determine end time: next frame start or +3s
                start_ms = frame.pts_ms
                end_ms = frames[i + 1].pts_ms if i + 1 < len(frames) else start_ms + 3000
                if end_ms <= start_ms:
                    end_ms = start_ms + 2000

                entries.append((start_ms, end_ms, text))

            except Exception as e:
                self._log("debug", f"Frame {i} OCR error: {e}")
                continue

        if not entries:
            self._log("warning", "OCR produced no text entries")
            return False

        self._write_srt(output_srt, entries)
        self._log("info", f"OCR complete: {len(entries)} entries → {output_srt}")
        return True

    @staticmethod
    def _ms_to_srt_time(ms: int) -> str:
        h = ms // 3_600_000
        ms %= 3_600_000
        m = ms // 60_000
        ms %= 60_000
        s = ms // 1_000
        ms %= 1_000
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def _write_srt(self, path: str, entries: list) -> None:
        lines = []
        for idx, (start_ms, end_ms, text) in enumerate(entries, 1):
            lines.append(str(idx))
            lines.append(f"{self._ms_to_srt_time(start_ms)} --> {self._ms_to_srt_time(end_ms)}")
            lines.append(text)
            lines.append("")
        Path(path).write_text("\n".join(lines), encoding="utf-8")

    def check_tesseract_lang(self, lang: str) -> bool:
        """Check if a Tesseract language pack is installed."""
        try:
            import pytesseract
            available = pytesseract.get_languages()
            return lang in available
        except Exception:
            return False
