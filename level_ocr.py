import os
import re
import threading
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


@dataclass
class LevelOcrResult:
    level: Optional[int]
    text: str = ""
    confidence: Optional[float] = None
    engine: str = "none"
    error: Optional[str] = None


class LevelOcrReader:
    """
    OCR reader for small game level crops.

    PaddleOCR is loaded lazily because it is heavy and downloads/initializes
    models on first use. The rest of the macro can still run if OCR is not
    installed; the engine will fall back to digit templates.
    """

    def __init__(self):
        self._engine = None
        self._recognition_engine = None
        self._engine_name = "paddleocr"
        self._ocr_init_error = None
        self._recognition_init_error = None
        self._lock = threading.Lock()

    @property
    def init_error(self):
        return self._recognition_init_error or self._ocr_init_error

    def read_level(self, frame) -> LevelOcrResult:
        with self._lock:
            return self._read_level_locked(frame)

    def warm_up(self) -> bool:
        with self._lock:
            recognition_engine = self._get_recognition_engine()
            if recognition_engine is not None:
                self._prime_recognition_engine(recognition_engine)
                return True
            engine = self._get_engine()
            if engine is not None:
                self._prime_full_engine(engine)
                return True
            return False

    def _read_level_locked(self, frame) -> LevelOcrResult:
        if frame is None:
            return LevelOcrResult(None, engine=self._engine_name, error="empty frame")

        best = LevelOcrResult(None, engine=self._engine_name)
        for image in self._preprocess_variants(frame):
            result = self._run_text_recognition(image)
            if result.level is not None:
                return result
            if result.text and not best.text:
                best = result

        engine = self._get_engine()
        if engine is None:
            return LevelOcrResult(best.level, best.text, best.confidence, best.engine, self.init_error)

        for image in self._preprocess_variants(frame):
            result = self._run_paddleocr(engine, image)
            if result.level is not None:
                return result
            if result.text and not best.text:
                best = result
        return best

    def _get_engine(self):
        if self._engine is not None:
            return self._engine
        if self._ocr_init_error:
            return None

        try:
            os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
            from paddleocr import PaddleOCR
        except Exception as exc:
            self._ocr_init_error = f"PaddleOCR import failed: {exc}"
            return None

        init_attempts = [
            {
                "lang": "en",
                "use_doc_orientation_classify": False,
                "use_doc_unwarping": False,
                "use_textline_orientation": False,
            },
            {"lang": "en", "use_angle_cls": False, "show_log": False},
            {"lang": "en"},
        ]
        last_error = None
        for kwargs in init_attempts:
            try:
                self._engine = PaddleOCR(**kwargs)
                return self._engine
            except TypeError as exc:
                last_error = exc
                continue
            except Exception as exc:
                last_error = exc
                continue
        self._ocr_init_error = f"PaddleOCR init failed: {last_error}"
        return None

    def _get_recognition_engine(self):
        if self._recognition_engine is not None:
            return self._recognition_engine
        if self._recognition_init_error:
            return None

        try:
            os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
            from paddleocr import TextRecognition
        except Exception as exc:
            self._recognition_init_error = f"PaddleOCR TextRecognition import failed: {exc}"
            return None

        init_attempts = [
            {"model_name": "PP-OCRv6_medium_rec"},
            {},
        ]
        last_error = None
        for kwargs in init_attempts:
            try:
                self._recognition_engine = TextRecognition(**kwargs)
                return self._recognition_engine
            except TypeError as exc:
                last_error = exc
                continue
            except Exception as exc:
                last_error = exc
                continue
        self._recognition_init_error = f"PaddleOCR TextRecognition init failed: {last_error}"
        return None

    def _preprocess_variants(self, frame):
        bgr = frame
        if len(frame.shape) == 2:
            bgr = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

        variants = []
        for region in self._level_text_regions(bgr):
            variants.extend(self._preprocess_single_region(region))
        return variants

    def _level_text_regions(self, bgr):
        height, width = bgr.shape[:2]
        if height <= 0 or width <= 0:
            return [bgr]

        boxes = [
            (0, max(0, round(height * 0.27)), width, height),
            (round(width * 0.13), max(0, round(height * 0.22)), round(width * 0.87), height),
            (round(width * 0.20), max(0, round(height * 0.30)), round(width * 0.77), max(1, round(height * 0.96))),
            (round(width * 0.42), max(0, round(height * 0.30)), round(width * 0.72), max(1, round(height * 0.96))),
            (0, 0, width, height),
        ]

        regions = []
        seen = set()
        for left, top, right, bottom in boxes:
            left = max(0, min(left, width - 1))
            top = max(0, min(top, height - 1))
            right = max(left + 1, min(right, width))
            bottom = max(top + 1, min(bottom, height))
            key = (left, top, right, bottom)
            if key in seen:
                continue
            seen.add(key)
            regions.append(bgr[top:bottom, left:right])
        return regions

    def _preprocess_single_region(self, bgr):
        scale = 4
        upscaled = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        sharpened = self._sharpen(upscaled)

        gray = cv2.cvtColor(upscaled, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4)).apply(gray)
        threshold = cv2.adaptiveThreshold(
            clahe,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            21,
            3,
        )
        threshold_bgr = cv2.cvtColor(threshold, cv2.COLOR_GRAY2BGR)

        return [upscaled, sharpened, threshold_bgr]

    def _sharpen(self, image):
        blurred = cv2.GaussianBlur(image, (0, 0), 1.0)
        return cv2.addWeighted(image, 1.6, blurred, -0.6, 0)

    def _run_paddleocr(self, engine, image) -> LevelOcrResult:
        try:
            raw = self._predict(engine, image)
        except Exception as exc:
            return LevelOcrResult(None, engine=self._engine_name, error=str(exc))

        entries = list(self._extract_text_entries(raw))
        text = " ".join(entry[0] for entry in entries if entry[0]).strip()
        confidence = max((entry[1] for entry in entries if entry[1] is not None), default=None)
        level = self._extract_level(text)
        return LevelOcrResult(level, text=text, confidence=confidence, engine=self._engine_name)

    def _run_text_recognition(self, image) -> LevelOcrResult:
        engine = self._get_recognition_engine()
        if engine is None:
            return LevelOcrResult(None, engine="paddleocr_rec", error=self._recognition_init_error)
        try:
            raw = engine.predict(image)
        except Exception as exc:
            return LevelOcrResult(None, engine="paddleocr_rec", error=str(exc))

        entries = list(self._extract_text_entries(raw))
        text = " ".join(entry[0] for entry in entries if entry[0]).strip()
        confidence = max((entry[1] for entry in entries if entry[1] is not None), default=None)
        level = self._extract_level(text)
        return LevelOcrResult(level, text=text, confidence=confidence, engine="paddleocr_rec")

    def _prime_recognition_engine(self, engine):
        try:
            engine.predict(np.zeros((64, 160, 3), dtype=np.uint8))
        except Exception:
            pass

    def _prime_full_engine(self, engine):
        try:
            self._predict(engine, np.zeros((64, 160, 3), dtype=np.uint8))
        except Exception:
            pass

    def _predict(self, engine, image):
        if hasattr(engine, "predict"):
            try:
                return engine.predict(input=image)
            except TypeError:
                return engine.predict(image)
        if hasattr(engine, "ocr"):
            try:
                return engine.ocr(image, cls=False)
            except TypeError:
                return engine.ocr(image)
        raise RuntimeError("PaddleOCR engine has no predict/ocr method")

    def _extract_text_entries(self, raw):
        if raw is None:
            return

        if isinstance(raw, dict):
            if "rec_text" in raw:
                yield str(raw.get("rec_text") or ""), self._coerce_score(raw.get("rec_score"))
            texts = raw.get("rec_texts") or raw.get("texts")
            scores = raw.get("rec_scores") or raw.get("scores") or []
            if texts:
                for i, text in enumerate(texts):
                    score = scores[i] if i < len(scores) else None
                    yield str(text), self._coerce_score(score)
            for value in raw.values():
                yield from self._extract_text_entries(value)
            return

        if isinstance(raw, (list, tuple)):
            if len(raw) == 2 and isinstance(raw[0], str):
                yield raw[0], self._coerce_score(raw[1])
                return
            if len(raw) == 2 and isinstance(raw[1], (list, tuple)) and raw[1] and isinstance(raw[1][0], str):
                score = raw[1][1] if len(raw[1]) > 1 else None
                yield raw[1][0], self._coerce_score(score)
                return
            for item in raw:
                yield from self._extract_text_entries(item)
            return

        if hasattr(raw, "json"):
            try:
                yield from self._extract_text_entries(raw.json)
            except Exception:
                pass
        if hasattr(raw, "res"):
            try:
                yield from self._extract_text_entries(raw.res)
            except Exception:
                pass

    def _extract_level(self, text):
        if not text:
            return None
        normalized = text.lower().replace(" ", "")
        normalized = normalized.replace("lⅴ", "lv").replace("1v", "lv").replace("iv", "lv")

        for pattern in (r"lv([^\d]*)(\d{1,4})", r"level([^\d]*)(\d{1,4})", r"^l([^\d]*)(\d{1,4})"):
            match = re.search(pattern, normalized, flags=re.IGNORECASE)
            if match:
                separator, digits = match.group(1), match.group(2)
                return self._normalize_level_digits(digits, lv_prefixed=True, separator=separator)

        numbers = re.findall(r"\d{1,3}", normalized)
        if not numbers:
            return None
        return int(numbers[-1])

    def _normalize_level_digits(self, digits, lv_prefixed=False, separator=""):
        if not digits:
            return None
        if lv_prefixed and len(digits) >= 3:
            value = int(digits)
            first_two = int(digits[:2])
            likely_noisy_prefix = "-" in separator
            likely_extra_trailing_zero = not separator and digits.endswith("0")
            likely_out_of_game_range = value > 300
            if (
                1 <= first_two <= 150
                and (likely_noisy_prefix or likely_extra_trailing_zero or likely_out_of_game_range)
            ):
                return first_two
        return int(digits)

    def _coerce_score(self, score):
        if score is None:
            return None
        try:
            return float(score)
        except (TypeError, ValueError):
            return None
