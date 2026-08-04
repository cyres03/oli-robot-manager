"""QThread for WiFi scan and connect operations."""
from PyQt6.QtCore import QThread, pyqtSignal
from network.wifi_manager import WifiManager


class WifiWorker(QThread):
    wifi_connected = pyqtSignal(str)
    wifi_error = pyqtSignal(str)
    wifi_status = pyqtSignal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scan_for_robot = False

    def scan_and_connect_to_robot_wifi(self):
        self._scan_for_robot = True
        if not self.isRunning():
            self.start()
        else:
            self._do_connect()

    def check_status(self):
        if not self.isRunning():
            self.start()
        else:
            self._do_check()

    def run(self):
        if self._scan_for_robot:
            self._do_connect()
        else:
            self._do_check()

    def _do_connect(self):
        from config import ROBOT_CONFIG

        ssid = WifiManager.get_current_ssid()

        if ssid and WifiManager._get_pattern().match(ssid):
            self.wifi_connected.emit(ssid)
            return

        try:
            robot_networks = WifiManager.scan_robot_networks()
        except Exception:
            self.wifi_error.emit("无法扫描WiFi网络")
            return

        if not robot_networks:
            self.wifi_error.emit("未发现机器人WiFi网络")
            return

        target = robot_networks[0].get("ssid", "")
        if not target:
            self.wifi_error.emit("未发现机器人WiFi网络")
            return
        success = WifiManager.connect_to_wifi(target, ROBOT_CONFIG.wifi_password)
        if success:
            self.wifi_connected.emit(target)
        else:
            self.wifi_error.emit(f"无法连接到 {target}")

    def _do_check(self):
        ssid = WifiManager.get_current_ssid()
        is_robot = ssid is not None and WifiManager._get_pattern().match(ssid)
        self.wifi_status.emit(is_robot, ssid or "")
