from datetime import datetime
from unittest.mock import Mock, patch

from macro_clicker.app import App
from macro_clicker.bot.ui import BotFrame
from macro_clicker.bot.ui_pages import BotPagesMixin
from macro_clicker.bot_app import BotApp


def test_bot_frame_exposes_normal_user_pages():
    assert BotFrame.TAB_NAMES == (
        "Dashboard",
        "Rally",
        "Gather",
        "Positions",
        "Alerts",
        "Schedule",
        "Settings",
    )


def test_runtime_log_panel_is_attached_below_notebook_instead_of_to_a_tab():
    pages = BotPagesMixin()
    panel = Mock()
    log = Mock()
    scrollbar = Mock()

    with (
        patch("macro_clicker.bot.ui_pages.ttk.LabelFrame", return_value=panel) as frame,
        patch("macro_clicker.bot.ui_pages.tk.Text", return_value=log) as text,
        patch(
            "macro_clicker.bot.ui_pages.ttk.Scrollbar",
            return_value=scrollbar,
        ) as scroll,
    ):
        pages._build_runtime_log()

    frame.assert_called_once_with(pages, text="Runtime Log", padding=(10, 8))
    panel.pack.assert_called_once_with(fill="x", padx=12, pady=(0, 12))
    text.assert_called_once_with(panel, state="disabled", height=7, wrap="none")
    scroll.assert_called_once_with(
        panel,
        orient="vertical",
        command=log.yview,
    )
    assert pages.runtime_log_panel is panel
    assert pages.bot_log is log


def test_bot_app_keeps_advanced_backends_behind_explicit_hooks():
    assert issubclass(BotApp, App)
    assert callable(BotApp._show_bot_surface)
    assert callable(BotApp._show_advanced_tools)
    assert callable(BotApp._show_alert_setup)
    assert callable(BotApp._show_tool_tab)
    assert callable(BotApp._start_bot_feature)


def test_bot_mode_removes_legacy_scenario_start_hotkey():
    app = BotApp.__new__(BotApp)
    app._advanced_tools_visible = False
    app._start_hotkey_handle = "legacy-handle"
    app._registered_start_hotkey = "f8"
    app._start_hotkey_registration_token = object()

    with patch("macro_clicker.bot_app.keyboard.remove_hotkey") as remove_hotkey:
        assert app._register_start_hotkey() is False

    remove_hotkey.assert_called_once_with("legacy-handle")
    assert app._start_hotkey_handle is None
    assert app._registered_start_hotkey is None
    assert app._start_hotkey_registration_token is None


def test_advanced_mode_delegates_to_existing_start_hotkey_registration():
    app = BotApp.__new__(BotApp)
    app._advanced_tools_visible = True

    with patch.object(
        App,
        "_register_start_hotkey",
        autospec=True,
        return_value=True,
    ) as inherited:
        assert app._register_start_hotkey() is True

    inherited.assert_called_once_with(app)


def test_bot_mode_suppresses_legacy_scenario_auto_start():
    app = BotApp.__new__(BotApp)
    app._advanced_tools_visible = False
    now = datetime(2026, 8, 26, 12, 0)

    with patch.object(App, "_check_auto_start", autospec=True) as inherited:
        assert app._check_auto_start(now) is False

    inherited.assert_not_called()


def test_advanced_mode_delegates_legacy_scenario_auto_start_check():
    app = BotApp.__new__(BotApp)
    app._advanced_tools_visible = True
    now = datetime(2026, 8, 26, 12, 0)

    with patch.object(
        App,
        "_check_auto_start",
        autospec=True,
        return_value=True,
    ) as inherited:
        assert app._check_auto_start(now) is True

    inherited.assert_called_once_with(app, now)
