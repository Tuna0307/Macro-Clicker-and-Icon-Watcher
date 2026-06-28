import copy
import os
import shutil


def duplicate_template_file(source_path: str, new_name: str) -> str:
    if not source_path:
        raise ValueError("Template path is required.")
    if not os.path.exists(source_path):
        raise FileNotFoundError(source_path)

    safe_name = "".join(c for c in new_name if c.isalnum() or c in ("_", "-"))
    if not safe_name:
        raise ValueError("New template name is required.")

    folder = os.path.dirname(source_path) or "."
    base_path = os.path.join(folder, f"{safe_name}.png")
    target_path = _unique_path(base_path)
    shutil.copy2(source_path, target_path)
    return target_path


def duplicate_step(step, existing_names):
    copied = copy.deepcopy(step)
    copied.name = _unique_step_name(f"{step.name}_copy", existing_names)
    return copied


def duplicate_scenario(scenario, new_name):
    if not new_name.strip():
        raise ValueError("New scenario name is required.")
    copied = copy.deepcopy(scenario)
    copied.name = new_name.strip()
    return copied


def _unique_step_name(base_name, existing_names):
    if base_name not in existing_names:
        return base_name
    counter = 2
    while f"{base_name}_{counter}" in existing_names:
        counter += 1
    return f"{base_name}_{counter}"


def _unique_path(base_path):
    if not os.path.exists(base_path):
        return base_path
    root, ext = os.path.splitext(base_path)
    counter = 2
    while os.path.exists(f"{root}_{counter}{ext}"):
        counter += 1
    return f"{root}_{counter}{ext}"
