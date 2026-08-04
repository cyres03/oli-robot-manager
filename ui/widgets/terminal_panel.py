"""Scrollable terminal-like output panel with color-coded log lines."""
from datetime import datetime
from PyQt6.QtWidgets import QPlainTextEdit, QWidget, QVBoxLayout
from PyQt6.QtCore import QMutex, QMutexLocker
from PyQt6.QtGui import QTextCursor


class TerminalPanel(QWidget):
    def __init__(self, max_lines: int = 5000, parent=None):
        super().__init__(parent)
        self._max_lines = max_lines
        self._mutex = QMutex()
        self._last_entry: tuple[str, str] | None = None
        self._last_repeat_count = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._editor = QPlainTextEdit()
        self._editor.setObjectName("terminalOutput")
        self._editor.setReadOnly(True)
        self._editor.setMaximumBlockCount(max_lines)
        layout.addWidget(self._editor)

    def append_log(self, text: str, level: str = "info"):
        with QMutexLocker(self._mutex):
            entry = (text, level)
            if entry == self._last_entry:
                self._last_repeat_count += 1
                if self._last_repeat_count % 10 != 0:
                    return
                text = f"{text} (重复 {self._last_repeat_count} 次)"
            else:
                self._last_entry = entry
                self._last_repeat_count = 1

            timestamp = datetime.now().strftime("%H:%M:%S")
            color_map = {
                "info": "#1D2129",
                "error": "#F53F3F",
                "pass": "#00B42A",
                "command": "#6C5CE7",
                "output": "#4E5969",
                "warn": "#FF7D00",
            }
            color = color_map.get(level, "#1D2129")
            safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            html = (
                f'<span style="color:#86909C">[{timestamp}]</span> '
                f'<span style="color:{color}">{safe}</span>'
            )
            self._editor.appendHtml(html)
            self._editor.moveCursor(QTextCursor.MoveOperation.End)

    def clear(self):
        self._editor.clear()
