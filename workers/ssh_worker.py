"""QThread that runs SSH commands and streams output."""
from PyQt6.QtCore import QThread, pyqtSignal
from network.ssh_client import SshAuthenticationError, SshClient
from services import credential_store


class SshWorker(QThread):
    output_line = pyqtSignal(str, str)      # (text, stream: "stdout"|"stderr")
    command_finished = pyqtSignal(int)       # exit_code
    error_occurred = pyqtSignal(str)         # error message
    authentication_required = pyqtSignal(str, str, str)  # host, username, robot_id

    def __init__(
        self,
        host: str,
        username: str,
        passwords: list[str] | None = None,
        parent=None,
        robot_id: str = "",
    ):
        super().__init__(parent)
        from config import ROBOT_CONFIG

        self.host = host
        self.username = username
        self.robot_id = robot_id or ROBOT_CONFIG.ws_accid
        self._passwords = passwords or [""]
        self._command = ""
        self._stdin_text = ""
        self._transient_credential = ""
        self._remember_credential = False
        self._credential_from_store = False
        self.credential_saved: bool | None = None
        self.stored_credential_invalid = False
        self.collected_output = ""

    def set_command(self, command: str):
        self._command = command

    def set_stdin_text(self, stdin_text: str):
        self._stdin_text = stdin_text

    def set_transient_credential(
        self, password: str, remember: bool, from_store: bool
    ):
        self._transient_credential = password
        self._remember_credential = remember
        self._credential_from_store = from_store

    def run(self):
        client = SshClient(
            self.host,
            self.username,
            self._passwords,
            robot_id=self.robot_id,
        )
        stdin_text = self._stdin_text
        self._stdin_text = ""
        try:
            client.connect()
            lines = []
            exit_code = client.execute_streaming(
                self._command,
                on_line=lambda line: lines.append(line),
                stdin_text=stdin_text,
            )
            self.collected_output = "\n".join(lines)
            if self._transient_credential:
                if exit_code == 0 and self._remember_credential and not self._credential_from_store:
                    self.credential_saved = credential_store.set_password(
                        self.robot_id,
                        self.host,
                        self.username,
                        self._transient_credential,
                    )
                elif (
                    exit_code != 0
                    and self._credential_from_store
                    and self._is_sudo_auth_failure(self.collected_output)
                ):
                    self.stored_credential_invalid = credential_store.delete_password(
                        self.robot_id,
                        self.host,
                        self.username,
                    )
            for line in lines:
                self.output_line.emit(line, "stdout")
            self.command_finished.emit(exit_code)
            # Brief sleep so queued signals are delivered before thread exits
            self.msleep(100)
        except SshAuthenticationError:
            self.authentication_required.emit(self.host, self.username, self.robot_id)
            self.msleep(100)
        except Exception as e:
            self.error_occurred.emit(str(e))
            self.msleep(100)
        finally:
            self._transient_credential = ""
            self._remember_credential = False
            self._credential_from_store = False
            client.close()

    @staticmethod
    def _is_sudo_auth_failure(output: str) -> bool:
        lowered = output.lower()
        if "__sudo_auth_failed__" in lowered:
            return True
        return any(
            marker in lowered
            for marker in (
                "incorrect password",
                "sorry, try again",
                "no password was provided",
                "authentication failure",
                "密码不正确",
                "密码错误",
                "抱歉，请重试",
            )
        )
