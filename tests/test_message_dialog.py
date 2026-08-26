from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QLabel, QMessageBox

from ui.dialogs.message_dialog import AppMessageBox


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "icon",
    [
        QMessageBox.Icon.Information,
        QMessageBox.Icon.Warning,
        QMessageBox.Icon.Critical,
    ],
)
def test_message_dialog_is_opaque_and_readable_with_application_theme(
    qapp, qtbot, icon
):
    original_style = qapp.styleSheet()
    qapp.setStyleSheet(
        (PROJECT_ROOT / "resources/styles/dark_theme.qss").read_text(
            encoding="utf-8"
        )
    )
    dialog = AppMessageBox(
        None,
        "SSH 密钥授权失败",
        "SSH 密码错误，密钥授权失败。",
        icon,
    )
    qtbot.addWidget(dialog)
    dialog.resize(560, 220)
    dialog.show()
    qapp.processEvents()

    image = QImage(dialog.size(), QImage.Format.Format_ARGB32)
    transparent_probe = QColor("#FF00FF")
    image.fill(transparent_probe)
    painter = QPainter(image)
    dialog.render(painter)
    painter.end()

    probe_pixels = sum(
        image.pixelColor(x, y) == transparent_probe
        for y in range(image.height())
        for x in range(image.width())
    )
    confirm_button = dialog.button(QMessageBox.StandardButton.Ok)

    assert not dialog.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert probe_pixels == 0
    assert dialog.palette().color(dialog.backgroundRole()).name().upper() == "#FFFFFF"
    assert confirm_button.text() == "确定"
    assert confirm_button.objectName() == "confirmButton"
    assert "background: #FFFFFF" in dialog.styleSheet()
    assert "color: #1D2129" in dialog.styleSheet()

    qapp.setStyleSheet(original_style)


def test_application_theme_keeps_raw_message_box_opaque(qapp, qtbot):
    original_style = qapp.styleSheet()
    qapp.setStyleSheet(
        (PROJECT_ROOT / "resources/styles/dark_theme.qss").read_text(
            encoding="utf-8"
        )
    )
    dialog = QMessageBox(QMessageBox.Icon.Warning, "兼容提示", "原生消息框兜底")
    qtbot.addWidget(dialog)
    dialog.resize(460, 180)
    dialog.show()
    qapp.processEvents()

    image = QImage(dialog.size(), QImage.Format.Format_ARGB32)
    transparent_probe = QColor("#FF00FF")
    image.fill(transparent_probe)
    painter = QPainter(image)
    dialog.render(painter)
    painter.end()

    probe_pixels = sum(
        image.pixelColor(x, y) == transparent_probe
        for y in range(image.height())
        for x in range(image.width())
    )

    assert probe_pixels == 0
    assert "QMessageBox" in qapp.styleSheet()

    qapp.setStyleSheet(original_style)


def test_long_message_wraps_inside_available_screen(qapp, qtbot):
    text = (
        "SSH 密码已验证且公钥已写入，但项目密钥复验失败: "
        "SSH limx@10.192.1.2 failed (tried 1 key and 0 passwords): "
        "timed out while waiting for SSH protocol banner"
    )
    dialog = AppMessageBox(
        None,
        "SSH 密钥授权失败",
        text,
        QMessageBox.Icon.Critical,
    )
    qtbot.addWidget(dialog)
    dialog.show()
    qapp.processEvents()

    label = dialog.findChild(QLabel, "qt_msgbox_label")
    available_width = dialog.screen().availableGeometry().width()

    assert label.wordWrap()
    assert label.width() <= 520
    assert label.geometry().right() < dialog.contentsRect().right()
    assert dialog.width() <= available_width