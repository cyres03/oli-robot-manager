"""
Calibration service — two types:
  1. MissionEngine calibrate (same state-machine path as remote L1+R1)
  2. Backlash test (launch external backlash-console-v0.5.exe)
"""
import os
import re
import subprocess
import sys
import time
from PyQt6.QtCore import QObject, QThread, pyqtSignal
import httpx
from config import ROBOT_CONFIG
from network.mcp_client import RobotClient
from network.ssh_client import (
    SshAuthenticationError,
    SshClient,
    SshRobotMismatchError,
    current_robot_id,
)
from workers.mcp_worker import McpWorker


BACKLASH_EXE_PATH = r"C:\Users\Limx\Downloads\backlash-console-v0.5.exe"
BACKLASH_BASE_URL = "http://127.0.0.1:8080"
BACKLASH_LEGACY_PAYLOAD_PATH = os.path.join(
    os.path.expanduser("~"), "AppData", "Local", "OliRobotManager", "backlash", "backlash_install.zip"
)
BACKLASH_RESULT_DIR = os.path.join(
    os.path.expanduser("~"), "AppData", "Local", "OliRobotManager", "backlash", "results"
)
MISSION_ENGINE_SWITCH_SERVICE = "/mission_engine/switch_state"
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_MROSSERVICE_LOG_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} .* [A-Z]/mrosservice\(\d+/\d+\):"
)
_MROS_ENV = (
    "export LD_LIBRARY_PATH=/opt/limx/install/lib; "
    "export PATH=/opt/limx/install/bin:$PATH; "
    "export MROS_IP_LIST=10.192.1.x; "
    "export MROS_ETC_PATH=/opt/limx/install/etc; "
    "export MROS_BIN_PATH=/opt/limx/install/bin; "
    "export MROS_LIB_PATH=/opt/limx/install/lib; "
    "export MROS_PKG_PATH=/opt/limx/install; "
    "export MROS_SIM_TIME=0; "
)


def _mros_command(command: str) -> str:
    return _MROS_ENV + command


def _mros_output(stdout: str, stderr: str) -> str:
    return "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)


def _mros_payload_lines(output: str) -> list[str]:
    lines = []
    for raw_line in _ANSI_ESCAPE_RE.sub("", output).splitlines():
        line = raw_line.strip()
        if not line or line == "Wait a moment..." or _MROSSERVICE_LOG_RE.match(line):
            continue
        lines.append(line)
    return lines


def _mros_service_names(output: str) -> set[str]:
    return {
        match.group(1)
        for line in _mros_payload_lines(output)
        if (match := re.match(r"^\*\s+(\S+)\s+\[type:", line))
    }


def _mros_call_succeeded(output: str) -> bool:
    payload = "\n".join(_mros_payload_lines(output))
    if re.search(r"cannot find service|\berror\b|\bfailed\b", payload, re.IGNORECASE):
        return False
    return bool(
        re.search(
            r"(?:['\"]?result['\"]?\s*:\s*['\"]success['\"]|\bsuccess\b)",
            payload,
            re.IGNORECASE,
        )
    )


def _summarize_mros_output(output: str, max_lines: int = 12) -> str:
    lines = _mros_payload_lines(output)
    summary = "\n".join(lines[-max_lines:])
    return summary[:1200] + ("..." if len(summary) > 1200 else "")


def _resource_path(relative_path: str) -> str:
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(base_dir, relative_path)


def _backlash_payload_path() -> str:
    packaged_path = _resource_path(os.path.join("resources", "backlash", "backlash_install.zip"))
    if os.path.exists(packaged_path):
        return packaged_path
    return BACKLASH_LEGACY_PAYLOAD_PATH


class MissionEngineCalibrateWorker(QThread):
    finished = pyqtSignal(bool, str)
    authentication_required = pyqtSignal(str, str, str)

    def __init__(self, robot_id: str, parent=None):
        super().__init__(parent)
        self.robot_id = robot_id
        self.host = ROBOT_CONFIG.main_control_ip
        self.username = ROBOT_CONFIG.main_control_user

    def run(self):
        connected_robot_id = current_robot_id()
        if connected_robot_id != self.robot_id:
            self.finished.emit(
                False,
                f"当前机器人是 {connected_robot_id or '未知'}，"
                f"校零目标是 {self.robot_id}，操作已取消",
            )
            return

        client = SshClient(
            self.host,
            self.username,
            list(ROBOT_CONFIG.main_control_passwords),
            robot_id=self.robot_id,
        )
        robot_client: RobotClient | None = None
        led_disabled = False
        led_restore_required = False
        try:
            client.connect(timeout=8)
            service_result = client.execute(
                _mros_command("/opt/limx/install/bin/mrosservice list"),
                timeout=12,
            )
            service_output = _mros_output(
                service_result.stdout,
                service_result.stderr,
            )
            if service_result.exit_code != 0:
                detail = _summarize_mros_output(service_output) or "未返回服务列表"
                self.finished.emit(
                    False,
                    f"MissionEngine 接口预检失败(exit={service_result.exit_code})，"
                    f"未执行校零，也未关闭 SDK LED。\n{detail}",
                )
                return
            if MISSION_ENGINE_SWITCH_SERVICE not in _mros_service_names(service_output):
                self.finished.emit(
                    False,
                    "当前固件未提供完整校零接口 "
                    f"{MISSION_ENGINE_SWITCH_SERVICE}，未执行校零，也未关闭 SDK LED。\n"
                    "检测到 /joint/calibration 仅是 MissionEngine 内部步骤；"
                    "为避免绕过停能力、阻尼和零力矩保护，软件不会直接调用。\n"
                    "请使用实体遥控器 L1+R1 执行完整校零。",
                )
                return

            robot_client = RobotClient(ROBOT_CONFIG.websocket_url, self.robot_id)
            try:
                led_restore_required = True
                led_result = robot_client.enable_led_control(False)
                led_disabled = led_result.get("result") == "success"
                led_detail = (
                    f"SDK LED 控制已关闭: {led_result}"
                    if led_disabled
                    else f"SDK LED 控制未关闭，继续尝试校零: {led_result}"
                )
            except Exception as error:
                led_detail = f"SDK LED 控制关闭失败，继续尝试校零: {error}"

            command = _mros_command(
                "/opt/limx/install/bin/mrosservice call /mission_engine/switch_state "
                "std_srvs/SetString '{\"data\":\"Calibration\"}'"
            )
            result = client.execute(command, timeout=30)
            output = _mros_output(result.stdout, result.stderr)
            summary = _summarize_mros_output(output) or "未返回业务响应"
            if result.exit_code != 0:
                restore_detail = self._restore_led_control(
                    robot_client,
                    led_restore_required,
                )
                self.finished.emit(
                    False,
                    f"{led_detail}\nMissionEngine 调用失败(exit={result.exit_code}): "
                    f"{summary}{restore_detail}",
                )
                return
            if not _mros_call_succeeded(output):
                restore_detail = self._restore_led_control(
                    robot_client,
                    led_restore_required,
                )
                self.finished.emit(
                    False,
                    f"{led_detail}\nMissionEngine 未返回成功响应: "
                    f"{summary}{restore_detail}",
                )
                return
            self.finished.emit(
                True,
                f"{led_detail}\n已进入 MissionEngine Calibration，"
                f"机器人应显示校零中并使用蓝色灯语。\n{summary}",
            )
        except SshAuthenticationError:
            self.authentication_required.emit(
                self.host,
                self.username,
                self.robot_id,
            )
            self.finished.emit(False, "当前机器人尚未授权 SSH 密钥，请完成一次密码验证")
        except SshRobotMismatchError as error:
            self.finished.emit(False, str(error))
        except Exception as error:
            restore_detail = (
                self._restore_led_control(robot_client, led_restore_required)
                if robot_client is not None
                else ""
            )
            self.finished.emit(False, f"{error}{restore_detail}")
        finally:
            client.close()

    @staticmethod
    def _restore_led_control(
        robot_client: RobotClient,
        led_restore_required: bool,
    ) -> str:
        if not led_restore_required:
            return ""
        try:
            result = robot_client.enable_led_control(True)
            return f"\nSDK LED 控制已恢复: {result}"
        except Exception as error:
            return f"\nSDK LED 控制恢复失败: {error}"


class BacklashConsoleWorker(QThread):
    finished = pyqtSignal(str, bool, str, object)

    def __init__(self, operation: str, payload: dict | None = None, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.payload = payload or {}

    def run(self):
        try:
            if self.operation == "launch":
                state = self._ensure_console()
                self.finished.emit(self.operation, True, "Backlash 控制台已就绪", state)
            elif self.operation == "state":
                self.finished.emit(self.operation, True, "Backlash 状态已刷新", self._get_state())
            elif self.operation == "connect":
                self._ensure_console()
                state = self._post_json("/api/connect", self.payload)
                self.finished.emit(self.operation, True, "已自动填入 SSH 信息并连接 Backlash 控制台", state)
            elif self.operation == "start":
                state = self._post_json("/api/start-workflow")
                self.finished.emit(self.operation, True, "已开始回差检测流程", state)
            elif self.operation == "disconnect":
                state = self._post_json("/api/disconnect")
                self.finished.emit(self.operation, True, "已断开 Backlash 控制台连接", state)
            elif self.operation == "download_all":
                save_path = self._download_all_results()
                self.finished.emit(self.operation, True, f"已下载全部结果: {save_path}", self._get_state())
            else:
                self.finished.emit(self.operation, False, f"未知操作: {self.operation}", {})
        except Exception as error:
            self.finished.emit(self.operation, False, str(error), {})

    def _get_state(self) -> dict:
        response = httpx.get(f"{BACKLASH_BASE_URL}/api/state", timeout=5.0)
        response.raise_for_status()
        return response.json()

    def _post_json(self, path: str, payload: dict | None = None) -> dict:
        response = httpx.post(f"{BACKLASH_BASE_URL}{path}", json=payload, timeout=10.0)
        response.raise_for_status()
        return response.json()

    def _ensure_console(self) -> dict:
        try:
            return self._get_state()
        except Exception:
            pass

        if not os.path.exists(BACKLASH_EXE_PATH):
            raise FileNotFoundError(f"文件不存在: {BACKLASH_EXE_PATH}")

        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        env = os.environ.copy()
        env["BROWSER"] = "cmd /c exit 0" if sys.platform == "win32" else "true"
        subprocess.Popen(
            [BACKLASH_EXE_PATH, "--host", "127.0.0.1", "--port", "8080"],
            cwd=os.path.dirname(BACKLASH_EXE_PATH),
            creationflags=creationflags,
            env=env,
        )
        for _ in range(30):
            self.msleep(500)
            try:
                return self._get_state()
            except Exception:
                continue
        raise TimeoutError("Backlash 控制台启动超时，未监听 127.0.0.1:8080")

    def _download_all_results(self) -> str:
        self._ensure_console()
        response = httpx.get(f"{BACKLASH_BASE_URL}/api/results/all.zip", timeout=60.0)
        response.raise_for_status()
        save_dir = os.path.join(os.path.expanduser("~"), "AppData", "Local", "OliRobotManager", "backlash")
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, "backlash_results_all.zip")
        with open(save_path, "wb") as file_handle:
            file_handle.write(response.content)
        return save_path


class BacklashDirectWorker(QThread):
    finished = pyqtSignal(str, bool, str, object)
    authentication_required = pyqtSignal(str, str, str)

    def __init__(
        self,
        operation: str,
        payload: dict | None = None,
        parent=None,
        robot_id: str = "",
    ):
        super().__init__(parent)
        self.operation = operation
        self.payload = payload or {}
        self.robot_id = robot_id or ROBOT_CONFIG.ws_accid

    def run(self):
        password = self.payload.get("password")
        passwords = [password] if password else list(ROBOT_CONFIG.main_control_passwords)
        client = SshClient(
            self.payload.get("host") or ROBOT_CONFIG.main_control_ip,
            self.payload.get("username") or ROBOT_CONFIG.main_control_user,
            passwords,
            robot_id=self.robot_id,
        )
        try:
            client.connect(timeout=10)
            if self.operation == "prepare":
                detail = self._prepare_robot(client)
                self.finished.emit(self.operation, True, detail, self._state("ready"))
            elif self.operation == "start":
                detail = self._prepare_robot(client)
                detail += "\n" + self._run_detection(client)
                files = self._download_results(client)
                if files:
                    detail += "\n已下载结果:\n" + "\n".join(files)
                self._restore_zeroing_node(client)
                self.finished.emit(self.operation, True, detail, self._state("completed", files))
            elif self.operation == "download_all":
                files = self._download_results(client)
                self.finished.emit(self.operation, True, "已下载结果:\n" + "\n".join(files), self._state("completed", files))
            elif self.operation == "disconnect":
                self._restore_zeroing_node(client)
                self.finished.emit(self.operation, True, "已恢复 joint_calibration 节点并断开 SSH", self._state("disconnected"))
            elif self.operation == "state":
                self.finished.emit(self.operation, True, "内置直连模式已就绪；开始检测会自动准备环境并运行 backlash_detection", self._state("ready"))
            else:
                self.finished.emit(self.operation, False, f"未知操作: {self.operation}", {})
        except SshAuthenticationError:
            self.authentication_required.emit(
                self.payload.get("host") or ROBOT_CONFIG.main_control_ip,
                self.payload.get("username") or ROBOT_CONFIG.main_control_user,
                self.robot_id,
            )
            self.finished.emit(
                self.operation,
                False,
                "当前机器人尚未授权 SSH 密钥，请完成一次密码验证",
                self._state("authorization_required"),
            )
        except Exception as error:
            try:
                self._restore_zeroing_node(client)
            except Exception:
                pass
            self.finished.emit(self.operation, False, str(error), self._state("failed"))
        finally:
            client.close()

    def _prepare_robot(self, client: SshClient) -> str:
        payload_path = _backlash_payload_path()
        if not os.path.exists(payload_path):
            raise FileNotFoundError(f"未找到 backlash_install.zip: {payload_path}")
        client.execute("mkdir -p ~/backlash", timeout=20)
        sftp = client._client.open_sftp()
        try:
            sftp.put(payload_path, "/home/limx/backlash/backlash_install.zip")
        finally:
            sftp.close()
        install = client.execute(
            "cd ~/backlash && rm -rf deploy_tmp install && unzip -oq backlash_install.zip -d deploy_tmp && "
            "if [ -d deploy_tmp/install ]; then mv deploy_tmp/install install; else mv deploy_tmp/* install; fi && rm -rf deploy_tmp",
            timeout=180,
        )
        if install.exit_code != 0:
            raise RuntimeError(f"安装 backlash 检测程序失败: {install.stderr or install.stdout}")
        self._stop_zeroing_node(client)
        return "机器人端 backlash 检测程序已准备完成，joint_calibration 节点已暂停。"

    def _run_detection(self, client: SshClient) -> str:
        robot_id = str(self.payload.get("robot_id") or "").strip()
        if not robot_id:
            raise ValueError("机器人 ID 不能为空")
        command = (
            "cd ~/backlash && source install/setup.bash && "
            f"backlash_detection --robot-id {self._shell_quote(robot_id)}"
        )
        stdin, stdout, stderr = client._client.exec_command(command)
        channel = stdout.channel
        output_parts: list[str] = []
        started_at = time.monotonic()
        last_output_at = started_at
        stop_requested = False
        while True:
            chunk = ""
            if channel.recv_ready():
                chunk += channel.recv(8192).decode("utf-8", errors="replace")
            if channel.recv_stderr_ready():
                chunk += channel.recv_stderr(8192).decode("utf-8", errors="replace")
            if chunk:
                output_parts.append(chunk)
                last_output_at = time.monotonic()
                joined = "".join(output_parts)[-12000:]
                if not stop_requested and (
                    "Backlash detection OK" in joined
                    or "Backlash detection all finished" in joined
                    or "Backlash detection failed" in joined
                ):
                    time.sleep(2.0)
                    client.execute("pkill -INT -f '[b]acklash_detection --robot-id' || true", timeout=10)
                    stop_requested = True
            if channel.exit_status_ready():
                break
            now = time.monotonic()
            if now - started_at > 420:
                client.execute("pkill -TERM -f '[b]acklash_detection --robot-id' || true", timeout=10)
                raise TimeoutError("回差检测 420 秒内未完成，已尝试终止 backlash_detection")
            if now - last_output_at > 90:
                output_parts.append("\nWarning: 90 秒无新输出，请检查机器人状态或网络。\n")
                last_output_at = now
            self.msleep(100)
        exit_code = channel.recv_exit_status()
        output = "".join(output_parts).strip()
        if exit_code != 0 and not stop_requested:
            raise RuntimeError(f"回差检测失败(exit={exit_code}): {output[-4000:]}")
        return output[-8000:] or "回差检测命令已完成。"

    def _download_results(self, client: SshClient) -> list[str]:
        os.makedirs(BACKLASH_RESULT_DIR, exist_ok=True)
        remote_dir = "/home/limx/backlash/results"
        sftp = client._client.open_sftp()
        saved_files: list[str] = []
        try:
            for item in sftp.listdir_attr(remote_dir):
                if not item.filename.endswith(".yaml"):
                    continue
                remote_path = f"{remote_dir}/{item.filename}"
                local_path = os.path.join(BACKLASH_RESULT_DIR, item.filename)
                sftp.get(remote_path, local_path)
                saved_files.append(local_path)
        finally:
            sftp.close()
        return sorted(saved_files)

    def _stop_zeroing_node(self, client: SshClient):
        client.execute("source /opt/limx/install/setup.bash && mrosnode stop joint_calibration", timeout=30)
        time.sleep(1.0)

    def _restore_zeroing_node(self, client: SshClient):
        client.execute("source /opt/limx/install/setup.bash && mrosnode start joint_calibration", timeout=30)

    def _state(self, session_state: str, files: list[str] | None = None) -> dict:
        return {
            "session_state": session_state,
            "active_step_id": None,
            "results": [{"name": os.path.basename(path)} for path in (files or [])],
        }

    @staticmethod
    def _shell_quote(value: str) -> str:
        return "'" + value.replace("'", "'\\''") + "'"


class CalibrateService(QObject):
    calibrate_started = pyqtSignal(str)
    calibrate_result = pyqtSignal(str, bool, str)  # cal_type, success, detail
    ssh_authorization_required = pyqtSignal(str, str, str, str)
    backlash_launched = pyqtSignal(str)             # exe path
    backlash_state_ready = pyqtSignal(dict)

    def __init__(self, mcp_worker: McpWorker, parent=None):
        super().__init__(parent)
        self._mcp = mcp_worker
        self._pending_type = ""
        self._mission_worker: MissionEngineCalibrateWorker | None = None
        self._backlash_worker: BacklashConsoleWorker | None = None
        self._last_backlash_payload: dict = {}
        self._last_backlash_operation = ""
        self._mcp.tool_result_ready.connect(self._on_result)
        self._mcp.tool_error.connect(self._on_error)

    def run_mission_engine_calibrate(self):
        """Calibrate through mission_engine, matching the remote-controller L1+R1 path."""
        if self._mission_worker and self._mission_worker.isRunning():
            self.calibrate_result.emit("mission_engine", False, "校零正在执行中")
            return
        self.calibrate_started.emit("mission_engine")
        self._mission_worker = MissionEngineCalibrateWorker(
            ROBOT_CONFIG.ws_accid, self
        )
        self._mission_worker.authentication_required.connect(
            lambda host, username, robot_id: self.ssh_authorization_required.emit(
                host, username, "mission_engine", robot_id
            )
        )
        self._mission_worker.finished.connect(
            lambda success, detail: self.calibrate_result.emit("mission_engine", success, detail))
        self._mission_worker.start()

    def retry_after_ssh_authorization(self, operation: str):
        if operation == "mission_engine":
            self.run_mission_engine_calibrate()
        elif operation == "backlash" and self._last_backlash_operation:
            self._run_backlash_operation(
                self._last_backlash_operation,
                self._last_backlash_payload,
            )

    def cancel_ssh_authorization(self, operation: str, detail: str):
        self.calibrate_result.emit(operation, False, detail)

    def run_websocket_calibrate(self):
        """Calibrate via WebSocket request_calibrate."""
        self._pending_type = "websocket"
        self.calibrate_started.emit("websocket")
        self._mcp.call_tool("calibrate", {})

    def launch_backlash_test(self, exe_path: str = None):
        """Launch external backlash-console tool."""
        path = exe_path or r"C:\Users\Limx\Downloads\backlash-console-v0.5.exe"
        if not os.path.exists(path):
            self.calibrate_result.emit("backlash", False, f"文件不存在: {path}")
            return
        try:
            subprocess.Popen(path, cwd=os.path.dirname(path))
            self.backlash_launched.emit(path)
            self.calibrate_result.emit("backlash", True, "已启动 backlash 测试工具")
        except Exception as e:
            self.calibrate_result.emit("backlash", False, str(e))

    def launch_backlash_console(self):
        self._run_backlash_operation("launch")

    def connect_backlash_console(self, payload: dict):
        self._run_backlash_operation("connect", payload)

    def start_backlash_workflow(self, payload: dict | None = None):
        self._run_backlash_operation("start", payload or self._last_backlash_payload)

    def refresh_backlash_state(self):
        self._run_backlash_operation("state")

    def disconnect_backlash_console(self):
        self._run_backlash_operation("disconnect", self._last_backlash_payload)

    def download_backlash_results(self):
        self._run_backlash_operation("download_all", self._last_backlash_payload)

    def _run_backlash_operation(self, operation: str, payload: dict | None = None):
        if self._backlash_worker and self._backlash_worker.isRunning():
            self.calibrate_result.emit("backlash", False, "Backlash 操作正在执行中")
            return
        self._last_backlash_operation = operation
        if payload is not None:
            self._last_backlash_payload = {
                key: value for key, value in payload.items() if key != "password"
            }
        self.calibrate_started.emit("backlash")
        direct_operation = {
            "connect": "prepare",
            "start": "start",
            "download_all": "download_all",
            "disconnect": "disconnect",
        }.get(operation, operation)
        self._backlash_worker = BacklashDirectWorker(
            direct_operation,
            payload,
            self,
            robot_id=ROBOT_CONFIG.ws_accid,
        )
        self._backlash_worker.finished.connect(self._on_backlash_finished)
        self._backlash_worker.authentication_required.connect(
            lambda host, username, robot_id: self.ssh_authorization_required.emit(
                host, username, "backlash", robot_id
            )
        )
        self._backlash_worker.start()

    def _on_backlash_finished(self, operation: str, success: bool, detail: str, state):
        if operation == "launch" and success:
            self.backlash_launched.emit(BACKLASH_EXE_PATH)
        if isinstance(state, dict) and state:
            self.backlash_state_ready.emit(state)
        self.calibrate_result.emit("backlash", success, detail)

    def _on_result(self, tool_name: str, result):
        if tool_name == "calibrate" and self._pending_type == "websocket":
            success = result.get("success", False)
            detail = result.get("content", [""])[0] if result.get("content") else ""
            self.calibrate_result.emit("websocket", success, f"校零{'成功' if success else '失败'} {detail}")

    def _on_error(self, tool_name: str, error: str):
        if tool_name == "calibrate" and self._pending_type == "websocket":
            self.calibrate_result.emit("websocket", False, error)
