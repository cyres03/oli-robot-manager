"""Opaque, consistently styled application message dialogs."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QMessageBox


class AppMessageBox(QMessageBox):
    def __init__(
        self,
        parent,
        title: str,
        text: str,
        icon: QMessageBox.Icon = QMessageBox.Icon.Information,
    ):
        super().__init__(parent)
        self.setObjectName("appMessageBox")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#FFFFFF"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#1D2129"))
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        self.setWindowTitle(title)
        self.setIcon(icon)
        self.setText(text)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setStandardButtons(QMessageBox.StandardButton.Ok)
        self.setDefaultButton(QMessageBox.StandardButton.Ok)
        self.setMinimumWidth(460)

        confirm_button = self.button(QMessageBox.StandardButton.Ok)
        if confirm_button:
            confirm_button.setText("确定")
            confirm_button.setObjectName("confirmButton")

        self.setStyleSheet(
            "QMessageBox#appMessageBox { background: #FFFFFF; color: #1D2129; }"
            "QMessageBox#appMessageBox QLabel { background: transparent; color: #1D2129; "
            "font-size: 14px; }"
            "QMessageBox#appMessageBox QLabel#qt_msgbox_label, "
            "QMessageBox#appMessageBox QLabel#qt_msgbox_informativelabel { min-width: 320px; }"
            "QMessageBox#appMessageBox QPushButton { min-width: 96px; min-height: 36px; "
            "border-radius: 6px; padding: 6px 18px; font-size: 14px; font-weight: 600; }"
            "QMessageBox#appMessageBox QPushButton#confirmButton { background: #6C5CE7; "
            "color: #FFFFFF; border: 1px solid #6C5CE7; }"
            "QMessageBox#appMessageBox QPushButton#confirmButton:hover { background: #5A4BD1; "
            "border-color: #5A4BD1; }"
            "QMessageBox#appMessageBox QPushButton#confirmButton:pressed { background: #4E3FB8; "
            "border-color: #4E3FB8; }"
        )

    @classmethod
    def information(cls, parent, title: str, text: str) -> int:
        return cls(parent, title, text, QMessageBox.Icon.Information).exec()

    @classmethod
    def warning(cls, parent, title: str, text: str) -> int:
        return cls(parent, title, text, QMessageBox.Icon.Warning).exec()

    @classmethod
    def critical(cls, parent, title: str, text: str) -> int:
        return cls(parent, title, text, QMessageBox.Icon.Critical).exec()