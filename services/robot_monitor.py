"""
Persistent WebSocket monitor — receives notify_robot_info in real-time.
Emits parsed status signals for UI display.
"""
import json
import asyncio
import websockets
from time import monotonic
from PyQt6.QtCore import QThread, pyqtSignal
from config import ROBOT_CONFIG, detect_accid_from_wifi


class RobotMonitor(QThread):
    status_updated = pyqtSignal(dict)  # {robot_status, ability, mode, battery, sn, ...}
    connected = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True
        self._last_status_key = None
        self._last_status_emit_at = 0.0

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._listen())

    async def _listen(self):
        while self._running and not self.isInterruptionRequested():
            try:
                accid = detect_accid_from_wifi()
                async with websockets.connect(
                    ROBOT_CONFIG.websocket_url,
                    ping_interval=20, ping_timeout=10,
                    open_timeout=10,
                ) as ws:
                    self.connected.emit(True)
                    while self._running:
                        raw = await asyncio.wait_for(ws.recv(), timeout=15)
                        msg = json.loads(raw)
                        if msg.get("title") == "notify_robot_info":
                            self._parse_robot_info(msg.get("data", {}))
            except Exception:
                self.connected.emit(False)
                await asyncio.sleep(5)  # Retry after 5s

    def _parse_robot_info(self, data: dict):
        result = data.get("result", [])
        info = {"battery": "?", "battery_pct": 0, "robot_status": "?",
                "ability": "?", "mode": "?", "sn": "?"}

        for item in result:
            name = item.get("name", "")
            values = item.get("values", [])

            if name == "peripheral":
                for v in values:
                    if v.get("key") == "battery":
                        info["battery_pct"] = int(v.get("value", 0))
                    elif v.get("key") == "bat_vol":
                        info["battery_voltage"] = int(v.get("value", 0)) / 1000

            elif name == "system_info":
                for v in values:
                    k = v.get("key", "")
                    if k == "robot_status":
                        info["robot_status"] = v.get("value", "?")
                    elif k == "ability_running":
                        info["ability"] = v.get("value", "?")
                    elif k == "mode":
                        info["mode"] = v.get("value", "?")
                    elif k == "sn":
                        info["sn"] = v.get("value", "?")
                    elif k == "version":
                        info["version"] = v.get("value", "?")

            elif name == "imu":
                for v in values:
                    if v.get("key") == "InitFail":
                        info["imu_status"] = v.get("value", "?")

        info["battery"] = f"{info['battery_pct']}%"
        status_key = (
            info.get("robot_status"),
            info.get("ability"),
            info.get("mode"),
            info.get("battery_pct"),
            info.get("imu_status"),
        )
        now = monotonic()
        if status_key == self._last_status_key and now - self._last_status_emit_at < 1.0:
            return
        self._last_status_key = status_key
        self._last_status_emit_at = now
        self.status_updated.emit(info)

    def stop(self):
        self._running = False
        self.requestInterruption()
        self.wait(3000)
