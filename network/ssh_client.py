"""
Paramiko-based SSH client. Auto-clears old host keys and tries multiple passwords.
"""
import subprocess
import sys
import paramiko
from dataclasses import dataclass


@dataclass
class SshResult:
    exit_code: int
    stdout: str
    stderr: str


class SshClient:
    def __init__(self, host: str, username: str, passwords: list[str] | None = None):
        self.host = host
        self.username = username
        self._passwords = passwords or [""]
        self._used_password = ""
        self._client: paramiko.SSHClient | None = None

    @staticmethod
    def clear_host_key(host: str):
        """Remove stale SSH host key for the given host."""
        kwargs = {}
        if sys.platform == "win32" and getattr(sys, 'frozen', False):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            subprocess.run(
                ["ssh-keygen", "-R", host],
                capture_output=True, timeout=5,
                **kwargs,
            )
        except Exception:
            pass

    def connect(self, timeout: int = 10) -> bool:
        # Clear old host key first (robot might have changed)
        SshClient.clear_host_key(self.host)

        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        last_error = None
        for pw in self._passwords:
            try:
                self._client.connect(
                    self.host,
                    username=self.username,
                    password=pw or None,
                    timeout=timeout,
                    look_for_keys=False,
                    allow_agent=False,
                )
                self._used_password = pw
                return True
            except paramiko.AuthenticationException as e:
                last_error = e
                # Password wrong, try next
                continue
            except Exception as e:
                last_error = e
                # Connection error, try next password anyway
                continue

        self._client = None
        raise ConnectionError(
            f"SSH {self.username}@{self.host} failed (tried {len(self._passwords)} passwords): {last_error}"
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

    def execute_streaming(self, command: str, on_line: callable, stdin_text: str = ""):
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
            on_line(f"[stderr] {line.rstrip('\n')}")

    def close(self):
        if self._client:
            self._client.close()
            self._client = None
