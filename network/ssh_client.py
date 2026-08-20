"""Paramiko-based SSH client with key authentication and password fallback."""
import os
import re
import threading
import paramiko
from dataclasses import dataclass


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

    def connect(self, timeout: int = 10) -> bool:
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
            self._client = self._create_client()
            try:
                self._client.connect(
                    self.host,
                    username=self.username,
                    key_filename=self._key_path,
                    timeout=timeout,
                    look_for_keys=False,
                    allow_agent=False,
                )
                self._verify_robot_identity()
                return True
            except SshRobotMismatchError:
                self._client.close()
                raise
            except Exception as e:
                last_error = e
                authentication_failed = self._is_authentication_error(e)
                self._client.close()

        for pw in self._passwords:
            self._client = self._create_client()
            try:
                self._client.connect(
                    self.host,
                    username=self.username,
                    password=pw or None,
                    timeout=timeout,
                    look_for_keys=False,
                    allow_agent=False,
                )
                self._verify_robot_identity()
                self._used_password = pw
                return True
            except SshRobotMismatchError:
                self._client.close()
                raise
            except paramiko.AuthenticationException as e:
                last_error = e
                authentication_failed = True
                self._client.close()
                # Password wrong, try next
                continue
            except Exception as e:
                last_error = e
                authentication_failed = authentication_failed or self._is_authentication_error(e)
                self._client.close()
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

    def close(self):
        if self._client:
            self._client.close()
            self._client = None
