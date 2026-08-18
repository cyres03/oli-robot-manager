"""Password prompt with optional secure OS credential storage."""
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from services import credential_store


class PasswordDialog(QDialog):
    def __init__(self, title: str, prompt: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        label = QLabel(prompt)
        label.setWordWrap(True)
        layout.addWidget(label)

        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("请输入密码")
        layout.addWidget(self.password_input)

        self.remember_checkbox = QCheckBox("记住到系统凭据管理器")
        available = credential_store.is_available()
        self.remember_checkbox.setChecked(available)
        self.remember_checkbox.setEnabled(available)
        if not available:
            self.remember_checkbox.setToolTip(
                "系统凭据管理器不可用，密码仅用于本次操作"
            )
        layout.addWidget(self.remember_checkbox)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        if not self.password_input.text():
            self.password_input.setFocus()
            return
        super().accept()

    @classmethod
    def get_password(cls, parent, title: str, prompt: str) -> tuple[str, bool, bool]:
        dialog = cls(title, prompt, parent)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        password = dialog.password_input.text() if accepted else ""
        remember = accepted and dialog.remember_checkbox.isChecked()
        dialog.password_input.clear()
        return password, remember, accepted