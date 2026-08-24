"""QThread that runs robot WebSocket command calls asynchronously."""
from time import monotonic
from PyQt6.QtCore import QThread, pyqtSignal
from network.mcp_client import RobotClient


STOP_WALK_BEFORE_TOOLS = {
    "prepare",
    "standup",
    "sit_down",
    "lie_down",
    "set_motion_engine",
    "execute_dance",
    "execute_motion",
}


class McpWorker(QThread):
    tool_result_ready = pyqtSignal(str, object)
    tool_error = pyqtSignal(str, str)
    mcp_connected = pyqtSignal(bool)

    def __init__(
        self,
        ws_url: str,
        accid: str | None,
        parent=None,
        allowed_tools: frozenset[str] | None = None,
    ):
        super().__init__(parent)
        self.client = RobotClient(ws_url, accid)
        self._allowed_tools = allowed_tools
        self._pending_requests: list[tuple[str, dict]] = []
        self._running = True
        self._last_action_status_requested_at = 0.0

    def call_tool(self, tool_name: str, arguments: dict):
        if not self.client.accid:
            self.tool_error.emit(tool_name, "未识别机器人，命令未发送")
            self.mcp_connected.emit(False)
            return
        if self._allowed_tools is not None and tool_name not in self._allowed_tools:
            self.tool_error.emit(tool_name, "当前机器人型号未开放此能力")
            return

        if tool_name == "set_walk_velocity":
            self._pending_requests = [
                item for item in self._pending_requests if item[0] != "set_walk_velocity"
            ]
        elif tool_name == "get_action_library_status":
            now = monotonic()
            if now - self._last_action_status_requested_at < 1.5:
                return
            self._last_action_status_requested_at = now
            self._pending_requests = [
                item for item in self._pending_requests if item[0] != "get_action_library_status"
            ]
        elif tool_name in STOP_WALK_BEFORE_TOOLS:
            self._pending_requests = [
                item for item in self._pending_requests if item[0] != "set_walk_velocity"
            ]
            self._pending_requests.append((
                "set_walk_velocity",
                {"x": 0.0, "y": 0.0, "yaw": 0.0},
            ))
        self._pending_requests.append((tool_name, arguments))

    def run(self):
        try:
            self.mcp_connected.emit(bool(self.client.accid))
            while self._running and not self.isInterruptionRequested():
                if self._pending_requests:
                    tool_name, args = self._pending_requests.pop(0)
                    try:
                        result = self.client.call_tool(tool_name, args)
                        self.tool_result_ready.emit(tool_name, result)
                    except Exception as e:
                        self.tool_error.emit(tool_name, str(e))
                else:
                    self.msleep(100)
        except Exception as e:
            self.tool_error.emit("connect", str(e))

    def update_accid(self, accid: str | None):
        """Update accid when switching robots — thread-safe."""
        self.update_target(accid, self._allowed_tools)

    def update_target(
        self,
        accid: str | None,
        allowed_tools: frozenset[str] | None,
        ws_url: str | None = None,
    ):
        """Atomically switch identity and capabilities, dropping stale commands."""
        self._pending_requests.clear()
        if ws_url:
            self.client.ws_url = ws_url
        self.client.update_accid(accid)
        self._allowed_tools = allowed_tools
        self.mcp_connected.emit(bool(self.client.accid))

    def stop(self):
        self._running = False
        self.requestInterruption()
        self.wait(3000)
