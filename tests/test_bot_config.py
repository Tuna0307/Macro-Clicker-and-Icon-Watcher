from macro_clicker.bot.config import (
    BotConfig,
    bot_config_from_dict,
    load_bot_config,
    save_bot_config,
    validate_bot_config,
)


def test_bot_config_round_trip(tmp_path):
    path = tmp_path / "bot.json"
    config = BotConfig()
    config.rally.min_level = 31
    config.rally.max_level = 72
    config.rally.team1_max_level = 72
    config.rally.team3_max_level = 55
    config.gather.enabled = True
    config.gather.start_level = 12
    config.gather.replacement_order = [3, 2, 1]

    save_bot_config(config, str(path))
    loaded = load_bot_config(str(path))

    assert loaded == config


def test_bot_config_tolerates_bad_values_and_bounds_them():
    loaded = bot_config_from_dict(
        {
            "rally": {
                "min_level": "40",
                "max_level": "20",
                "team1_max_level": 9999,
                "team3_max_level": "bad",
                "join_delay": float("inf"),
            },
            "gather": {
                "start_level": 0,
                "march_count": 99,
                "replacement_order": "3 -> 2 -> 1",
            },
        }
    )

    assert loaded.rally.min_level == 40
    assert loaded.rally.max_level == 40
    assert loaded.rally.team1_max_level == 40
    assert loaded.rally.join_delay == 0.0
    assert loaded.gather.start_level == 1
    assert loaded.gather.march_count == 3
    assert loaded.gather.replacement_order == [3, 2, 1]


def test_default_bot_config_is_valid():
    validate_bot_config(BotConfig())
