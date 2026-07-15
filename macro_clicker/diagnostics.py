"""Bounded asynchronous screenshot and metadata collection for debugging."""

import atexit
import json
import os
import queue
import random
import re
import shutil
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from .runtime_paths import DIAGNOSTIC_DIR

DEFAULT_MAX_EVENTS = 200
DEFAULT_MAX_CRITICAL_EVENTS = 175
DEFAULT_MAX_SAMPLE_EVENTS = 25
DEFAULT_MAX_AGE_DAYS = 7
DEFAULT_MAX_BYTES = 500 * 1024 * 1024
DEFAULT_QUEUE_SIZE = 16
DEFAULT_DECISION_QUEUE_SIZE = 1024
DEFAULT_DECISION_LOG_BYTES = 5 * 1024 * 1024
DEFAULT_DECISION_LOG_BACKUPS = 3
DEFAULT_IMAGE_DEDUPE_DISTANCE = 4
DEFAULT_STALE_TEMP_AGE_SECONDS = 24 * 60 * 60


def _safe_name(value, fallback="event"):
    text = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(value or "")).strip("._-")
    return (text or fallback)[:80]


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


class DiagnosticCollector:
    """Writes bounded diagnostic evidence without blocking the macro loop."""

    def __init__(
        self,
        root=DIAGNOSTIC_DIR,
        *,
        max_events=DEFAULT_MAX_EVENTS,
        max_critical_events=DEFAULT_MAX_CRITICAL_EVENTS,
        max_sample_events=DEFAULT_MAX_SAMPLE_EVENTS,
        max_age_days=DEFAULT_MAX_AGE_DAYS,
        max_bytes=DEFAULT_MAX_BYTES,
        queue_size=DEFAULT_QUEUE_SIZE,
        decision_queue_size=DEFAULT_DECISION_QUEUE_SIZE,
        decision_log_bytes=DEFAULT_DECISION_LOG_BYTES,
        decision_log_backups=DEFAULT_DECISION_LOG_BACKUPS,
        log=None,
        synchronous=False,
    ):
        self.root = Path(root)
        self.max_events = max_events
        self.max_critical_events = max_critical_events
        self.max_sample_events = max_sample_events
        self.max_age_days = max_age_days
        self.max_bytes = max_bytes
        self.decision_log_bytes = decision_log_bytes
        self.decision_log_backups = max(0, int(decision_log_backups))
        self.log = log or (lambda _message: None)
        self.synchronous = synchronous
        self._queue = queue.Queue(maxsize=max(1, int(queue_size)))
        self._decision_queue = queue.Queue(
            maxsize=max(1, int(decision_queue_size))
        )
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._decision_file_lock = threading.Lock()
        self._last_capture = {}
        self._last_image_hash = {}
        self._drop_log_at = 0.0
        self._decision_drop_log_at = 0.0
        self._worker = None
        self._decision_worker = None
        self._closed = False
        self.cleanup()
        if not synchronous:
            self._worker = threading.Thread(
                target=self._run,
                name="diagnostic-writer",
                daemon=True,
            )
            self._worker.start()
            self._decision_worker = threading.Thread(
                target=self._run_decisions,
                name="diagnostic-decision-writer",
                daemon=True,
            )
            self._decision_worker.start()

    @staticmethod
    def _category_name(category):
        return "samples" if str(category).lower() in {"sample", "samples"} else "critical"

    def should_capture(self, key, *, min_interval=0.0, sample_rate=1.0, now=None):
        return self.reserve_capture(
            key,
            min_interval=min_interval,
            sample_rate=sample_rate,
            now=now,
        ) is not None

    def reserve_capture(self, key, *, min_interval=0.0, sample_rate=1.0, now=None):
        if sample_rate <= 0.0:
            return None
        if sample_rate < 1.0 and random.random() >= sample_rate:
            return None
        now = time.monotonic() if now is None else float(now)
        key = str(key or "event")
        with self._lock:
            last = self._last_capture.get(key)
            if last is not None and now - last < max(0.0, float(min_interval)):
                return None
            self._last_capture[key] = now
            if len(self._last_capture) > 512:
                oldest = sorted(
                    self._last_capture,
                    key=lambda item: self._last_capture[item],
                )[:128]
                for old_key in oldest:
                    self._last_capture.pop(old_key, None)
        return key, now

    def rollback_capture(self, reservation):
        if reservation is None:
            return
        key, reserved_at = reservation
        with self._lock:
            if self._last_capture.get(key) == reserved_at:
                self._last_capture.pop(key, None)

    def record_decision(self, event_type, metadata, *, category="critical"):
        """Append compact decision data without capturing or encoding a screenshot."""
        payload = {
            **_json_safe(metadata or {}),
            "schema_version": 1,
            "event_type": str(event_type),
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "category": self._category_name(category),
        }
        if self.synchronous:
            if self._closed:
                return None
            self._write_decision(payload)
            return str(self.root / "decisions.jsonl")
        try:
            with self._state_lock:
                if self._closed:
                    return None
                self._decision_queue.put_nowait(payload)
            return str(self.root / "decisions.jsonl")
        except queue.Full:
            now = time.monotonic()
            if now - self._decision_drop_log_at >= 10.0:
                self.log("[diagnostic] decision queue full; dropping metadata event")
                self._decision_drop_log_at = now
            return None

    @staticmethod
    def _perceptual_hash(image):
        if not isinstance(image, np.ndarray) or not image.size:
            return None
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        resized = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
        bits = resized[:, 1:] >= resized[:, :-1]
        value = 0
        for bit in bits.flat:
            value = (value << 1) | int(bit)
        return value

    def _is_duplicate_image(
        self,
        key,
        image,
        *,
        window,
        max_distance=DEFAULT_IMAGE_DEDUPE_DISTANCE,
    ):
        if window <= 0.0:
            return False
        image_hash = self._perceptual_hash(image)
        if image_hash is None:
            return False
        now = time.monotonic()
        key = str(key or "event")
        with self._lock:
            previous = self._last_image_hash.get(key)
            duplicate = False
            if previous is not None and now - previous[0] < float(window):
                duplicate = (image_hash ^ previous[1]).bit_count() <= int(max_distance)
            self._last_image_hash[key] = (now, image_hash)
            if len(self._last_image_hash) > 512:
                oldest = sorted(
                    self._last_image_hash,
                    key=lambda item: self._last_image_hash[item][0],
                )[:128]
                for old_key in oldest:
                    self._last_image_hash.pop(old_key, None)
        return duplicate

    def submit(
        self,
        event_type,
        metadata,
        images,
        *,
        key=None,
        min_interval=0.0,
        sample_rate=1.0,
        force=False,
        category="critical",
        dedupe_image=None,
        dedupe_window=0.0,
        dedupe_distance=DEFAULT_IMAGE_DEDUPE_DISTANCE,
        log_decision=True,
        capture_reservation=None,
    ):
        category = self._category_name(category)
        if log_decision:
            self.record_decision(
                event_type,
                {
                    **(metadata or {}),
                    "screenshot_requested": True,
                },
                category=category,
            )
        reservation = capture_reservation
        if not force:
            reservation = self.reserve_capture(
                key or event_type,
                min_interval=min_interval,
                sample_rate=sample_rate,
            )
            if reservation is None:
                return None
        if self._is_duplicate_image(
            key or event_type,
            dedupe_image,
            window=max(0.0, float(dedupe_window)),
            max_distance=dedupe_distance,
        ):
            return None
        timestamp_ns = time.time_ns()
        event_id = (
            f"{time.strftime('%Y%m%d-%H%M%S')}-"
            f"{timestamp_ns % 1_000_000_000:09d}_{_safe_name(event_type)}"
        )
        payload_images = {}
        for name, image in (images or {}).items():
            if isinstance(image, np.ndarray) and image.size:
                safe_name = _safe_name(name, "image")
                extension = ".jpg" if safe_name.startswith("context") else ".png"
                payload_images[safe_name + extension] = image.copy()
        payload = {
            "event_id": event_id,
            "event_type": str(event_type),
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "category": category,
            "metadata": _json_safe(metadata or {}),
            "images": payload_images,
        }
        if self.synchronous:
            if self._closed:
                self.rollback_capture(reservation)
                return None
            try:
                return self._write(payload)
            except Exception:
                self.rollback_capture(reservation)
                raise
        try:
            with self._state_lock:
                if self._closed:
                    self.rollback_capture(reservation)
                    return None
                self._queue.put_nowait(payload)
            return str(self.root / category / event_id)
        except queue.Full:
            self.rollback_capture(reservation)
            now = time.monotonic()
            if now - self._drop_log_at >= 10.0:
                self.log("[diagnostic] writer queue full; dropping screenshot event")
                self._drop_log_at = now
            return None

    def flush(self, timeout=10.0):
        if self.synchronous:
            return True
        deadline = time.monotonic() + max(0.0, float(timeout))
        while (
            self._queue.unfinished_tasks or self._decision_queue.unfinished_tasks
        ) and time.monotonic() < deadline:
            time.sleep(0.01)
        return (
            self._queue.unfinished_tasks == 0
            and self._decision_queue.unfinished_tasks == 0
        )

    def close(self, timeout=2.0):
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        if self.synchronous or self._worker is None:
            return
        deadline = time.monotonic() + max(0.0, float(timeout))

        def enqueue_stop(target_queue):
            remaining = max(0.0, deadline - time.monotonic())
            try:
                target_queue.put(None, timeout=remaining)
                return True
            except queue.Full:
                return False

        image_stop_queued = enqueue_stop(self._queue)
        decision_stop_queued = enqueue_stop(self._decision_queue)
        if not image_stop_queued or not decision_stop_queued:
            self.log("[diagnostic] timed out while closing writer queues")
        remaining = max(0.0, deadline - time.monotonic())
        self._worker.join(timeout=remaining)
        if self._decision_worker is not None:
            remaining = max(0.0, deadline - time.monotonic())
            self._decision_worker.join(timeout=remaining)

    def _run(self):
        while True:
            payload = self._queue.get()
            try:
                if payload is None:
                    return
                self._write(payload)
            except Exception as exc:
                self.log(f"[diagnostic] could not save event: {exc}")
            finally:
                self._queue.task_done()

    def _run_decisions(self):
        while True:
            payload = self._decision_queue.get()
            try:
                if payload is None:
                    return
                self._write_decision(payload)
            except Exception as exc:
                self.log(f"[diagnostic] could not save decision metadata: {exc}")
            finally:
                self._decision_queue.task_done()

    def _write_decision(self, payload):
        encoded = (
            json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        with self._decision_file_lock:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / "decisions.jsonl"
            if (
                self.decision_log_bytes is not None
                and path.is_file()
                and path.stat().st_size + len(encoded) > self.decision_log_bytes
            ):
                self._rotate_decision_log(path)
            with path.open("ab") as handle:
                handle.write(encoded)

    def _rotate_decision_log(self, path):
        if self.decision_log_backups <= 0:
            path.unlink(missing_ok=True)
            return
        oldest = path.with_name(f"{path.name}.{self.decision_log_backups}")
        oldest.unlink(missing_ok=True)
        for index in range(self.decision_log_backups - 1, 0, -1):
            source = path.with_name(f"{path.name}.{index}")
            if source.exists():
                os.replace(source, path.with_name(f"{path.name}.{index + 1}"))
        if path.exists():
            os.replace(path, path.with_name(f"{path.name}.1"))

    def _write(self, payload):
        self.root.mkdir(parents=True, exist_ok=True)
        category_dir = self.root / payload["category"]
        category_dir.mkdir(parents=True, exist_ok=True)
        final_dir = category_dir / payload["event_id"]
        temp_dir = category_dir / f".{payload['event_id']}.tmp"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        temp_dir.mkdir(parents=True)
        try:
            image_names = []
            for filename, image in payload["images"].items():
                path = temp_dir / filename
                extension = Path(filename).suffix.lower()
                encode_options = [cv2.IMWRITE_JPEG_QUALITY, 85] if extension == ".jpg" else []
                ok, encoded = cv2.imencode(extension, image, encode_options)
                if not ok:
                    raise OSError(f"could not encode {filename}")
                path.write_bytes(encoded.tobytes())
                image_names.append(filename)
            record = {
                **payload["metadata"],
                "schema_version": 1,
                "event_id": payload["event_id"],
                "event_type": payload["event_type"],
                "captured_at": payload["captured_at"],
                "category": payload["category"],
                "images": image_names,
            }
            with (temp_dir / "metadata.json").open("w", encoding="utf-8") as handle:
                json.dump(record, handle, indent=2, sort_keys=True, ensure_ascii=False)
                handle.write("\n")
            os.replace(temp_dir, final_dir)
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
        self.cleanup()
        self.log(f"[diagnostic] saved {payload['event_type']}: {final_dir}")
        return str(final_dir)

    def cleanup(self):
        if not self.root.is_dir():
            return 0
        now = time.time()
        cutoff = (
            now - float(self.max_age_days) * 86400
            if self.max_age_days is not None and self.max_age_days >= 0
            else None
        )
        events = []
        removed = 0
        candidates = []
        for category in ("critical", "samples"):
            category_dir = self.root / category
            if category_dir.is_dir():
                candidates.extend((path, category) for path in category_dir.iterdir())
        candidates.extend(
            (path, "critical")
            for path in self.root.iterdir()
            if path.is_dir() and path.name not in {"critical", "samples"}
        )
        for path, category in candidates:
            if not path.is_dir():
                continue
            if path.name.startswith("."):
                if path.name.endswith(".tmp"):
                    try:
                        is_stale = (
                            now - path.stat().st_mtime
                            >= DEFAULT_STALE_TEMP_AGE_SECONDS
                        )
                    except OSError:
                        is_stale = False
                    if is_stale:
                        shutil.rmtree(path, ignore_errors=True)
                        if not path.exists():
                            removed += 1
                continue
            try:
                mtime = path.stat().st_mtime
                size = sum(
                    item.stat().st_size
                    for item in path.rglob("*")
                    if item.is_file()
                )
            except OSError:
                continue
            if cutoff is not None and mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
            else:
                events.append((mtime, size, path, category))

        retained = []
        category_limits = {
            "critical": self.max_critical_events,
            "samples": self.max_sample_events,
        }
        for category, limit in category_limits.items():
            category_events = sorted(
                (event for event in events if event[3] == category),
                key=lambda item: item[0],
                reverse=True,
            )
            for index, event in enumerate(category_events):
                if limit is not None and index >= limit:
                    shutil.rmtree(event[2], ignore_errors=True)
                    removed += 1
                else:
                    retained.append(event)

        retained.sort(key=lambda item: item[0], reverse=True)
        retained_bytes = 0
        for index, (_mtime, size, path, _category) in enumerate(retained):
            over_count = self.max_events is not None and index >= self.max_events
            over_bytes = (
                self.max_bytes is not None
                and retained_bytes + size > self.max_bytes
            )
            if over_count or over_bytes:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
            else:
                retained_bytes += size
        return removed


_SHARED_COLLECTOR = None
_SHARED_LOCK = threading.Lock()


def get_diagnostic_collector(log=None):
    global _SHARED_COLLECTOR
    with _SHARED_LOCK:
        if _SHARED_COLLECTOR is None:
            _SHARED_COLLECTOR = DiagnosticCollector(log=log)
            atexit.register(_SHARED_COLLECTOR.close)
        elif log is not None:
            _SHARED_COLLECTOR.log = log
        return _SHARED_COLLECTOR
