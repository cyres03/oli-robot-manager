from network.wifi_manager import WifiManager
from ui.dialogs.wifi_selector_dialog import WifiSelectorDialog


def test_selector_lists_robot_networks_when_not_connected(qtbot, monkeypatch):
    monkeypatch.setattr(
        WifiManager,
        "scan_robot_networks",
        staticmethod(lambda: [
            {"ssid": "HU_L04_01_091_2.4G", "signal": 95, "security": "WPA2"},
            {"ssid": "HU_L04_01_091_5G", "signal": 85, "security": "WPA2"},
        ]),
    )
    monkeypatch.setattr(
        WifiManager,
        "get_current_ssid",
        staticmethod(lambda: None),
    )

    dialog = WifiSelectorDialog()
    qtbot.addWidget(dialog)

    assert dialog.status_label.text() == "发现 2 个机器人网络"
    assert dialog.list_widget.count() == 2
    assert "HU_L04_01_091_2.4G" in dialog.list_widget.item(0).text()
    assert "HU_L04_01_091_5G" in dialog.list_widget.item(1).text()
    assert "Luna L04" in dialog.list_widget.item(0).text()
    assert all(
        "[已连接]" not in dialog.list_widget.item(index).text()
        for index in range(dialog.list_widget.count())
    )