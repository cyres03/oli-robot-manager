"""
Manages the QWebSocket connection lifecycle on a QThread.
Note: QWebSocket is asynchronous, so we start the connection
and process events via the main Qt event loop.
"""
from PyQt6.QtCore import QThread, pyqtSignal
from network.websocket_client import WebSocketClient


class WebSocketWorker(QThread):
    ws_connected = pyqtSignal(bool)
    ws_message = pyqtSignal(dict)
    ws_error = pyqtSignal(str)

    def __init__(self, url: str, accid: str, parent=None):
        super().__init__(parent)
        self.url = url
        self.accid = accid
        self._client: WebSocketClient | None = None
        self._running = True

    def run(self):
        from PyQt6.QtCore import QEventLoop, QTimer
        # QWebSocket must live in the main thread.
        # This worker acts as a status monitor instead.
        # The actual WebSocket client will be created in the main thread.
        pass

    def stop(self):
        self._running = False
        self.requestInterruption()
