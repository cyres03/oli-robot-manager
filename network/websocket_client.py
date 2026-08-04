"""
WebSocket client for the Oli robot upper-level protocol (port 5000).
Wraps QWebSocket for persistent connection and JSON message protocol.

Request format:
{
    "accid": "WF_TRON2A_001",
    "title": "request_<action>",
    "timestamp": <unix_ms>,
    "guid": "<uuid-hex>",
    "data": { ... }
}
"""
import json
import uuid
from datetime import datetime
from PyQt6.QtWebSockets import QWebSocket
from PyQt6.QtCore import QObject, pyqtSignal, QUrl


class WebSocketClient(QObject):
    message_received = pyqtSignal(dict)
    connection_changed = pyqtSignal(bool)
    error_occurred = pyqtSignal(str)

    def __init__(self, url: str, accid: str, parent=None):
        super().__init__(parent)
        self.url = QUrl(url)
        self.accid = accid
        self._socket = QWebSocket()
        self._socket.connected.connect(self._on_connected)
        self._socket.disconnected.connect(self._on_disconnected)
        self._socket.textMessageReceived.connect(self._on_message)
        self._socket.errorOccurred.connect(
            lambda e: self.error_occurred.emit(self._socket.errorString())
        )

    def connect(self):
        self._socket.open(self.url)

    def disconnect(self):
        self._socket.close()

    def is_connected(self) -> bool:
        return self._socket.state() == QWebSocket.SocketState.ConnectedState

    def send_request(self, title: str, data: dict | None = None) -> str:
        guid = uuid.uuid4().hex[:32]
        msg = {
            "accid": self.accid,
            "title": title,
            "timestamp": int(datetime.now().timestamp() * 1000),
            "guid": guid,
            "data": data or {},
        }
        self._socket.sendTextMessage(json.dumps(msg))
        return guid

    def _on_message(self, text: str):
        try:
            msg = json.loads(text)
            self.message_received.emit(msg)
        except json.JSONDecodeError:
            self.message_received.emit({"raw": text, "error": "JSON parse failed"})

    def _on_connected(self):
        self.connection_changed.emit(True)

    def _on_disconnected(self):
        self.connection_changed.emit(False)
