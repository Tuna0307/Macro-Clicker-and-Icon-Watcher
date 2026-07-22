import os
import re
import threading
from dataclasses import dataclass
from typing import Any, Optional

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
    models on first use. If OCR is unavailable, level-filtered rows fail safe
    and are skipped.
    """

    MIN_ACCEPT_CONFIDENCE = 0.70
    STRONG_ACCEPT_CONFIDENCE = 0.90
    _VARIANTS_PER_REGION = 3
    _FAST_REGION_INDEX = 1
    _LEVEL_PREFIX_PATTERN = re.compile(
        r"(?<![a-z0-9])(?:level|lv)([^a-z0-9]{0,4})(\d{1,4})"
    )
    _SINGLE_L_PREFIX_PATTERN = re.compile(
        r"(?<![a-z0-9])l([^a-z0-9]{1,3})(\d{1,4})"
    )
    _OCR_PREFIX_CORRECTION_PATTERN = re.compile(
        r"(?<![a-z0-9])(?:1v|iv|ly)(?=[^a-z0-9]{0,4}\d)"
    )
    _OCR_ROMAN_V_PREFIX_CORRECTION_PATTERN = re.compile(
        r"(?<![a-z0-9])l\u2174(?=[^a-z0-9]{0,4}\d)"
    )

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

        # The second level-text region's plain upscaled image was the fastest
        # reliable variant in the screenshot benchmark. Try it once before the
        # exhaustive path, but only trust a literal Lv/Level-prefixed strong
        # result. Anything less certain continues through every prior safety
        # check below.
        fast_image = self._preprocess_fast_variant(frame)
        fast_result = self._run_text_recognition(fast_image)
        if self._is_fast_path_result(fast_result):
            return fast_result

        # Build the more expensive sharpened and threshold variants only when
        # the fast result was not safe enough to accept.
        variants = self._preprocess_variants(frame)
        if not variants:
            return LevelOcrResult(None, engine="paddleocr_rec", error="no OCR variants")
        fast_index = min(
            self._FAST_REGION_INDEX * self._VARIANTS_PER_REGION,
            len(variants) - 1,
        )

        best = self._better_result(
            LevelOcrResult(None, engine="paddleocr_rec"),
            fast_result,
        )
        strong_levels = {}
        unprefixed_levels = {}
        if self._is_strong_result(fast_result):
            # It was not literal enough for one-read acceptance, but it still
            # counts as one observation in the existing consensus path.
            strong_levels[fast_result.level] = fast_result
        else:
            self._record_unprefixed_level(unprefixed_levels, fast_result)
        for index, image in enumerate(variants):
            if index == fast_index:
                continue
            result = self._run_text_recognition(image)
            best = self._better_result(best, result)
            if self._is_strong_result(result):
                prior = strong_levels.get(result.level)
                if prior is not None:
                    return self._better_result(prior, result)
                strong_levels[result.level] = result
            else:
                consensus = self._record_unprefixed_level(unprefixed_levels, result)
                if consensus is not None and self._is_confident_result(consensus):
                    return consensus

        if self._is_acceptable_result(best) and self._has_level_prefix(best.text):
            return best

        engine = self._get_engine()
        if engine is None:
            if self._is_acceptable_result(best):
                return self._as_provisional_result(best)
            return LevelOcrResult(None, best.text, best.confidence, best.engine, self.init_error)

        for image in variants:
            result = self._run_paddleocr(engine, image)
            best = self._better_result(best, result)
            if self._is_strong_result(result):
                prior = strong_levels.get(result.level)
                if prior is not None:
                    return self._better_result(prior, result)
                strong_levels[result.level] = result
            else:
                consensus = self._record_unprefixed_level(unprefixed_levels, result)
                if consensus is not None and self._is_confident_result(consensus):
                    return consensus
        if self._is_acceptable_result(best):
            if self._has_level_prefix(best.text):
                return best
            return self._as_provisional_result(best)
        error = best.error
        if best.level is not None and not error:
            confidence = "unknown" if best.confidence is None else f"{best.confidence:.2f}"
            error = f"OCR confidence {confidence} is below the safety threshold"
        return LevelOcrResult(None, best.text, best.confidence, best.engine, error)

    def _has_level_prefix(self, text):
        normalized = self._normalize_ocr_prefix(text)
        return bool(
            self._LEVEL_PREFIX_PATTERN.search(normalized)
            or self._SINGLE_L_PREFIX_PATTERN.search(normalized)
        )

    def _normalize_ocr_prefix(self, text):
        normalized = (text or "").lower()
        normalized = self._OCR_PREFIX_CORRECTION_PATTERN.sub("lv", normalized)
        return self._OCR_ROMAN_V_PREFIX_CORRECTION_PATTERN.sub("lv", normalized)

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

    def _is_confident_result(self, result):
        return (
            self._is_acceptable_result(result)
            and result.confidence >= self.STRONG_ACCEPT_CONFIDENCE
        )

    def _record_unprefixed_level(self, observations, result):
        if (
            not self._is_acceptable_result(result)
            or self._has_level_prefix(result.text)
        ):
            return None
        prior = observations.get(result.level)
        observations[result.level] = self._better_result(prior, result)
        if prior is None:
            return None
        return self._better_result(prior, result)

    def _as_provisional_result(self, result):
        if not self._is_confident_result(result) or self._has_level_prefix(result.text):
            return result
        return LevelOcrResult(
            result.level,
            text=result.text,
            confidence=self.STRONG_ACCEPT_CONFIDENCE - 0.001,
            engine=result.engine,
            error=result.error,
        )

    def _is_fast_path_result(self, result):
        if (
            result is None
            or result.level is None
            or result.confidence is None
            or result.confidence < self.STRONG_ACCEPT_CONFIDENCE
        ):
            return False
        # Be stricter than the exhaustive path here. OCR corrections such as
        # "1v"/"iv"/"ly" and the generic leading-L form remain supported by
        # the fallback, but cannot trigger the one-read fast acceptance.
        normalized = (result.text or "").lower()
        return bool(self._LEVEL_PREFIX_PATTERN.search(normalized))

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

        init_attempts: list[dict[str, Any]] = [
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
        bgr = self._as_bgr(frame)

        variants = []
        for region in self._level_text_regions(bgr):
            variants.extend(self._preprocess_single_region(region))
        return variants

    def _preprocess_fast_variant(self, frame):
        bgr = self._as_bgr(frame)
        regions = self._level_text_regions(bgr)
        region = regions[min(self._FAST_REGION_INDEX, len(regions) - 1)]
        return self._upscale(region)

    def _as_bgr(self, frame):
        if len(frame.shape) == 2:
            return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        return frame

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
        upscaled = self._upscale(bgr)
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

    def _upscale(self, image):
        return cv2.resize(image, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)

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
        normalized = self._normalize_ocr_prefix(text)

        for pattern in (self._LEVEL_PREFIX_PATTERN, self._SINGLE_L_PREFIX_PATTERN):
            match = pattern.search(normalized)
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
