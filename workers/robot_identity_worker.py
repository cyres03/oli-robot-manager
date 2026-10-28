"""Background robot identity discovery for the Qt UI."""
from PyQt6.QtCore import QThread, pyqtSignal

from config import detect_robot_identity


class RobotIdentityWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, timeout: float = 0.35, parent=None):
        super().__init__(parent)
        self.setObjectName("RobotIdentityWorker")
        self._timeout = timeout

    def run(self):
        try:
            identity = detect_robot_identity(timeout=self._timeout)
            if not self.isInterruptionRequested():
                self.completed.emit(identity)
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.failed.emit(str(exc))