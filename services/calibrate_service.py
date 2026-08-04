"""
Calibration service — two types:
  1. MissionEngine calibrate (same state-machine path as remote L1+R1)
  2. Backlash test (launch external backlash-console-v0.5.exe)
"""
import os
import subprocess
import sys
import time
from PyQt6.QtCore import QObject, QThread, pyqtSignal
import httpx
from config import ROBOT_CONFIG
from network.mcp_client import RobotClient
from network.ssh_client import SshClient
from workers.mcp_worker import McpWorker


BACKLASH_EXE_PATH = r"C:\Users\Limx\Downloads\backlash-console-v0.5.exe"
BACKLASH_BASE_URL = "http://127.0.0.1:8080"
BACKLASH_LEGACY_PAYLOAD_PATH = os.path.join(
    os.path.expanduser("~"), "AppData", "Local", "OliRobotManager", "backlash", "backlash_install.zip"
)
BACKLASH_RESULT_DIR = os.path.join(
    os.path.expanduser("~"), "AppData", "Local", "OliRobotManager", "backlash", "results"
)


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

    def run(self):
        led_detail = ""
        try:
            led_result = RobotClient(ROBOT_CONFIG.websocket_url, ROBOT_CONFIG.ws_accid).enable_led_control(False)
            led_detail = f"SDK LED 控制已关闭: {led_result}"
        except Exception as error:
            led_detail = f"SDK LED 控制关闭失败，继续尝试校零: {error}"

        client = SshClient(
            ROBOT_CONFIG.main_control_ip,
            ROBOT_CONFIG.main_control_user,
            list(ROBOT_CONFIG.main_control_passwords),
        )
        try:
            client.connect(timeout=8)
            command = (
                "export LD_LIBRARY_PATH=/opt/limx/install/lib; "
                "export PATH=/opt/limx/install/bin:$PATH; "
                "export MROS_IP_LIST=10.192.1.x; "
                "export MROS_ETC_PATH=/opt/limx/install/etc; "
                "export MROS_BIN_PATH=/opt/limx/install/bin; "
                "export MROS_LIB_PATH=/opt/limx/install/lib; "
                "export MROS_PKG_PATH=/opt/limx/install; "
                "export MROS_SIM_TIME=0; "
                "/opt/limx/install/bin/mrosservice call /mission_engine/switch_state "
                "std_srvs/SetString '{\"data\":\"Calibration\"}'"
            )
            result = client.execute(command, timeout=30)
            output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
            if result.exit_code != 0:
                self.finished.emit(False, f"{led_detail}\nMissionEngine 调用失败(exit={result.exit_code}): {output}")
                return
            if "success" not in output.lower():
                self.finished.emit(False, f"{led_detail}\nMissionEngine 未返回 success: {output}")
                return
            self.finished.emit(True, f"{led_detail}\n已进入 MissionEngine Calibration，机器人应显示校零中并使用蓝色灯语。\n{output}")
        except Exception as error:
            self.finished.emit(False, f"{led_detail}\n{error}")
        finally:
            client.close()


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

    def __init__(self, operation: str, payload: dict | None = None, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.payload = payload or {}

    def run(self):
        client = SshClient(
            self.payload.get("host") or ROBOT_CONFIG.main_control_ip,
            self.payload.get("username") or ROBOT_CONFIG.main_control_user,
            [self.payload.get("password") or ROBOT_CONFIG.main_control_passwords[0]],
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
    backlash_launched = pyqtSignal(str)             # exe path
    backlash_state_ready = pyqtSignal(dict)

    def __init__(self, mcp_worker: McpWorker, parent=None):
        super().__init__(parent)
        self._mcp = mcp_worker
        self._pending_type = ""
        self._mission_worker: MissionEngineCalibrateWorker | None = None
        self._backlash_worker: BacklashConsoleWorker | None = None
        self._last_backlash_payload: dict = {}
        self._mcp.tool_result_ready.connect(self._on_result)
        self._mcp.tool_error.connect(self._on_error)

    def run_mission_engine_calibrate(self):
        """Calibrate through mission_engine, matching the remote-controller L1+R1 path."""
        if self._mission_worker and self._mission_worker.isRunning():
            self.calibrate_result.emit("mission_engine", False, "校零正在执行中")
            return
        self.calibrate_started.emit("mission_engine")
        self._mission_worker = MissionEngineCalibrateWorker(self)
        self._mission_worker.finished.connect(
            lambda success, detail: self.calibrate_result.emit("mission_engine", success, detail))
        self._mission_worker.start()

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
        self._last_backlash_payload = dict(payload)
        self._run_backlash_operation("connect", payload)

    def start_backlash_workflow(self, payload: dict | None = None):
        if payload:
            self._last_backlash_payload = dict(payload)
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
        self.calibrate_started.emit("backlash")
        direct_operation = {
            "connect": "prepare",
            "start": "start",
            "download_all": "download_all",
            "disconnect": "disconnect",
        }.get(operation, operation)
        self._backlash_worker = BacklashDirectWorker(direct_operation, payload, self)
        self._backlash_worker.finished.connect(self._on_backlash_finished)
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
