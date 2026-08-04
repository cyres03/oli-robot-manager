"""QThread that runs SSH commands and streams output."""
from PyQt6.QtCore import QThread, pyqtSignal
from network.ssh_client import SshClient


class SshWorker(QThread):
    output_line = pyqtSignal(str, str)      # (text, stream: "stdout"|"stderr")
    command_finished = pyqtSignal(int)       # exit_code
    error_occurred = pyqtSignal(str)         # error message

    def __init__(self, host: str, username: str, passwords: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.host = host
        self.username = username
        self._passwords = passwords or [""]
        self._command = ""
        self._stdin_text = ""
        self.collected_output = ""

    def set_command(self, command: str):
        self._command = command

    def set_stdin_text(self, stdin_text: str):
        self._stdin_text = stdin_text

    def run(self):
        client = SshClient(self.host, self.username, self._passwords)
        stdin_text = self._stdin_text
        self._stdin_text = ""
        try:
            client.connect()
            lines = []
            client.execute_streaming(
                self._command,
                on_line=lambda line: lines.append(line),
                stdin_text=stdin_text,
            )
            self.collected_output = "\n".join(lines)
            for line in lines:
                self.output_line.emit(line, "stdout")
            self.command_finished.emit(0)
            # Brief sleep so queued signals are delivered before thread exits
            self.msleep(100)
        except Exception as e:
            self.error_occurred.emit(str(e))
            self.msleep(100)
        finally:
            client.close()
