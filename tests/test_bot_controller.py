from macro_clicker.bot.config import BotConfig
from macro_clicker.bot.controller import BotController


def _controller(config, started, stopped=None):
    return BotController(
        lambda: config,
        lambda feature: started.append(feature) or True,
        stopped or (lambda: True),
    )


def test_controller_starts_finite_work_before_continuous_rally():
    config = BotConfig()
    config.positions.development_enabled = True
    config.positions.science_enabled = True
    config.gather.enabled = True
    config.rally.enabled = True
    started = []
    controller = _controller(config, started)

    assert controller.enabled_features() == [
        "development",
        "science",
        "gather",
        "rally",
    ]
    assert controller.start() is True
    assert started == ["development"]
    assert controller.status.active_feature == "development"
    assert controller.status.session_active is True


def test_controller_advances_through_enabled_bot_cycle():
    config = BotConfig()
    config.rally.enabled = False
    config.positions.development_enabled = True
    config.gather.enabled = True
    started = []
    controller = _controller(config, started)

    assert controller.start() is True
    assert started == ["development"]

    controller.engine_stopped()
    assert started == ["development", "gather"]
    assert controller.status.active_feature == "gather"

    controller.engine_stopped()
    assert controller.status.active_feature is None
    assert controller.status.session_active is False
    assert controller.status.last_message == "Bot cycle completed"


def test_direct_run_stays_one_off_and_serializes_input():
    config = BotConfig()
    started = []
    controller = _controller(config, started)

    assert controller.run_feature("rally") is True
    assert controller.run_feature("gather") is False
    assert started == ["rally"]

    controller.engine_stopped()
    assert controller.run_feature("gather") is True
    assert started == ["rally", "gather"]
    assert controller.status.session_active is False


def test_stop_discards_remaining_bot_cycle():
    config = BotConfig()
    config.rally.enabled = False
    config.positions.development_enabled = True
    config.gather.enabled = True
    started = []
    stop_calls = []
    controller = _controller(
        config,
        started,
        stopped=lambda: stop_calls.append(True) or True,
    )

    assert controller.start() is True
    assert started == ["development"]
    assert controller.stop() is True
    assert stop_calls == [True]
    assert controller.status.active_feature is None
    assert controller.status.session_active is False

    # A late engine-stopped notification must not restart the queued Gather job.
    controller.engine_stopped()
    assert started == ["development"]
