import network.wifi_manager as wifi_module
from network.wifi_manager import WifiManager


def test_scan_robot_networks_keeps_supported_models(monkeypatch):
    monkeypatch.setattr(
        WifiManager,
        "scan_networks",
        staticmethod(lambda: [
            {"ssid": "HU_D04_01_121_5G", "signal": 80, "security": "WPA2"},
            {"ssid": "HU_L04_01_091_5G", "signal": 85, "security": "WPA2"},
            {"ssid": "WF_TRON2A_001", "signal": 75, "security": "WPA2"},
            {"ssid": "office", "signal": 90, "security": "WPA2"},
        ]),
    )

    assert WifiManager.scan_robot_networks() == [
        {"ssid": "HU_D04_01_121_5G", "signal": 80, "security": "WPA2"},
        {"ssid": "HU_L04_01_091_5G", "signal": 85, "security": "WPA2"},
        {"ssid": "WF_TRON2A_001", "signal": 75, "security": "WPA2"},
    ]


def test_linux_scan_forces_rescan(monkeypatch):
    commands = []

    def fake_run(args):
        commands.append(args)
        return "HU_L04_01_091_5G:85:WPA2\n"

    monkeypatch.setattr(wifi_module, "_run", fake_run)

    assert WifiManager._linux_scan() == [
        {"ssid": "HU_L04_01_091_5G", "signal": 85, "security": "WPA2"},
    ]
    assert commands == [[
        "nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY",
        "dev", "wifi", "list", "--rescan", "yes",
    ]]


def test_windows_interfaces_include_disconnected_wifi_adapter(monkeypatch):
    output = """
    Name                   : Wi-Fi
    Description            : Internal adapter
    State                  : connected
    SSID                   : office
    Signal                 : 90%

    Name                   : Wi-Fi 2
    Description            : USB adapter
    State                  : disconnected
    """
    monkeypatch.setattr(wifi_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(wifi_module, "_run", lambda args: output)

    assert WifiManager._get_all_interfaces() == [
        {
            "name": "Wi-Fi",
            "ssid": "office",
            "description": "Internal adapter",
            "state": "connected",
            "signal": 90,
        },
        {
            "name": "Wi-Fi 2",
            "ssid": "",
            "description": "USB adapter",
            "state": "disconnected",
            "signal": 0,
        },
    ]


def test_windows_scan_uses_disconnected_wifi_adapter(monkeypatch):
    monkeypatch.setattr(
        WifiManager,
        "_get_all_interfaces",
        staticmethod(lambda: [{
            "name": "Wi-Fi 2",
            "ssid": "",
            "description": "USB adapter",
            "state": "disconnected",
            "signal": 0,
        }]),
    )
    monkeypatch.setattr(
        wifi_module,
        "_run",
        lambda args: "SSID 1 : HU_L04_01_091_5G\n    Signal : 75%\n",
    )

    assert WifiManager._windows_scan() == [
        {"ssid": "HU_L04_01_091_5G", "signal": 75, "security": "WPA2"},
    ]