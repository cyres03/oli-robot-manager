from types import SimpleNamespace
import time

import network.wifi_manager as wifi_module
from network.wifi_manager import WifiManager


def test_scan_robot_networks_keeps_supported_models(monkeypatch):
    monkeypatch.setattr(
        WifiManager,
        "scan_networks",
        staticmethod(lambda: [
            {"ssid": "HU_D04_01_121_5G", "signal": 80, "security": "WPA2"},
            {"ssid": "HU_L04_01_091_5G", "signal": 85, "security": "WPA2"},
            {"ssid": "HU_X99_01_001_5G", "signal": 70, "security": "WPA2"},
            {"ssid": "WF_TRON2A_001", "signal": 75, "security": "WPA2"},
            {"ssid": "office", "signal": 90, "security": "WPA2"},
        ]),
    )

    assert WifiManager.scan_robot_networks() == [
        {"ssid": "HU_D04_01_121_5G", "signal": 80, "security": "WPA2"},
        {"ssid": "HU_L04_01_091_5G", "signal": 85, "security": "WPA2"},
        {"ssid": "HU_X99_01_001_5G", "signal": 70, "security": "WPA2"},
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


def test_windows_decodes_chinese_netsh_for_single_connected_d04(monkeypatch):
    output = """
    名称                   : WLAN
    描述                   : Realtek Wireless LAN
    状态                   : 已连接
    SSID                   : HU_D04_01_124_5G
    信号                   : 92%
    """.encode("gb18030")
    monkeypatch.setattr(wifi_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        wifi_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=output),
    )

    assert WifiManager.get_robot_ssid() == "HU_D04_01_124_5G"
    assert WifiManager.is_robot_wifi() is True


def test_windows_decodes_utf8_netsh_output(monkeypatch):
    output = """
    Name                   : Wi-Fi
    Description            : Wireless adapter
    State                  : connected
    SSID                   : HU_D04_01_125_5G
    Signal                 : 88%
    """.encode("utf-8")
    monkeypatch.setattr(wifi_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        wifi_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=output),
    )

    assert WifiManager.get_robot_ssid() == "HU_D04_01_125_5G"


def test_windows_decode_prefers_valid_netsh_fields_over_system_codepage(monkeypatch):
    output = "名称 : WLAN\n状态 : 已连接\nSSID : HU_D04_01_127_5G\n信号 : 91%\n".encode("gb18030")
    monkeypatch.setattr(wifi_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        wifi_module.locale,
        "getpreferredencoding",
        lambda _do_setlocale=False: "cp1252",
    )

    decoded = wifi_module._decode_output(output)

    assert "名称 : WLAN" in decoded
    assert "状态 : 已连接" in decoded


def test_windows_chinese_single_adapter_scans_d04_and_l04(monkeypatch):
    interfaces = """
    名称                   : WLAN
    描述                   : Intel Wireless
    状态                   : 已断开连接
    """.encode("gb18030")
    networks = """
    SSID 1 : HU_D04_01_124_5G
        信号 : 86%
    SSID 2 : HU_L04_01_093_5G
        信号 : 78%
    """.encode("gb18030")

    def fake_run(args, **kwargs):
        output = networks if "networks" in args else interfaces
        return SimpleNamespace(stdout=output)

    monkeypatch.setattr(wifi_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(wifi_module.subprocess, "run", fake_run)

    assert WifiManager.scan_robot_networks() == [
        {"ssid": "HU_D04_01_124_5G", "signal": 86, "security": "WPA2"},
        {"ssid": "HU_L04_01_093_5G", "signal": 78, "security": "WPA2"},
    ]


def test_windows_single_adapter_connect_uses_actual_interface(monkeypatch):
    commands = []
    monkeypatch.setattr(
        WifiManager,
        "_get_all_interfaces",
        staticmethod(lambda: [{
            "name": "Wi-Fi",
            "ssid": "",
            "description": "Internal adapter",
            "state": "disconnected",
            "signal": 0,
        }]),
    )
    monkeypatch.setattr(
        wifi_module.subprocess,
        "run",
        lambda args, **kwargs: commands.append(args) or SimpleNamespace(stdout=b""),
    )
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(WifiManager, "get_current_ssid", staticmethod(lambda: "HU_D04_01_126_5G"))

    assert WifiManager._windows_connect("HU_D04_01_126_5G", "password") is True
    connect_command = next(command for command in commands if "connect" in command)
    assert "interface=Wi-Fi" in connect_command


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


def test_connected_robot_ssids_include_all_adapters(monkeypatch):
    monkeypatch.setattr(
        WifiManager,
        "_get_all_interfaces",
        staticmethod(lambda: [
            {"ssid": "office", "state": "connected"},
            {"ssid": "HU_L04_01_091_5G", "state": "connected"},
            {"ssid": "HU_D04_01_121_5G", "state": "connected"},
            {"ssid": "HU_L04_01_091_5G", "state": "connected"},
        ]),
    )

    assert WifiManager.get_connected_robot_ssids() == [
        "HU_L04_01_091_5G",
        "HU_D04_01_121_5G",
    ]


def test_disconnects_only_other_robot_networks(monkeypatch):
    commands = []
    monkeypatch.setattr(wifi_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        WifiManager,
        "_get_all_interfaces",
        staticmethod(lambda: [
            {"name": "wlan0", "ssid": "office"},
            {"name": "wlan1", "ssid": "HU_L04_01_091_5G"},
            {"name": "wlan2", "ssid": "HU_D04_01_121_5G"},
        ]),
    )
    monkeypatch.setattr(
        wifi_module.subprocess,
        "run",
        lambda args, **kwargs: commands.append(args),
    )

    assert WifiManager.disconnect_robot_networks_except("HU_L04_01_091_5G") is True
    assert commands == [["nmcli", "device", "disconnect", "wlan2"]]