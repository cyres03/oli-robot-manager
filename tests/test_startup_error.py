import os
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_startup_error_without_qapplication_falls_back_to_stderr(tmp_path):
    environment = dict(os.environ)
    environment["HOME"] = str(tmp_path)
    environment["USERPROFILE"] = str(tmp_path)
    environment["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from main import _log_startup_error; "
                "_log_startup_error(RuntimeError('before QApplication'))"
            ),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    crash_log = (
        tmp_path / "AppData" / "Local" / "OliRobotManager" / "crash.log"
    )
    assert result.returncode == 0
    assert "启动失败: before QApplication" in result.stderr
    assert crash_log.is_file()
    assert "RuntimeError: before QApplication" in crash_log.read_text(
        encoding="utf-8"
    )