"""Paramiko-based SSH client with key authentication and password fallback."""
import os
import re
import shlex
import threading
import time
import uuid
import paramiko
from dataclasses import dataclass
from typing import Callable


DEFAULT_SSH_KEY_PATH = os.path.join(
    os.path.expanduser("~"), ".ssh", "oli_robot_manager_ed25519"
)
DEFAULT_ROBOT_KNOWN_HOSTS_PATH = os.path.join(
    os.path.expanduser("~"), ".ssh", "oli_robot_manager_known_hosts"
)
_HOST_KEY_LOCK = threading.Lock()


class SshAuthenticationError(ConnectionError):
    pass


class SshRobotMismatchError(ConnectionError):
    pass


class SshExecutionCancelled(RuntimeError):
    pass


class SshOutputLimitError(RuntimeError):
    pass


def robot_host_key_alias(robot_id: str, host: str, username: str) -> str:
    safe_robot_id = re.sub(r"[^A-Za-z0-9_.-]", "-", robot_id)
    safe_host = re.sub(r"[^A-Za-z0-9_.-]", "-", host)
    safe_username = re.sub(r"[^A-Za-z0-9_.-]", "-", username)
    return f"oli-{safe_robot_id}-{safe_host}-{safe_username}"


def current_robot_id() -> str | None:
    from config import detect_accid_from_robot_portal, extract_robot_accid
    from network.wifi_manager import WifiManager

    ssid = WifiManager.get_robot_ssid()
    robot_id = extract_robot_accid(ssid) if ssid else None
    return robot_id or detect_accid_from_robot_portal(timeout=1.0)


class RobotHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, alias: str, robot_id: str, known_hosts_path: str):
        self.alias = alias
        self.robot_id = robot_id
        self.known_hosts_path = known_hosts_path

    def missing_host_key(self, client, hostname, key):
        del client, hostname
        connected_robot_id = current_robot_id()
        if connected_robot_id != self.robot_id:
            raise SshRobotMismatchError(
                f"SSH 握手期间机器人已切换为 {connected_robot_id or '未知'}，"
                f"原目标是 {self.robot_id}"
            )
        with _HOST_KEY_LOCK:
            host_keys = paramiko.HostKeys()
            if os.path.isfile(self.known_hosts_path):
                host_keys.load(self.known_hosts_path)

            known_keys = host_keys.lookup(self.alias)
            if known_keys:
                known_key = known_keys.get(key.get_name())
                if known_key == key:
                    return
                raise paramiko.SSHException(
                    f"机器人 {self.alias} 的 SSH 主机指纹已变化，已拒绝连接"
                )

            os.makedirs(os.path.dirname(self.known_hosts_path), mode=0o700, exist_ok=True)
            host_keys.add(self.alias, key.get_name(), key)
            temporary_path = self.known_hosts_path + ".tmp"
            host_keys.save(temporary_path)
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self.known_hosts_path)


@dataclass
class SshResult:
    exit_code: int
    stdout: str
    stderr: str


class SshClient:
    def __init__(
        self,
        host: str,
        username: str,
        passwords: list[str] | None = None,
        key_path: str | None = None,
        robot_id: str = "",
        known_hosts_path: str = DEFAULT_ROBOT_KNOWN_HOSTS_PATH,
    ):
        self.host = host
        self.username = username
        self._passwords = passwords or [""]
        self._key_path = os.path.expanduser(key_path or DEFAULT_SSH_KEY_PATH)
        self.robot_id = robot_id
        self._known_hosts_path = os.path.expanduser(known_hosts_path)
        self._used_password = ""
        self._client: paramiko.SSHClient | None = None

    def _create_client(self) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        if self.robot_id:
            client.set_missing_host_key_policy(RobotHostKeyPolicy(
                robot_host_key_alias(self.robot_id, self.host, self.username),
                self.robot_id,
                self._known_hosts_path,
            ))
        else:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        return client

    def connect(
        self,
        timeout: int = 10,
        cancel_event: threading.Event | None = None,
    ) -> bool:
        self._raise_if_cancelled(cancel_event)
        if self.robot_id:
            connected_robot_id = current_robot_id()
            if connected_robot_id != self.robot_id:
                raise SshRobotMismatchError(
                    f"当前机器人是 {connected_robot_id or '未知'}，"
                    f"操作目标是 {self.robot_id}，已拒绝 SSH 连接"
                )

        last_error = None
        authentication_failed = False
        if os.path.isfile(self._key_path):
            self._raise_if_cancelled(cancel_event)
            client = self._create_client()
            self._client = client
            try:
                client.connect(
                    self.host,
                    username=self.username,
                    key_filename=self._key_path,
                    timeout=timeout,
                    banner_timeout=timeout,
                    auth_timeout=timeout,
                    look_for_keys=False,
                    allow_agent=False,
                )
                self._raise_if_cancelled(cancel_event)
                self._verify_robot_identity()
                self._raise_if_cancelled(cancel_event)
                return True
            except (SshRobotMismatchError, SshExecutionCancelled):
                client.close()
                if self._client is client:
                    self._client = None
                raise
            except Exception as e:
                last_error = e
                authentication_failed = self._is_authentication_error(e)
                client.close()
                if self._client is client:
                    self._client = None
                self._raise_if_cancelled(cancel_event)

        for pw in self._passwords:
            self._raise_if_cancelled(cancel_event)
            client = self._create_client()
            self._client = client
            try:
                client.connect(
                    self.host,
                    username=self.username,
                    password=pw or None,
                    timeout=timeout,
                    banner_timeout=timeout,
                    auth_timeout=timeout,
                    look_for_keys=False,
                    allow_agent=False,
                )
                self._raise_if_cancelled(cancel_event)
                self._verify_robot_identity()
                self._raise_if_cancelled(cancel_event)
                self._used_password = pw
                return True
            except (SshRobotMismatchError, SshExecutionCancelled):
                client.close()
                if self._client is client:
                    self._client = None
                raise
            except paramiko.AuthenticationException as e:
                last_error = e
                authentication_failed = True
                client.close()
                if self._client is client:
                    self._client = None
                self._raise_if_cancelled(cancel_event)
                # Password wrong, try next
                continue
            except Exception as e:
                last_error = e
                authentication_failed = authentication_failed or self._is_authentication_error(e)
                client.close()
                if self._client is client:
                    self._client = None
                self._raise_if_cancelled(cancel_event)
                # Connection error, try next password anyway
                continue

        self._client = None
        key_attempt = "1 key" if os.path.isfile(self._key_path) else "no key"
        error_type = SshAuthenticationError if authentication_failed else ConnectionError
        raise error_type(
            f"SSH {self.username}@{self.host} failed "
            f"(tried {key_attempt} and {len(self._passwords)} passwords): {last_error}"
        )

    @staticmethod
    def _raise_if_cancelled(cancel_event: threading.Event | None):
        if cancel_event and cancel_event.is_set():
            raise SshExecutionCancelled("SSH 连接已取消")

    @staticmethod
    def _is_authentication_error(error: Exception) -> bool:
        if isinstance(error, paramiko.AuthenticationException):
            return True
        message = str(error).lower()
        return "authentication" in message or "permission denied" in message

    def _verify_robot_identity(self):
        if not self.robot_id:
            return
        connected_robot_id = current_robot_id()
        if connected_robot_id != self.robot_id:
            raise SshRobotMismatchError(
                f"SSH 登录后机器人是 {connected_robot_id or '未知'}，"
                f"原目标是 {self.robot_id}"
            )

    def execute(self, command: str, timeout: int = 30) -> SshResult:
        if not self._client:
            raise RuntimeError("Not connected. Call connect() first.")
        stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)
        return SshResult(
            exit_code=stdout.channel.recv_exit_status(),
            stdout=stdout.read().decode("utf-8", errors="replace"),
            stderr=stderr.read().decode("utf-8", errors="replace"),
        )

    def execute_streaming(self, command: str, on_line: callable, stdin_text: str = "") -> int:
        if not self._client:
            raise RuntimeError("Not connected. Call connect() first.")
        stdin, stdout, stderr = self._client.exec_command(command)
        if stdin_text:
            stdin.write(stdin_text)
            stdin.flush()
            stdin.channel.shutdown_write()
        for line in iter(stdout.readline, ""):
            on_line(line.rstrip("\n"))
        for line in iter(stderr.readline, ""):
            on_line("[stderr] " + line.rstrip("\n"))
        return stdout.channel.recv_exit_status()

    def execute_managed(
        self,
        command: str,
        on_line: Callable[[str, str], None],
        cancel_event: threading.Event,
        timeout: float,
        max_output_bytes: int = 1024 * 1024,
    ) -> SshResult:
        if not self._client:
            raise RuntimeError("Not connected. Call connect() first.")

        marker = "__OLI_TEST_PID__="
        pid_file = f"/tmp/.oli-robot-manager-{uuid.uuid4().hex}.pid"
        launch_script = (
            "pid_file=$1; "
            'tmp_file="${pid_file}.tmp.$$"; '
            "umask 077; "
            'printf "%s\\n" "$$" > "$tmp_file"; '
            'mv -f -- "$tmp_file" "$pid_file"; '
            'trap \'rm -f -- "$pid_file" "$tmp_file"\' EXIT; '
            f'printf "{marker}%s\\n" "$$"; '
            f"{command}"
        )
        wrapped = " ".join([
            "setsid sh -c",
            shlex.quote(launch_script),
            "oli-managed-test",
            shlex.quote(pid_file),
        ])
        _, stdout, stderr = self._client.exec_command(wrapped)
        channel = stdout.channel
        started_at = time.monotonic()
        remote_pid = None
        total_bytes = 0
        collected = {"stdout": [], "stderr": []}
        pending = {"stdout": "", "stderr": ""}

        def consume(chunk: bytes, stream: str):
            nonlocal remote_pid, total_bytes
            decoded = chunk.decode("utf-8", errors="replace")
            if stream == "stdout" and remote_pid is None:
                pid_match = re.search(
                    rf"(?:^|\n){re.escape(marker)}(\d+)(?:\r?\n|$)",
                    pending[stream] + decoded,
                )
                if pid_match:
                    remote_pid = int(pid_match.group(1))
            total_bytes += len(chunk)
            if total_bytes > max_output_bytes:
                terminated = self._terminate_process_group(remote_pid, pid_file)
                channel.close()
                outcome = "已终止远端进程" if terminated else "无法确认远端进程已终止"
                raise SshOutputLimitError(
                    f"测试输出超过 {max_output_bytes} 字节，{outcome}"
                )
            text = pending[stream] + decoded
            lines = text.splitlines(keepends=True)
            pending[stream] = ""
            if lines and not lines[-1].endswith(("\n", "\r")):
                pending[stream] = lines.pop()
            for raw_line in lines:
                line = raw_line.rstrip("\r\n")
                if stream == "stdout" and line.startswith(marker):
                    pid_text = line[len(marker):].strip()
                    remote_pid = int(pid_text) if pid_text.isdigit() else None
                    continue
                collected[stream].append(line)
                on_line(line, stream)

        try:
            while True:
                if channel.recv_ready():
                    consume(channel.recv(8192), "stdout")
                if channel.recv_stderr_ready():
                    consume(channel.recv_stderr(8192), "stderr")
                if channel.exit_status_ready():
                    if not channel.recv_ready() and not channel.recv_stderr_ready():
                        break
                    continue
                if cancel_event.is_set():
                    terminated = self._terminate_process_group(remote_pid, pid_file)
                    channel.close()
                    outcome = "远端进程已终止" if terminated else "无法确认远端进程已终止"
                    raise SshExecutionCancelled(f"测试已取消，{outcome}")
                if time.monotonic() - started_at > timeout:
                    terminated = self._terminate_process_group(remote_pid, pid_file)
                    channel.close()
                    outcome = "远端进程已终止" if terminated else "无法确认远端进程已终止"
                    raise TimeoutError(f"测试超过 {timeout:.0f} 秒，{outcome}")
                time.sleep(0.05)

            for stream in ("stdout", "stderr"):
                if pending[stream]:
                    line = pending[stream]
                    if stream == "stdout" and line.startswith(marker):
                        pid_text = line[len(marker):].strip()
                        remote_pid = int(pid_text) if pid_text.isdigit() else remote_pid
                    else:
                        collected[stream].append(line)
                        on_line(line, stream)
            exit_code = channel.recv_exit_status()
            return SshResult(
                exit_code=exit_code,
                stdout="\n".join(collected["stdout"]),
                stderr="\n".join(collected["stderr"]),
            )
        finally:
            channel.close()

    def _terminate_process_group(
        self,
        remote_pid: int | None,
        pid_file: str,
    ) -> bool:
        if not self._client:
            return False
        initial_pid = str(remote_pid) if remote_pid is not None else ""
        command = (
            f"pid={shlex.quote(initial_pid)}; "
            f"pid_file={shlex.quote(pid_file)}; "
            'attempt=0; while [ -z "$pid" ] && [ "$attempt" -lt 10 ]; do '
            '[ -s "$pid_file" ] && pid=$(cat -- "$pid_file" 2>/dev/null); '
            'attempt=$((attempt + 1)); [ -n "$pid" ] || sleep 0.05; done; '
            'case "$pid" in ""|*[!0-9]*) rm -f -- "$pid_file"; exit 3;; esac; '
            'kill -TERM "-$pid" 2>/dev/null || true; '
            'attempt=0; while kill -0 "-$pid" 2>/dev/null '
            '&& [ "$attempt" -lt 10 ]; do '
            'sleep 0.1; attempt=$((attempt + 1)); done; '
            'if kill -0 "-$pid" 2>/dev/null; then '
            'kill -KILL "-$pid" 2>/dev/null || true; fi; '
            'attempt=0; while kill -0 "-$pid" 2>/dev/null '
            '&& [ "$attempt" -lt 10 ]; do '
            'sleep 0.1; attempt=$((attempt + 1)); done; '
            'rm -f -- "$pid_file"; '
            '! kill -0 "-$pid" 2>/dev/null'
        )
        try:
            _, stdout, _ = self._client.exec_command(command, timeout=5)
            return stdout.channel.recv_exit_status() == 0
        except Exception:
            return False

    def close(self):
        client = self._client
        self._client = None
        if client:
            client.close()
