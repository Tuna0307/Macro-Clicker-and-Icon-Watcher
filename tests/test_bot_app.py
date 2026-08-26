from macro_clicker.app import App
from macro_clicker.bot.ui import BotFrame
from macro_clicker.bot_app import BotApp


def test_bot_frame_exposes_normal_user_pages():
    assert BotFrame.TAB_NAMES == (
        "Dashboard",
        "Rally",
        "Gather",
        "Positions",
        "Alerts",
        "Schedule",
        "Logs",
        "Settings",
    )


def test_bot_app_keeps_advanced_backends_behind_explicit_hooks():
    assert issubclass(BotApp, App)
    assert callable(BotApp._show_advanced_tools)
    assert callable(BotApp._show_alert_setup)
    assert callable(BotApp._show_tool_tab)
    assert callable(BotApp._start_bot_feature)
