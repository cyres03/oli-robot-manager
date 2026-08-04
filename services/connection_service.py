"""Aggregated connection status for WiFi, MCP, WebSocket, and SSH."""
from PyQt6.QtCore import QObject, pyqtSignal
from network.wifi_manager import WifiManager


class ConnectionService(QObject):
    status_changed = pyqtSignal(dict)  # {wifi, mcp, ws, ssh}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._status = {"wifi": False, "mcp": False, "ws": False, "ssh": False}

    def update_wifi(self, connected: bool):
        self._status["wifi"] = connected
        self.status_changed.emit(dict(self._status))

    def update_mcp(self, connected: bool):
        self._status["mcp"] = connected
        self.status_changed.emit(dict(self._status))

    def update_ws(self, connected: bool):
        self._status["ws"] = connected
        self.status_changed.emit(dict(self._status))

    def update_ssh(self, connected: bool):
        self._status["ssh"] = connected
        self.status_changed.emit(dict(self._status))

    def check_wifi(self):
        connected = WifiManager.is_robot_wifi()
        self.update_wifi(connected)
        return connected

    @property
    def all_connected(self) -> bool:
        return all(self._status.values())
