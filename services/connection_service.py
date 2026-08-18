"""Aggregated connection status for WiFi, MCP, WebSocket, and SSH."""
from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from network.wifi_manager import WifiManager


class ConnectionService(QObject):
    status_changed = pyqtSignal(dict)  # {wifi, mcp, ws, ssh}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._status = {"wifi": False, "mcp": False, "ws": False, "ssh": False}
        self._network = QNetworkAccessManager(self)
        self._network.finished.connect(self._on_mcp_reply)

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

    def check_mcp(self, url: str):
        request = QNetworkRequest(QUrl(url))
        request.setTransferTimeout(3000)
        self._network.get(request)

    def _on_mcp_reply(self, reply: QNetworkReply):
        status_code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        self.update_mcp(status_code is not None and int(status_code) < 500)
        reply.deleteLater()

    @property
    def all_connected(self) -> bool:
        return all(self._status.values())
