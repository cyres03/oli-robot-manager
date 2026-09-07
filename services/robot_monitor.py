"""
Persistent WebSocket monitor — receives notify_robot_info in real-time.
Emits parsed status signals for UI display.
"""
import json
import asyncio
import threading
import websockets
from time import monotonic
from PyQt6.QtCore import QThread, pyqtSignal
from config import ROBOT_CONFIG
from models.robot_profile import extract_robot_accid


class RobotMonitor(QThread):
    status_updated = pyqtSignal(dict)  # {robot_status, ability, mode, battery, sn, ...}
    connected = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True
        self._last_status_key = None
        self._last_status_emit_at = 0.0
        self._target_lock = threading.Lock()
        self._target_generation = 0
        self._ws_url = ROBOT_CONFIG.websocket_url
        self._accid = ROBOT_CONFIG.ws_accid

    @property
    def target_generation(self) -> int:
        with self._target_lock:
            return self._target_generation

    def update_target(self, ws_url: str, accid: str):
        next_url = str(ws_url or "")
        next_accid = str(accid or "")
        with self._target_lock:
            if self._ws_url == next_url and self._accid == next_accid:
                return self._target_generation
            self._ws_url = next_url
            self._accid = next_accid
            self._target_generation += 1
            generation = self._target_generation
        self._last_status_key = None
        self.connected.emit(False)
        return generation

    def _target_snapshot(self) -> tuple[int, str, str]:
        with self._target_lock:
            return self._target_generation, self._ws_url, self._accid

    def _target_is_current(self, generation: int, ws_url: str, accid: str) -> bool:
        return self._target_snapshot() == (generation, ws_url, accid)

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._listen())

    async def _listen(self):
        while self._running and not self.isInterruptionRequested():
            try:
                generation, ws_url, accid = self._target_snapshot()
                if not accid or not ws_url:
                    self.connected.emit(False)
                    await asyncio.sleep(5)
                    continue
                async with websockets.connect(
                    ws_url,
                    ping_interval=20, ping_timeout=10,
                    open_timeout=10,
                ) as ws:
                    if not self._target_is_current(generation, ws_url, accid):
                        continue
                    self.connected.emit(True)
                    while self._running:
                        raw = await asyncio.wait_for(ws.recv(), timeout=15)
                        if not self._target_is_current(generation, ws_url, accid):
                            break
                        msg = json.loads(raw)
                        message_accid = str(msg.get("accid", "")).strip()
                        if message_accid and message_accid != accid:
                            continue
                        if msg.get("title") == "notify_robot_info":
                            self._parse_robot_info(
                                msg.get("data", {}),
                                generation,
                                ws_url,
                                accid,
                            )
            except Exception:
                self.connected.emit(False)
                await asyncio.sleep(5)  # Retry after 5s

    def _parse_robot_info(
        self,
        data: dict,
        generation: int,
        ws_url: str,
        accid: str,
    ):
        if not self._target_is_current(generation, ws_url, accid):
            return
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
        reported_accid = extract_robot_accid(str(info.get("sn", "")))
        if reported_accid and reported_accid.upper() != accid.upper():
            return
        info["_target_generation"] = generation
        info["_target_accid"] = accid
        info["_reported_accid"] = reported_accid or ""
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
