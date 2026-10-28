import threading

from models.robot_profile import RobotIdentity, RobotIdentityStatus
from ui.main_window import MainWindow
import workers.robot_identity_worker as identity_worker_module


def test_identity_probe_runs_outside_gui_thread(qtbot, monkeypatch):
    caller_thread = threading.get_ident()
    observed = []
    identity = RobotIdentity(
        RobotIdentityStatus.NO_TARGET,
        message="未连接机器人 WiFi",
    )

    def detect(timeout):
        observed.append((threading.get_ident(), timeout))
        return identity

    monkeypatch.setattr(identity_worker_module, "detect_robot_identity", detect)
    worker = identity_worker_module.RobotIdentityWorker(timeout=0.35)

    with qtbot.waitSignal(worker.completed, timeout=1000) as signal:
        worker.start()

    assert signal.args == [identity]
    assert observed[0][1] == 0.35
    assert observed[0][0] != caller_thread
    assert worker.wait(1000)


def test_identity_poll_skips_an_inflight_probe(qtbot):
    class ProbeStub:
        def __init__(self):
            self.running = True
            self.starts = 0

        def isRunning(self):
            return self.running

        def start(self):
            self.starts += 1

    window = MainWindow()
    qtbot.addWidget(window)
    probe = ProbeStub()
    window._robot_identity_worker = probe

    window._poll_robot_identity()
    assert probe.starts == 0

    probe.running = False
    window._poll_robot_identity()
    assert probe.starts == 1