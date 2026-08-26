from macro_clicker.resource_gathering import (
    GatherController,
    replacement_click_offset,
)


def test_free_success_keeps_replacement_pointer():
    controller = GatherController()
    progress = controller.record_success(target_count=3, replacement_order=[3, 2, 1])
    assert progress.successful_dispatches == 1
    assert progress.next_replacement_march == 3
    assert progress.used_replacement is False
    assert progress.complete is False


def test_replacement_success_advances_three_two_one_only_after_success():
    controller = GatherController()
    assert controller.mark_replacement_selected([3, 2, 1]) == 3
    first = controller.record_success(target_count=3, replacement_order=[3, 2, 1])
    assert first.next_replacement_march == 2

    assert controller.mark_replacement_selected([3, 2, 1]) == 2
    second = controller.record_success(target_count=3, replacement_order=[3, 2, 1])
    assert second.next_replacement_march == 1

    assert controller.mark_replacement_selected([3, 2, 1]) == 1
    third = controller.record_success(target_count=3, replacement_order=[3, 2, 1])
    assert third.complete is True
    assert third.next_replacement_march is None


def test_taken_resource_does_not_consume_replacement_pointer():
    controller = GatherController()
    assert controller.mark_replacement_selected([3, 2, 1]) == 3
    controller.cancel_retry()
    assert controller.current_replacement([3, 2, 1]) == 3
    assert controller.mark_replacement_selected([3, 2, 1]) == 3


def test_controller_resets_between_scenario_runs():
    controller = GatherController()
    controller.mark_replacement_selected([3, 2, 1])
    controller.record_success(target_count=3, replacement_order=[3, 2, 1])
    controller.reset()
    assert controller.successful_dispatches == 0
    assert controller.current_replacement([3, 2, 1]) == 3
    assert controller.replacement_pending is False


def test_replacement_offsets_match_proven_march_layout():
    assert replacement_click_offset(3) == (63, 630)
    assert replacement_click_offset(2) == (-61, 630)
    assert replacement_click_offset(1) == (-188, 630)


def _bare_engine():
    import threading
    from unittest.mock import Mock

    from macro_clicker.engine import MacroEngine

    engine = object.__new__(MacroEngine)
    engine._stop_event = threading.Event()
    engine._gather_controller = GatherController()
    engine._retry_current_step = False
    engine.log = lambda _message: None
    engine._match_geometry_scale = lambda _match: (1.0, 1.0)
    engine._click_point = Mock(return_value=True)
    engine.stop = Mock(side_effect=engine._stop_event.set)
    return engine


def test_engine_selects_current_replacement_from_anchor_and_marks_it_pending():
    from macro_clicker.models import Action

    engine = _bare_engine()
    action = Action(
        type="gather_control",
        gather_command="select_replacement",
        on_condition_index=0,
        gather_replacement_order=[3, 2, 1],
    )
    points = {0: (1000, 100)}
    matches = {0: [{"center": (1000, 100)}]}

    result = engine._run_gather_control_action(action, points, matches)

    assert result is True
    engine._click_point.assert_called_once_with(1063, 730, "left")
    assert engine._gather_controller.replacement_pending is True
    assert engine._gather_controller.current_replacement([3, 2, 1]) == 3


def test_engine_stops_after_configured_success_count():
    from macro_clicker.models import Action

    engine = _bare_engine()
    action = Action(
        type="gather_control",
        gather_command="record_success",
        gather_target_count=1,
        gather_replacement_order=[3, 2, 1],
    )

    result = engine._run_gather_control_action(action, {}, {})

    assert result is False
    engine.stop.assert_called_once_with()
    assert engine._gather_controller.successful_dispatches == 1
