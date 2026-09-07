from config import ROBOT_CONFIG
from models.robot_profile import OLI_PROFILE, TRON2_PROFILE
from PyQt6.QtWidgets import QLineEdit
from ui.panels.settings_panel import SettingsPanel, TRON2_LOCKED_SETTING_KEYS


def test_tron2_profile_locks_managed_topology_fields(qtbot):
    panel = SettingsPanel()
    qtbot.addWidget(panel)

    panel.apply_profile(TRON2_PROFILE)

    assert panel._locked_fields == TRON2_LOCKED_SETTING_KEYS
    for key in TRON2_LOCKED_SETTING_KEYS:
        field = panel._fields[key]
        if isinstance(field, QLineEdit):
            assert field.isReadOnly()
        else:
            assert not field.isEnabled()
        assert "TRON2 产品基线" in field.toolTip()
    assert not panel._fields["wifi_password"].isReadOnly()


def test_tron2_save_does_not_override_locked_topology(qtbot, monkeypatch):
    panel = SettingsPanel()
    qtbot.addWidget(panel)
    monkeypatch.setattr(ROBOT_CONFIG, "websocket_url", "ws://10.192.1.2:5000")
    monkeypatch.setattr(ROBOT_CONFIG, "main_control_ip", "10.192.1.2")
    panel.apply_profile(TRON2_PROFILE)
    emitted = []
    panel.settings_changed.connect(emitted.append)

    panel._fields["websocket_url"].setText("ws://untrusted:5000")
    panel._fields["main_control_ip"].setText("192.0.2.1")
    panel._save_settings()

    assert ROBOT_CONFIG.websocket_url == "ws://10.192.1.2:5000"
    assert ROBOT_CONFIG.main_control_ip == "10.192.1.2"
    assert "websocket_url" not in emitted[-1]
    assert "main_control_ip" not in emitted[-1]


def test_switching_to_oli_restores_editable_settings(qtbot):
    panel = SettingsPanel()
    qtbot.addWidget(panel)

    panel.apply_profile(TRON2_PROFILE)
    panel.apply_profile(OLI_PROFILE)

    assert panel._locked_fields == frozenset()
    assert not panel._fields["websocket_url"].isReadOnly()
    assert panel._fields["expected_cpu_cores"].isEnabled()