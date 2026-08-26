from macro_clicker.bot.config import BotConfig
from macro_clicker.bot.controller import BotController


def test_controller_starts_highest_priority_enabled_feature():
    config = BotConfig()
    config.rally.enabled = False
    config.positions.development_enabled = True
    config.gather.enabled = True
    started = []

    controller = BotController(
        lambda: config,
        lambda feature: started.append(feature) or True,
        lambda: True,
    )

    assert controller.start() is True
    assert started == ["development"]
    assert controller.status.active_feature == "development"


def test_controller_serializes_clicking_automations():
    config = BotConfig()
    started = []
    controller = BotController(
        lambda: config,
        lambda feature: started.append(feature) or True,
        lambda: True,
    )

    assert controller.run_feature("rally") is True
    assert controller.run_feature("gather") is False
    assert started == ["rally"]

    controller.engine_stopped()
    assert controller.run_feature("gather") is True
    assert started == ["rally", "gather"]
