"""Background worker for one-time SSH public-key enrollment."""
import paramiko
from PyQt6.QtCore import QThread, pyqtSignal

from network.ssh_client import SshAuthenticationError
from network.ssh_key_manager import install_operator_key
from services import credential_store


class SshKeyInstallWorker(QThread):
    completed = pyqtSignal(bool, str, str)  # success, detail, error_code

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        robot_id: str,
        remember: bool = False,
        from_store: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.host = host
        self.username = username
        self.robot_id = robot_id
        self.remember = remember
        self.from_store = from_store
        self.credential_saved: bool | None = None
        self._password = password

    def run(self):
        try:
            install_operator_key(
                self.host,
                self.username,
                self._password,
                self.robot_id,
            )
            if self.remember and not self.from_store:
                self.credential_saved = credential_store.set_password(
                    self.robot_id,
                    self.host,
                    self.username,
                    self._password,
                )
            self.completed.emit(
                True,
                f"SSH 密钥已授权: {self.username}@{self.host}",
                "",
            )
        except (paramiko.AuthenticationException, SshAuthenticationError):
            self.completed.emit(
                False,
                "SSH 密码错误，密钥授权失败",
                "authentication",
            )
        except Exception as error:
            self.completed.emit(
                False,
                f"SSH 密钥授权失败: {error}",
                "connection",
            )
        finally:
            self._password = ""
