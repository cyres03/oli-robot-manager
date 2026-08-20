from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QPainter

from ui.dialogs.password_dialog import PasswordDialog


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_password_dialog_renders_opaque_with_application_theme(qapp, qtbot):
    original_style = qapp.styleSheet()
    qapp.setStyleSheet(
        (PROJECT_ROOT / "resources/styles/dark_theme.qss").read_text(encoding="utf-8")
    )

    dialog = PasswordDialog(
        "首次连接机器人",
        "机器人 HU_D04_01_172 尚未授权本机 SSH 密钥。\n"
        "请输入 limx@10.192.1.2 的密码：",
    )
    qtbot.addWidget(dialog)
    dialog.resize(620, 300)
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

    assert not dialog.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert probe_pixels == 0

    qapp.setStyleSheet(original_style)
