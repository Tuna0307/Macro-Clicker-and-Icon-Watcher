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

    MIN_ACCEPT_CONFIDENCE = 0.70
    STRONG_ACCEPT_CONFIDENCE = 0.90

    def __init__(self):
        self._engine = None
        self._recognition_engine = None
        self._engine_name = "paddleocr"
        self._ocr_init_error = None
        self._recognition_init_error = None
        self._lock = threading.Lock()
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))

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
                if self._prime_recognition_engine(recognition_engine):
                    return True
                self._recognition_engine = None

            # The full OCR pipeline is a heavier fallback. Only load it during
            # warm-up when text-only recognition is unavailable; otherwise it
            # remains lazy until a crop genuinely needs it.
            engine = self._get_engine()
            if engine is not None:
                if self._prime_full_engine(engine):
                    return True
                self._engine = None
            return False

    def _read_level_locked(self, frame) -> LevelOcrResult:
        if (
            frame is None
            or not isinstance(frame, np.ndarray)
            or frame.size == 0
            or frame.ndim not in (2, 3)
        ):
            return LevelOcrResult(None, engine=self._engine_name, error="empty frame")

        variants = self._preprocess_variants(frame)
        best = LevelOcrResult(None, engine="paddleocr_rec")
        strong_levels = {}
        for image in variants:
            result = self._run_text_recognition(image)
            best = self._better_result(best, result)
            if self._is_strong_result(result):
                prior = strong_levels.get(result.level)
                if prior is not None:
                    return self._better_result(prior, result)
                strong_levels[result.level] = result

        if self._is_acceptable_result(best) and self._has_level_prefix(best.text):
            return best

        engine = self._get_engine()
        if engine is None:
            if self._is_acceptable_result(best):
                return best
            return LevelOcrResult(None, best.text, best.confidence, best.engine, self.init_error)

        for image in variants:
            result = self._run_paddleocr(engine, image)
            best = self._better_result(best, result)
            if self._is_strong_result(result):
                prior = strong_levels.get(result.level)
                if prior is not None:
                    return self._better_result(prior, result)
                strong_levels[result.level] = result
        if self._is_acceptable_result(best):
            return best
        error = best.error
        if best.level is not None and not error:
            confidence = "unknown" if best.confidence is None else f"{best.confidence:.2f}"
            error = f"OCR confidence {confidence} is below the safety threshold"
        return LevelOcrResult(None, best.text, best.confidence, best.engine, error)

    def _has_level_prefix(self, text):
        normalized = (text or "").lower().replace(" ", "")
        normalized = normalized.replace("lⅴ", "lv").replace("1v", "lv").replace("iv", "lv").replace("ly", "lv")
        return bool(re.search(r"(?:lv|level|^l)[^\d]*\d", normalized))

    def _result_rank(self, result):
        has_level = result is not None and result.level is not None
        prefixed = has_level and self._has_level_prefix(result.text)
        confidence = -1.0 if result is None or result.confidence is None else result.confidence
        acceptable = has_level and confidence >= self.MIN_ACCEPT_CONFIDENCE
        text_length = 0 if result is None else len(result.text or "")
        return (
            has_level,
            acceptable,
            acceptable and prefixed,
            confidence,
            prefixed,
            text_length,
        )

    def _better_result(self, current, candidate):
        if candidate is None:
            return current
        return candidate if self._result_rank(candidate) > self._result_rank(current) else current

    def _is_acceptable_result(self, result):
        return (
            result is not None
            and result.level is not None
            and result.confidence is not None
            and result.confidence >= self.MIN_ACCEPT_CONFIDENCE
        )

    def _is_strong_result(self, result):
        return (
            self._is_acceptable_result(result)
            and self._has_level_prefix(result.text)
            and result.confidence >= self.STRONG_ACCEPT_CONFIDENCE
        )

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
        last_error: Optional[Exception] = None
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
        last_error: Optional[Exception] = None
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
        clahe = self._clahe.apply(gray)
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
        return self._result_from_entries(entries, self._engine_name)

    def _run_text_recognition(self, image) -> LevelOcrResult:
        engine = self._get_recognition_engine()
        if engine is None:
            return LevelOcrResult(None, engine="paddleocr_rec", error=self._recognition_init_error)
        try:
            raw = engine.predict(image)
        except Exception as exc:
            return LevelOcrResult(None, engine="paddleocr_rec", error=str(exc))

        entries = list(self._extract_text_entries(raw))
        return self._result_from_entries(entries, "paddleocr_rec")

    def _result_from_entries(self, entries, engine_name):
        clean_entries = [
            (str(text).strip(), self._coerce_score(score))
            for text, score in entries
            if str(text or "").strip()
        ]
        joined_text = " ".join(text for text, _score in clean_entries).strip()
        candidates = []
        for order, (text, score) in enumerate(clean_entries):
            level = self._extract_level(text)
            if level is None:
                continue
            numeric_score = score if score is not None else -1.0
            acceptable = numeric_score >= self.MIN_ACCEPT_CONFIDENCE
            prefixed = self._has_level_prefix(text)
            candidates.append(
                (
                    acceptable,
                    acceptable and prefixed,
                    numeric_score,
                    prefixed,
                    -order,
                    level,
                    text,
                    score,
                )
            )

        if candidates:
            (
                _acceptable,
                _safe_prefix,
                _rank_score,
                _prefixed,
                _order,
                level,
                source_text,
                confidence,
            ) = max(candidates)
            return LevelOcrResult(
                level,
                text=source_text,
                confidence=confidence,
                engine=engine_name,
            )

        level = self._extract_level(joined_text)
        digit_scores = [
            score
            for text, score in clean_entries
            if score is not None and re.search(r"\d", text)
        ]
        # Joined text can span multiple OCR boxes, so use the weakest involved
        # digit-bearing score rather than borrowing confidence from unrelated text.
        confidence = min(digit_scores) if digit_scores else None
        return LevelOcrResult(level, text=joined_text, confidence=confidence, engine=engine_name)

    def _prime_recognition_engine(self, engine):
        try:
            engine.predict(np.zeros((64, 160, 3), dtype=np.uint8))
            return True
        except Exception as exc:
            self._recognition_init_error = f"PaddleOCR TextRecognition warm-up failed: {exc}"
            return False

    def _prime_full_engine(self, engine):
        try:
            self._predict(engine, np.zeros((64, 160, 3), dtype=np.uint8))
            return True
        except Exception as exc:
            self._ocr_init_error = f"PaddleOCR warm-up failed: {exc}"
            return False

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

    def _extract_text_entries(self, raw, _depth=0, _seen=None):
        if raw is None:
            return
        if _depth > 8:
            return
        if _seen is None:
            _seen = set()
        if isinstance(raw, (dict, list, tuple, np.ndarray)) or hasattr(raw, "json") or hasattr(raw, "res"):
            raw_id = id(raw)
            if raw_id in _seen:
                return
            _seen.add(raw_id)

        if isinstance(raw, dict):
            if "rec_text" in raw:
                yield str(raw.get("rec_text") or ""), self._coerce_score(raw.get("rec_score"))
            texts = raw.get("rec_texts")
            if texts is None:
                texts = raw.get("texts")
            scores = raw.get("rec_scores")
            if scores is None:
                scores = raw.get("scores")
            if texts is not None:
                texts = texts.tolist() if isinstance(texts, np.ndarray) else texts
                scores = scores.tolist() if isinstance(scores, np.ndarray) else (scores or [])
                if isinstance(texts, (list, tuple)):
                    for i, text in enumerate(texts):
                        score = scores[i] if isinstance(scores, (list, tuple)) and i < len(scores) else None
                        yield str(text), self._coerce_score(score)
            handled_keys = {"rec_text", "rec_score", "rec_texts", "texts", "rec_scores", "scores"}
            for key, value in raw.items():
                if key in handled_keys:
                    continue
                yield from self._extract_text_entries(value, _depth + 1, _seen)
            return

        if isinstance(raw, np.ndarray):
            yield from self._extract_text_entries(raw.tolist(), _depth + 1, _seen)
            return

        if isinstance(raw, (list, tuple)):
            is_text_score_pair = (
                len(raw) == 2
                and isinstance(raw[0], str)
                and (raw[1] is None or isinstance(raw[1], (int, float, np.number)))
            )
            if is_text_score_pair:
                yield raw[0], self._coerce_score(raw[1])
                return
            if len(raw) == 2 and isinstance(raw[1], (list, tuple)) and raw[1] and isinstance(raw[1][0], str):
                score = raw[1][1] if len(raw[1]) > 1 else None
                yield raw[1][0], self._coerce_score(score)
                return
            for item in raw:
                yield from self._extract_text_entries(item, _depth + 1, _seen)
            return

        if hasattr(raw, "json"):
            try:
                value = raw.json
                if callable(value):
                    value = value()
                yield from self._extract_text_entries(value, _depth + 1, _seen)
            except Exception:
                pass
        if hasattr(raw, "res"):
            try:
                value = raw.res
                if callable(value):
                    value = value()
                yield from self._extract_text_entries(value, _depth + 1, _seen)
            except Exception:
                pass

    def _extract_level(self, text):
        if not text:
            return None
        normalized = text.lower().replace(" ", "")
        normalized = (
            normalized.replace("lⅴ", "lv")
            .replace("1v", "lv")
            .replace("iv", "lv")
            .replace("ly", "lv")
        )

        for pattern in (r"lv([^\d]*)(\d{1,4})", r"level([^\d]*)(\d{1,4})", r"^l([^\d]*)(\d{1,4})"):
            match = re.search(pattern, normalized)
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
            likely_out_of_game_range = value > 300
            if (
                1 <= first_two <= 150
                and likely_out_of_game_range
            ):
                return first_two
        return int(digits)

    def _coerce_score(self, score):
        if score is None:
            return None
        try:
            value = float(score)
        except (TypeError, ValueError, OverflowError):
            return None
        if not np.isfinite(value) or not 0.0 <= value <= 1.0:
            return None
        return value
