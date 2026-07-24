import copy
import os
import shutil
import tempfile

from .models import project_path

CONDITION_REFERENCE_FIELDS = (
    "on_condition_index",
    "match_condition_index",
    "no_match_condition_index",
)


def duplicate_template_file(source_path: str, new_name: str) -> str:
    if not source_path:
        raise ValueError("Template path is required.")
    resolved_source = project_path(source_path)
    if not os.path.isfile(resolved_source):
        raise FileNotFoundError(source_path)

    safe_name = "".join(c for c in new_name if c.isalnum() or c in ("_", "-"))
    if not safe_name:
        raise ValueError("New template name is required.")

    folder = os.path.dirname(resolved_source) or "."
    base_path = os.path.join(folder, f"{safe_name}.png")
    target_path = _unique_path(base_path)
    fd, temp_path = tempfile.mkstemp(prefix=f".{safe_name}.", suffix=".png", dir=folder)
    os.close(fd)
    try:
        shutil.copy2(resolved_source, temp_path)
        os.replace(temp_path, target_path)
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise
    return target_path


def duplicate_step(step, existing_names):
    copied = copy.deepcopy(step)
    copied.name = _unique_step_name(f"{step.name}_copy", existing_names)
    rewrite_step_references([copied], step.name, copied.name)
    return copied


def duplicate_scenario(scenario, new_name):
    if not new_name.strip():
        raise ValueError("New scenario name is required.")
    copied = copy.deepcopy(scenario)
    copied.name = new_name.strip()
    return copied


def remap_condition_references(actions, removed_index):
    """
    Keep action condition indexes aligned after a condition is removed.

    References to the deleted condition are cleared so they cannot silently
    point at the condition that shifted into its place. References above the
    deleted position are decremented.
    """
    if (
        isinstance(removed_index, bool)
        or not isinstance(removed_index, int)
        or removed_index < 0
    ):
        raise ValueError("removed_index must be a non-negative integer")

    cleared = 0
    shifted = 0
    for action in actions:
        for field_name in CONDITION_REFERENCE_FIELDS:
            value = getattr(action, field_name, None)
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            if value == removed_index:
                setattr(action, field_name, None)
                cleared += 1
            elif value > removed_index:
                setattr(action, field_name, value - 1)
                shifted += 1
    return {"cleared": cleared, "shifted": shifted}


def rewrite_step_references(steps, old_name, new_name=None):
    """Rename or remove all references to a step name in a scenario."""
    old_name = str(old_name or "")
    new_name = None if new_name is None else str(new_name)
    if not old_name:
        return {"renamed": 0, "removed_actions": 0, "removed_list_entries": 0}

    renamed = 0
    removed_actions = 0
    removed_list_entries = 0

    for step in steps:
        kept_actions = []
        for action in step.actions:
            if (
                getattr(action, "type", None) == "set_step"
                and getattr(action, "step_name", "") == old_name
            ):
                if new_name is None:
                    removed_actions += 1
                    continue
                action.step_name = new_name
                renamed += 1

            names = list(getattr(action, "no_match_disable_steps", None) or [])
            if names:
                rewritten = []
                for name in names:
                    if name == old_name:
                        if new_name is None:
                            removed_list_entries += 1
                            continue
                        name = new_name
                        renamed += 1
                    if name not in rewritten:
                        rewritten.append(name)
                action.no_match_disable_steps = rewritten
            kept_actions.append(action)
        step.actions = kept_actions

    return {
        "renamed": renamed,
        "removed_actions": removed_actions,
        "removed_list_entries": removed_list_entries,
    }


def find_case_insensitive_name(names, candidate, exclude_name=None):
    """Return the existing spelling of a case-insensitive name collision."""
    folded_candidate = str(candidate).casefold()
    folded_exclude = None if exclude_name is None else str(exclude_name).casefold()
    for name in names:
        folded_name = str(name).casefold()
        if folded_name == folded_exclude:
            continue
        if folded_name == folded_candidate:
            return name
    return None


def _unique_step_name(base_name, existing_names):
    existing_folded = {str(name).casefold() for name in existing_names}
    if base_name.casefold() not in existing_folded:
        return base_name
    counter = 2
    while f"{base_name}_{counter}".casefold() in existing_folded:
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
