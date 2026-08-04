"""Status bar showing WiFi, MCP, WebSocket, and SSH connection indicators."""
from PyQt6.QtWidgets import QStatusBar, QLabel
from services.connection_service import ConnectionService


class StatusBarWidget(QStatusBar):
    def __init__(self, connection_service: ConnectionService, parent=None):
        super().__init__(parent)
        self._conn = connection_service
        self.setStyleSheet(
            "QStatusBar { background: #FFFFFF; border-top: 1px solid #E5E6EB; }"
            "QStatusBar::item { border: none; }"
        )
        self.setFixedHeight(32)

        self._indicators: dict[str, QLabel] = {}
        for key, name in [("wifi", "WiFi"), ("mcp", "MCP"), ("ws", "WS"), ("ssh", "SSH")]:
            dot = QLabel(f"  {name}")
            dot.setStyleSheet("color: #86909C; font-size: 12px; background: transparent;")
            self.addPermanentWidget(dot)
            self._indicators[key] = dot

        self._conn.status_changed.connect(self._on_status)

    def _on_status(self, status: dict):
        for key, label in self._indicators.items():
            connected = status.get(key, False)
            color = "#00B42A" if connected else "#F53F3F"
            label.setStyleSheet(f"color: {color}; font-size: 12px; background: transparent;")
