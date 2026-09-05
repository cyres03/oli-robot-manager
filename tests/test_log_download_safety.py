from pathlib import Path

import pytest

from ui.panels.acceptance_test_panel import (
    LogDownloadWorker,
    LogListWorker,
    normalize_log_name,
)


@pytest.mark.parametrize("name", [
    "../outside.log",
    "..\\outside.log",
    "https://evil.test/outside.log",
    "C:\\outside.log",
    "bad?.log",
    "not-a-log.txt",
])
def test_log_name_rejects_paths_urls_and_invalid_extensions(name):
    assert normalize_log_name(name) is None
    with pytest.raises(ValueError, match="日志文件名无效"):
        LogDownloadWorker("http://10.192.1.2:8090", name)


def test_log_name_accepts_plain_log_files():
    assert normalize_log_name("robot-20260905.log") == "robot-20260905.log"
    assert normalize_log_name("robot.log.active") == "robot.log.active"


def test_log_list_filters_unsafe_server_entries(monkeypatch):
    import ui.panels.acceptance_test_panel as module

    class Response:
        text = (
            '<a href="robot.log">ok</a>'
            '<a href="../outside.log">bad</a>'
            '<a href="https://evil.test/remote.log">bad</a>'
        )

        def raise_for_status(self):
            pass

    monkeypatch.setattr(module.httpx, "get", lambda *args, **kwargs: Response())
    worker = LogListWorker("http://10.192.1.2:8090")
    results = []
    worker.finished.connect(results.append)

    worker.run()

    assert results == [["robot.log"]]


def test_log_download_stays_in_private_app_directory(tmp_path, monkeypatch):
    import config
    import ui.panels.acceptance_test_panel as module

    class Response:
        text = "diagnostic log"

        def raise_for_status(self):
            pass

    requested_urls = []
    monkeypatch.setattr(config.APP_CONFIG, "data_dir", str(tmp_path))
    monkeypatch.setattr(
        module.httpx,
        "get",
        lambda url, **kwargs: requested_urls.append((url, kwargs)) or Response(),
    )
    worker = LogDownloadWorker("http://10.192.1.2:8090", "robot.log.active")
    results = []
    worker.finished.connect(lambda *args: results.append(args))

    worker.run()

    saved_path = Path(results[0][1])
    assert requested_urls[0][0] == "http://10.192.1.2:8090/log/robot.log.active"
    assert requested_urls[0][1]["follow_redirects"] is False
    assert saved_path == tmp_path / "logs" / "robot.log.active"
    assert saved_path.read_text(encoding="utf-8") == "diagnostic log"