import os
import time


DEFAULT_MAX_LOG_BYTES = 5 * 1024 * 1024
DEFAULT_LOG_BACKUPS = 5
DEFAULT_DEBUG_MAX_FILES = 200
DEFAULT_DEBUG_MAX_AGE_DAYS = 7


def rotated_log_path(path, index):
    root, ext = os.path.splitext(path)
    return f"{root}.{index}{ext}"


def rotate_log_file(path, max_bytes=DEFAULT_MAX_LOG_BYTES, backups=DEFAULT_LOG_BACKUPS):
    if max_bytes <= 0 or backups <= 0 or not os.path.exists(path):
        return False
    try:
        if os.path.getsize(path) < max_bytes:
            return False
        oldest = rotated_log_path(path, backups)
        if os.path.exists(oldest):
            os.remove(oldest)
        for index in range(backups - 1, 0, -1):
            src = rotated_log_path(path, index)
            if os.path.exists(src):
                os.replace(src, rotated_log_path(path, index + 1))
        os.replace(path, rotated_log_path(path, 1))
        return True
    except OSError:
        return False


def cleanup_directory(path, max_files=DEFAULT_DEBUG_MAX_FILES,
                      max_age_days=DEFAULT_DEBUG_MAX_AGE_DAYS, now=None,
                      preserve_names=None):
    if not os.path.isdir(path):
        return 0
    now = time.time() if now is None else now
    cutoff = now - (max_age_days * 86400) if max_age_days is not None and max_age_days >= 0 else None
    removed = 0
    files = []
    preserved = {str(name).casefold() for name in (preserve_names or ())}

    for name in os.listdir(path):
        if name.casefold() in preserved:
            continue
        full_path = os.path.join(path, name)
        if not os.path.isfile(full_path):
            continue
        try:
            mtime = os.path.getmtime(full_path)
        except OSError:
            continue
        if cutoff is not None and mtime < cutoff:
            try:
                os.remove(full_path)
                removed += 1
            except OSError:
                pass
            continue
        files.append((mtime, full_path))

    if max_files is not None and max_files >= 0 and len(files) > max_files:
        files.sort(key=lambda item: item[0], reverse=True)
        for _, full_path in files[max_files:]:
            try:
                os.remove(full_path)
                removed += 1
            except OSError:
                pass
    return removed


def maintain_logs(log_dir, main_log_path=None, max_log_bytes=DEFAULT_MAX_LOG_BYTES,
                  log_backups=DEFAULT_LOG_BACKUPS, debug_max_files=DEFAULT_DEBUG_MAX_FILES,
                  debug_max_age_days=DEFAULT_DEBUG_MAX_AGE_DAYS):
    if main_log_path:
        rotate_log_file(main_log_path, max_log_bytes, log_backups)
    for folder in ("level_debug", "region_debug"):
        cleanup_directory(
            os.path.join(log_dir, folder),
            max_files=debug_max_files,
            max_age_days=debug_max_age_days,
            preserve_names={"labels.json"},
        )
