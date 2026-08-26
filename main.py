"""Oli Robot Manager — Entry Point."""
import sys
import os
import traceback
import datetime


def _resource_path(relative_path):
    """Get absolute path to resource, works for dev and PyInstaller."""
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative_path)


def _log_startup_error(exc: Exception) -> None:
    log_dir = os.path.join(os.path.expanduser("~"), "AppData", "Local", "OliRobotManager")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "crash.log")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"\n=== CRASH {datetime.datetime.now().isoformat()} ===\n")
        f.write(f"sys.frozen={getattr(sys, 'frozen', False)}\n")
        f.write(f"sys._MEIPASS={getattr(sys, '_MEIPASS', 'N/A')}\n")
        f.write(f"sys.argv={sys.argv}\n")
        f.write(f"sys.path[:3]={sys.path[:3]}\n")
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)
    message = f"{exc}\n\n详情已写入:\n{log_path}"
    try:
        from PyQt6.QtWidgets import QApplication

        if QApplication.instance() is not None:
            from ui.dialogs.message_dialog import AppMessageBox

            AppMessageBox.critical(None, "启动失败", message)
            return
    except Exception:
        pass
    print(f"启动失败: {message}", file=sys.stderr)


def main():
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QIcon
        from app import Application

        app = QApplication(sys.argv)
        app.setApplicationName("Oli Robot Manager")
        app.setOrganizationName("Limx")

        logo_path = _resource_path(os.path.join("resources", "logo", "oli_manager_logo.svg"))
        if os.path.exists(logo_path):
            app.setWindowIcon(QIcon(logo_path))

        style_path = _resource_path(os.path.join("resources", "styles", "dark_theme.qss"))
        if os.path.exists(style_path):
            with open(style_path, "r", encoding="utf-8") as f:
                app.setStyleSheet(f.read())

        application = Application()
        application.window.show()
        app.aboutToQuit.connect(application.shutdown)
        sys.exit(app.exec())
    except Exception as e:
        _log_startup_error(e)
        raise


if __name__ == "__main__":
    main()
