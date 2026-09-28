from macro_clicker.bot import (
    BossCandidate,
    BotRuntime,
    CapabilityStatus,
    RallyCandidate,
)
from macro_clicker.bot.features.rally_join import RallyJoinController
from macro_clicker.bot.input_backend import DisabledInputBackend, InputBackendError
from macro_clicker.bot.skeleton_port import SkeletonGamePort


def test_disabled_input_backend_fails_closed_without_using_cursor():
    backend = DisabledInputBackend()
    assert backend.uses_physical_cursor is False
    assert backend.status is CapabilityStatus.NOT_IMPLEMENTED

    try:
        backend.click_client(10, 10)
    except InputBackendError:
        pass
    else:
        raise AssertionError("disabled backend must reject clicks")


def test_runtime_advertises_unproven_statuses():
    runtime = BotRuntime(SkeletonGamePort())
    statuses = {item.name: item.status for item in runtime.capabilities()}

    assert statuses["cursor_free_input"] is CapabilityStatus.IMPLEMENTED_NOT_LIVE_TESTED
    assert statuses["server_jump"] is CapabilityStatus.NOT_IMPLEMENTED
    assert statuses["zombie_boss_scan"] is CapabilityStatus.NOT_IMPLEMENTED
    assert statuses["auto_join_zombie_boss_rally"] is CapabilityStatus.NOT_IMPLEMENTED


def test_server_jump_placeholder_does_not_claim_success():
    runtime = BotRuntime(SkeletonGamePort())
    result = runtime.jump_to_server(123)

    assert result.ok is False
    assert result.status is CapabilityStatus.NOT_IMPLEMENTED


def test_rally_policy_prefers_known_larger_available_slot_count():
    controller = RallyJoinController(SkeletonGamePort())
    boss = BossCandidate("boss-1")
    rallies = [
        RallyCandidate("r3", boss=boss, slots_available=None),
        RallyCandidate("r2", boss=boss, slots_available=1),
        RallyCandidate("r1", boss=boss, slots_available=3),
    ]

    assert controller.choose_rally(rallies).rally_id == "r1"
