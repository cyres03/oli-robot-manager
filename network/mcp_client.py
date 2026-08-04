"""
Robot command client using WebSocket protocol (port 5000).

Protocol: JSON over WebSocket — request_<action> / response_<action> / notify_<event>

API Reference (per SDK doc):
  4.4.1  request_connect_wifi          → response_connect_wifi
  4.4.2  request_wifi_connection_status→ response_wifi_connection_status
  4.4.3  request_prepare               → response_prepare
  4.4.4  request_set_walk_mode         → response_set_walk_mode
  4.4.5  request_set_walk_vel          → response_set_walk_vel
  4.4.6  request_set_walk_vel_sync     → response_set_walk_vel_sync
  4.4.7  request_damping               → response_damping
  4.4.8  request_zero_torque           → response_zero_torque
  4.4.12 request_calibrate             → response_calibrate
  4.4.13 request_enter_dance_mode      → response_enter_dance_mode
  4.4.13 request_get_dance_list        → response_get_dance_list
  4.4.13 request_dance                 → response_dance → notify_dance
"""
import json
import uuid
import asyncio
import os
import time
import websockets
from datetime import datetime
from config import ROBOT_CONFIG


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _is_walk_mode_ok(data: dict) -> bool:
    if data.get("result") == "success":
        return True
    return data.get("result") == "fail_state_not_allowed" and data.get("current_state") == "Walk"


class RobotClient:
    """Synchronous wrapper around WebSocket for robot commands."""

    def __init__(self, ws_url: str, accid: str):
        self.ws_url = ws_url
        self.accid = accid

    def update_accid(self, accid: str):
        """Update accid when switching to a different robot."""
        self.accid = accid

    def _audit(self, event: str, title: str, guid: str, data: dict | None = None, result: dict | None = None):
        try:
            log_dir = os.path.join(os.path.expanduser("~"), "AppData", "Local", "OliRobotManager", "audit")
            os.makedirs(log_dir, exist_ok=True)
            record = {
                "time": datetime.now().isoformat(timespec="milliseconds"),
                "event": event,
                "accid": self.accid,
                "title": title,
                "guid": guid,
                "data": data or {},
                "result": result or {},
            }
            with open(os.path.join(log_dir, "sdk_requests.jsonl"), "a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _send_request(self, title: str, data: dict | None = None, timeout: float = 10.0) -> dict:
        async def _do():
            async with websockets.connect(self.ws_url) as ws:
                guid = uuid.uuid4().hex[:32]
                payload = data or {}
                self._audit("send", title, guid, payload)
                await ws.send(json.dumps({
                    "accid": self.accid, "title": title,
                    "timestamp": int(datetime.now().timestamp() * 1000),
                    "guid": guid, "data": payload,
                }))
                response_title = title.replace("request_", "response_")
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                        msg = json.loads(raw)
                        if msg.get("title") == response_title and msg.get("guid") == guid:
                            self._audit("response", response_title, guid, payload, msg.get("data", {}))
                            return msg
                    except Exception as exc:
                        self._audit("error", title, guid, payload, {"error": str(exc)})
                        raise
        return asyncio.run(_do())

    def _send_command(self, title: str, data: dict | None = None, failure_timeout: float = 0.3) -> dict:
        async def _do():
            async with websockets.connect(self.ws_url) as ws:
                guid = uuid.uuid4().hex[:32]
                payload = data or {}
                self._audit("send_no_response_expected", title, guid, payload)
                await ws.send(json.dumps({
                    "accid": self.accid, "title": title,
                    "timestamp": int(datetime.now().timestamp() * 1000),
                    "guid": guid, "data": payload,
                }))
                response_title = title.replace("request_", "response_")
                deadline = asyncio.get_running_loop().time() + failure_timeout
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    except asyncio.TimeoutError:
                        break
                    msg = json.loads(raw)
                    if msg.get("title") == response_title and msg.get("guid") == guid:
                        result = msg.get("data", {})
                        self._audit("response", response_title, guid, payload, result)
                        return {"data": result}
                self._audit("sent_no_response_observed", title, guid, payload, {"result": "sent"})
                return {"data": {"result": "sent"}}
        return asyncio.run(_do())

    def _send_request_with_notify(
        self, title: str, data: dict, notify_title: str, timeout: float = 120.0
    ) -> (dict, dict):
        async def _do():
            async with websockets.connect(self.ws_url) as ws:
                guid = uuid.uuid4().hex[:32]
                payload = data or {}
                self._audit("send", title, guid, payload)
                await ws.send(json.dumps({
                    "accid": self.accid, "title": title,
                    "timestamp": int(datetime.now().timestamp() * 1000),
                    "guid": guid, "data": payload,
                }))
                response_title = title.replace("request_", "response_")
                response_msg = None
                notify_msg = None
                deadline = asyncio.get_running_loop().time() + timeout
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        self._audit("notify_timeout", title, guid, payload, {
                            "response": response_msg.get("data", {}) if response_msg else None,
                            "notify_title": notify_title,
                        })
                        if response_msg:
                            return response_msg, notify_msg
                        raise TimeoutError(f"{title} {timeout:.0f}s 内未收到 response/notify")
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                        msg = json.loads(raw)
                    except Exception as exc:
                        self._audit("error", title, guid, payload, {"error": str(exc)})
                        raise
                    if msg.get("title") == response_title and msg.get("guid") == guid:
                        response_msg = msg
                        self._audit("response", response_title, guid, payload, msg.get("data", {}))
                        result = msg.get("data", {}).get("result")
                        if result and result != "success":
                            return response_msg, notify_msg
                    if msg.get("title") == notify_title:
                        notify_msg = msg
                        self._audit("notify", notify_title, guid, payload, msg.get("data", {}))
                    if response_msg and notify_msg:
                        return response_msg, notify_msg
        return asyncio.run(_do())

    def initialize(self) -> bool:
        try:
            return True
        except Exception:
            return False

    # ---- 4.4.1 WiFi ----

    def connect_wifi(self, ssid: str, password: str, band: str = "0",
                     router_admin_password: str | None = None) -> dict:
        """Request robot to connect to a WiFi hotspot."""
        router_admin_password = router_admin_password or ROBOT_CONFIG.router_admin_password
        resp = self._send_request("request_connect_wifi", {
            "wifi_band": band, "wifi_ssid": ssid,
            "wifi_password": password, "router_admin_password": router_admin_password,
        })
        return resp.get("data", {})

    # ---- 4.4.2 WiFi Status ----

    def wifi_connection_status(self, router_admin_password: str | None = None) -> dict:
        router_admin_password = router_admin_password or ROBOT_CONFIG.router_admin_password
        resp = self._send_request("request_wifi_connection_status", {
            "router_admin_password": router_admin_password,
        })
        return resp.get("data", {})

    # ---- 4.4.3 Prepare (ready stance) ----

    def prepare(self) -> bool:
        resp = self._send_request("request_prepare", {})
        return resp.get("data", {}).get("result") == "success"

    # ---- 4.4.4 Walk Mode ----

    def set_walk_mode(self) -> bool:
        resp = self._send_request("request_set_walk_mode", {})
        return _is_walk_mode_ok(resp.get("data", {}))

    # ---- 4.4.5 / 4.4.6 Walking ----

    def set_walk_velocity(self, x: float, y: float, yaw: float, sync: bool = False) -> dict:
        """Control walking. sync=True uses the sync interface (4.4.6)."""
        title = "request_set_walk_vel_sync" if sync else "request_set_walk_vel"
        resp = self._send_command(title, {
            "x": _clamp(float(x), -1.0, 1.0),
            "y": _clamp(float(y), -1.0, 1.0),
            "yaw": _clamp(float(yaw), -1.0, 1.0),
        })
        return resp.get("data", {})

    def safe_stop_to_damping(self) -> dict:
        stop = self.set_walk_velocity(0.0, 0.0, 0.0, sync=True)
        return {
            "stop_walk": stop.get("result", "success"),
            "damping": "not_sent_by_safe_stop",
        }

    # ---- 4.4.7 Damping ----

    def damping(self) -> bool:
        resp = self._send_request("request_damping", {})
        return resp.get("data", {}).get("result") == "success"

    # ---- 4.4.8 Zero Torque ----

    def zero_torque(self) -> bool:
        resp = self._send_request("request_zero_torque", {})
        return resp.get("data", {}).get("result") == "success"

    # ---- 4.4.9 Sit Down ----

    def sit_down(self) -> bool:
        resp = self._send_request("request_from_stand_to_sit", {})
        return resp.get("data", {}).get("result") == "success"

    # ---- 4.4.10 Stand Up ----

    def standup(self, mode: str = "lying") -> bool:
        resp = self._send_request("request_standup", {"mode": mode})
        return resp.get("data", {}).get("result") == "success"

    # ---- 4.4.11 Lie Down ----

    def lie_down(self) -> bool:
        resp = self._send_request("request_lie_down", {})
        return resp.get("data", {}).get("result") == "success"

    # ---- 4.4.12 Calibration ----

    def calibrate(self, timeout: float = 20.0) -> dict:
        """Calibrate and wait for notify_calibrate completion."""
        try:
            resp, notify = self._send_request_with_notify(
                "request_calibrate", {}, "notify_calibrate", timeout=timeout,
            )
        except TimeoutError:
            return {
                "response": None,
                "notify": None,
                "error": f"request_calibrate {timeout:.0f}s 内未收到 response/notify；当前固件可用路径是遥控器 L1+R1",
            }
        return {
            "response": resp.get("data", {}).get("result") if resp else None,
            "notify": notify.get("data", {}).get("result") if notify else None,
        }

    # ---- 4.4.13 Dance ----

    def enter_dance_mode(self, mode: int = 1) -> bool:
        resp = self._send_request("request_enter_dance_mode", {"mode": mode})
        return resp.get("data", {}).get("result") == "success"

    def get_dance_list(self) -> list[dict]:
        resp = self._send_request("request_get_dance_list", {})
        data = resp.get("data", {})
        if data.get("result") == "success":
            return data.get("dances", [])
        return []

    def execute_dance(self, rc_mapping: str, timeout: float = 120.0) -> dict:
        resp, notify = self._send_request_with_notify(
            "request_dance", {"name": rc_mapping}, "notify_dance", timeout=timeout,
        )
        return {
            "response": resp.get("data", {}).get("result") if resp else None,
            "notify": notify.get("data", {}).get("result") if notify else None,
        }

    # ---- 4.4.15 Action Library ----

    def get_action_library_status(self) -> dict:
        """Returns {action_library_mode, action_library_state, result}."""
        resp = self._send_request("request_get_action_library_status", {})
        return resp.get("data", {})

    def set_motion_engine(self, mode: int = 1) -> bool:
        """Enter (1) or exit (0) motion engine mode."""
        resp = self._send_request("request_set_motion_engine", {"mode": mode})
        return resp.get("data", {}).get("result") == "success"

    def _wait_action_library_mode(self, timeout: float = 3.0, interval: float = 0.2) -> dict:
        deadline = time.monotonic() + timeout
        last_status = {}
        while time.monotonic() < deadline:
            last_status = self.get_action_library_status()
            if last_status.get("action_library_mode") == "action_library":
                return last_status
            time.sleep(interval)
        return last_status

    def _ensure_action_library_mode(self) -> dict:
        status = self.get_action_library_status()
        if status.get("action_library_mode") == "action_library":
            return {"status": status, "entered": False, "result": "already_action_library"}
        entered = self.set_motion_engine(1)
        ready_status = self._wait_action_library_mode() if entered else {}
        ready = ready_status.get("action_library_mode") == "action_library"
        return {
            "status": status,
            "entered": entered,
            "ready_status": ready_status,
            "result": "success" if entered and ready else "fail",
        }

    def _restore_walk_mode_after_action(self) -> dict:
        exited = self.set_motion_engine(0)
        walk_mode = self.set_walk_mode()
        return {
            "exit_motion_engine": "success" if exited else "fail",
            "set_walk_mode": "success" if walk_mode else "fail",
        }

    def get_atomic_motion_list(self) -> list[dict]:
        """Get available atomic motions. Returns [{motion_index, motion_name_cn, motion_name_en}, ...]."""
        resp = self._send_request("request_get_atomic_motion_list", {})
        data = resp.get("data", {})
        if data.get("result") == "success":
            return data.get("motion_list", [])
        return []

    def execute_atomic_motion(self, motion_name: str, timeout: float = 60.0) -> dict:
        """Execute an atomic motion. Waits for notify_execute_atomic_motion."""
        resp, notify = self._send_request_with_notify(
            "request_execute_atomic_motion", {"motion_name": motion_name},
            "notify_execute_atomic_motion", timeout=timeout,
        )
        return {
            "response": resp.get("data", {}).get("result") if resp else None,
            "notify": notify.get("data", {}).get("result") if notify else None,
        }

    def execute_action_sync(self, names: list[str]) -> dict:
        """Execute multiple dances/motions at once (comma-separated names)."""
        resp = self._send_request("request_action_sync", {"name": ",".join(names)})
        return resp.get("data", {})

    # ---- 4.4.16 Mobile (UB) Manipulation ----

    def set_ub_manip_mode(self, mode: int = 1) -> dict:
        """0=prepare, 1=track ee pose, 2=exit."""
        resp = self._send_request("request_set_ub_manip_mode", {"mode": mode})
        return resp.get("data", {})

    def set_ub_manip_ee_pose(self, left_hand_pos: list, left_hand_quat: list,
                              right_hand_pos: list, right_hand_quat: list,
                              head_quat: list = None) -> dict:
        data = {
            "left_hand_pos": left_hand_pos, "left_hand_quat": left_hand_quat,
            "right_hand_pos": right_hand_pos, "right_hand_quat": right_hand_quat,
        }
        if head_quat is not None:
            data["head_quat"] = head_quat
        resp = self._send_request("request_set_ub_manip_ee_pose", data)
        return resp.get("data", {})

    def get_ub_manip_ee_pose(self) -> dict:
        resp = self._send_request("request_get_ub_manip_ee_pose", {})
        return resp.get("data", {})

    # ---- 4.4.17 Whole-Body (WB) Manipulation ----

    def set_wb_manip_mode(self, mode: int = 1) -> dict:
        """0=prepare, 1=track ee pose, 2=exit."""
        resp = self._send_request("request_set_wb_manip_mode", {"mode": mode})
        return resp.get("data", {})

    def set_wb_manip_ee_pose(self, left_hand_pos: list, left_hand_quat: list,
                              right_hand_pos: list, right_hand_quat: list) -> dict:
        resp = self._send_request("request_set_wb_manip_ee_pose", {
            "left_hand_pos": left_hand_pos, "left_hand_quat": left_hand_quat,
            "right_hand_pos": right_hand_pos, "right_hand_quat": right_hand_quat,
        })
        return resp.get("data", {})

    def get_wb_manip_ee_pose(self) -> dict:
        resp = self._send_request("request_get_wb_manip_ee_pose", {})
        return resp.get("data", {})

    # ---- 4.4.20 Joint State ----

    def get_joint_state(self) -> dict:
        resp = self._send_request("request_get_joint_state", {})
        return resp.get("data", {})

    # ---- 4.4.21 IMU Data ----

    def get_imu_data(self) -> dict:
        resp = self._send_request("request_get_imu_data", {})
        return resp.get("data", {})

    # ---- 4.4.22 LED Control ----

    def enable_led_control(self, enable: bool = True) -> dict:
        resp = self._send_request("request_enable_led_control", {"enable": 1 if enable else 0})
        return resp.get("data", {})

    def led_control(self, led_index: int = 0, led_state: int = 0, led_color: int = 7) -> dict:
        resp = self._send_request("request_led_control", {
            "led_index": max(0, min(5, int(led_index))),
            "led_state": max(0, min(6, int(led_state))),
            "led_color": max(0, min(7, int(led_color))),
        })
        return resp.get("data", {})

    # ---- 4.6.8 Audio Wakeup Control ----

    def audio_wakeup_control(self, enable: bool = True) -> dict:
        resp = self._send_request("request_audio_wakeup_control", {"enable": 1 if enable else 0})
        return resp.get("data", {})

    # ---- 4.6.9 Set Wakeup Word ----

    def audio_set_wakeup_word(self, word: str, pinyin: str = "",
                               thresh: float = 0.38, greeting: str = "",
                               subsets: str = "") -> dict:
        data = {"word": word}
        if pinyin:
            data["pinyin"] = pinyin
        data["thresh"] = str(thresh)
        if greeting:
            data["greeting"] = greeting
        if subsets:
            data["subsets"] = subsets
        resp = self._send_request("request_audio_set_wakeup_word", data)
        return resp.get("data", {})

    # ---- 4.6.10 Get Wakeup Word ----

    def audio_get_wakeup_word(self) -> dict:
        resp = self._send_request("request_audio_get_wakeup_word", {})
        return resp.get("data", {})

    # ---- 4.6.11 Set Volume ----

    def audio_set_volume(self, volume: int = 50) -> dict:
        resp = self._send_request("request_audio_set_volume", {"volume": max(0, min(100, volume))})
        return resp.get("data", {})

    # ---- 4.9 Dual-Arm / Upper Body Control (from examples) ----

    def set_move_mode(self, mode: int = 0) -> dict:
        """Servo control mode: 0/1/2."""
        resp = self._send_request("request_set_move_mode", {"mode": mode})
        return resp.get("data", {})

    def move_joint(self, left: list = None, right: list = None,
                   head_pitch: float = None, head_yaw: float = None,
                   torso_height: float = None, torso_pitch: float = None,
                   torso_roll: float = None, torso_yaw: float = None,
                   speed: float = 0.1) -> dict:
        """Joint-space motion. Supports arm joints, head, and waist."""
        data = {"speed": _clamp(float(speed), 0.0, 0.5)}
        if left is not None:
            data["left"] = left
        if right is not None:
            data["right"] = right
        if head_pitch is not None:
            data["head_pitch"] = head_pitch
        if head_yaw is not None:
            data["head_yaw"] = head_yaw
        if torso_height is not None:
            data["torso_height"] = torso_height
        if torso_pitch is not None:
            data["torso_pitch"] = torso_pitch
        if torso_roll is not None:
            data["torso_roll"] = torso_roll
        if torso_yaw is not None:
            data["torso_yaw"] = torso_yaw
        resp = self._send_request("request_moveJ", data)
        return resp.get("data", {})

    def move_cartesian(self, left_position: list = None, left_quat: list = None,
                       right_position: list = None, right_quat: list = None,
                       speed: float = 0.1) -> dict:
        """Cartesian-space motion for end-effectors."""
        data = {"speed": _clamp(float(speed), 0.0, 0.5)}
        if left_position is not None:
            data["left_position"] = left_position
        if left_quat is not None:
            data["left_quat"] = left_quat
        if right_position is not None:
            data["right_position"] = right_position
        if right_quat is not None:
            data["right_quat"] = right_quat
        resp = self._send_request("request_moveP", data)
        return resp.get("data", {})

    def set_claw_cmd(self, left_opening: int = 100, left_speed: int = 500,
                      left_force: int = 500, left_mode: int = 1,
                      right_opening: int = 100, right_speed: int = 500,
                      right_force: int = 500, right_mode: int = 1) -> dict:
        resp = self._send_request("request_set_claw_cmd", {
            "left_opening": left_opening, "left_speed": left_speed,
            "left_force": left_force, "left_mode": left_mode,
            "right_opening": right_opening, "right_speed": right_speed,
            "right_force": right_force, "right_mode": right_mode,
        })
        return resp.get("data", {})

    def get_claw_state(self) -> dict:
        resp = self._send_request("request_get_claw_state", {})
        return resp.get("data", {})

    def get_move_pose(self) -> dict:
        """Get current end-effector pose."""
        resp = self._send_request("request_get_move_pose", {})
        return resp.get("data", {})

    # ---- 4.8 Notifications (server push) ----

    # notify_robot_info  — periodic status (battery, system, motor)
    #   data.result[] → {level, name, message, hardware_id, values: [{key, value}]}
    #   Key names: battery, peripheral, system_info, ethercat, imu, ...
    #
    # notify_joy_data    — remote controller axes + buttons
    #   data: {axes: [], buttons: []}

    # ---- MCP worker compatibility (used by services) ----

    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Map internal tool names to WebSocket requests. Used by McpWorker."""
        title_map = {
            "calibrate": "request_calibrate",
            "get_dances": "request_get_dance_list",
            "get_motions": "request_get_atomic_motion_list",
            "execute_dance": "request_dance",
            "execute_motion": "request_execute_atomic_motion",
            "set_walk_velocity": "request_set_walk_vel",
            "set_walk_mode": "request_set_walk_mode",
            "set_motion_engine": "request_set_motion_engine",
            "get_action_library_status": "request_get_action_library_status",
            "prepare": "request_prepare",
            "damping": "request_damping",
            "zero_torque": "request_zero_torque",
            "sit_down": "request_from_stand_to_sit",
            "standup": "request_standup",
            "lie_down": "request_lie_down",
            "safe_stop": "request_set_walk_vel_sync",
            "audio_get_wakeup": "request_audio_get_wakeup_word",
            "audio_wakeup_control": "request_audio_wakeup_control",
            "audio_set_volume": "request_audio_set_volume",
            "enable_led_control": "request_enable_led_control",
            "led_control": "request_led_control",
        }
        ws_title = title_map.get(tool_name)
        if not ws_title:
            return {"success": False, "content": [f"Unknown tool: {tool_name}"]}

        # Pass through all arguments by default, override for specific mappings
        ws_data = dict(arguments)
        if tool_name in ("execute_dance",):
            ws_data = {"name": arguments.get("dance_name", "")}
        elif tool_name in ("execute_motion",):
            ws_data = {"motion_name": arguments.get("motion_name", "")}
        elif tool_name == "set_walk_velocity":
            ws_data = {
                "x": _clamp(float(arguments.get("x", 0)), -1.0, 1.0),
                "y": _clamp(float(arguments.get("y", 0)), -1.0, 1.0),
                "yaw": _clamp(float(arguments.get("yaw", 0)), -1.0, 1.0),
            }
            ws_title = "request_set_walk_vel_sync"
        elif tool_name == "audio_wakeup_control":
            ws_data = {"enable": 1 if arguments.get("enable", True) else 0}
        elif tool_name == "audio_set_volume":
            ws_data = {"volume": max(0, min(100, int(arguments.get("volume", 50))))}
        elif tool_name == "enable_led_control":
            ws_data = {"enable": 1 if arguments.get("enable", True) else 0}
        elif tool_name == "led_control":
            ws_data = {
                "led_index": max(0, min(5, int(arguments.get("led_index", 0)))),
                "led_state": max(0, min(6, int(arguments.get("led_state", 0)))),
                "led_color": max(0, min(7, int(arguments.get("led_color", 7)))),
            }
        elif tool_name == "standup":
            ws_data = {"mode": arguments.get("mode", "lying")}

        try:
            if tool_name == "calibrate":
                # calibrate waits for notify_calibrate
                result = self.calibrate()
                success = result.get("response") == "success" and result.get("notify") == "success"
                return {"success": success, "content": [json.dumps(result)]}
            if tool_name == "safe_stop":
                result = self.safe_stop_to_damping()
                return {"success": result.get("stop_walk") in {"success", "sent"}, "content": [json.dumps(result)]}
            if tool_name == "standup":
                resp = self._send_request(ws_title, ws_data, timeout=30.0)
                data = resp.get("data", {})
                result = dict(data)
                walk_mode_ok = True
                if data.get("result") == "success" and arguments.get("enter_walk_after", False):
                    walk_mode_ok = self.set_walk_mode()
                    result["set_walk_mode"] = "success" if walk_mode_ok else "fail"
                return {"success": data.get("result") == "success" and walk_mode_ok, "content": [json.dumps(result)]}
            if tool_name == "set_walk_velocity":
                resp = self._send_command(ws_title, ws_data)
                return {"success": True, "content": [json.dumps(resp.get("data", {}))]}
            if tool_name == "execute_dance":
                if arguments.get("ensure_action_library", True):
                    result = {"pre_action": self._ensure_action_library_mode()}
                    if result["pre_action"].get("result") == "fail":
                        return {"success": False, "content": [json.dumps(result)]}
                else:
                    result = {}
                result.update(self.execute_dance(ws_data.get("name", ""), timeout=240.0))
                success = result.get("response") == "success" and result.get("notify") == "success"
                if success and arguments.get("restore_walk_mode", True):
                    result["post_action"] = self._restore_walk_mode_after_action()
                return {"success": success, "content": [json.dumps(result)]}
            if tool_name == "execute_motion":
                if arguments.get("ensure_action_library", True):
                    result = {"pre_action": self._ensure_action_library_mode()}
                    if result["pre_action"].get("result") == "fail":
                        return {"success": False, "content": [json.dumps(result)]}
                else:
                    result = {}
                result.update(self.execute_atomic_motion(ws_data.get("motion_name", ""), timeout=45.0))
                success = result.get("response") == "success" and result.get("notify") == "success"
                if success and arguments.get("restore_walk_mode", True):
                    result["post_action"] = self._restore_walk_mode_after_action()
                return {"success": success, "content": [json.dumps(result)]}
            resp = self._send_request(ws_title, ws_data)
            result = resp.get("data", {}).get("result", "fail")
            return {"success": result == "success", "content": [json.dumps(resp.get("data", {}))]}
        except Exception as e:
            return {"success": False, "content": [str(e)]}

    def get_dances(self) -> list:
        """Legacy compat: returns rc_mapping names."""
        return [d.get("rc_mapping", d.get("english_name", "")) for d in self.get_dance_list()]
