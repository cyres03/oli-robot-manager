from network.ssh_client import SshResult
from services.calibrate_service import (
    MISSION_ENGINE_SWITCH_SERVICE,
    MissionEngineCalibrateWorker,
    _mros_call_succeeded,
    _mros_service_names,
)


class FakeSshClient:
    def __init__(self, results):
        self.results = list(results)
        self.commands = []
        self.closed = False

    def connect(self, timeout):
        assert timeout == 8

    def execute(self, command, timeout):
        self.commands.append((command, timeout))
        return self.results.pop(0)

    def close(self):
        self.closed = True


class RaisingSshClient(FakeSshClient):
    def execute(self, command, timeout):
        self.commands.append((command, timeout))
        if len(self.commands) == 1:
            return self.results.pop(0)
        raise TimeoutError("MissionEngine 调用超时")


class FakeRobotClient:
    def __init__(self):
        self.led_calls = []

    def enable_led_control(self, enable):
        self.led_calls.append(enable)
        return {"result": "success"}


class UnconfirmedLedRobotClient(FakeRobotClient):
    def enable_led_control(self, enable):
        self.led_calls.append(enable)
        return {"result": "success" if enable else "fail_busy"}


def _run_worker(monkeypatch, ssh_client, robot_client=None):
    monkeypatch.setattr(
        "services.calibrate_service.current_robot_id",
        lambda: "HU_D04_01_075",
    )
    monkeypatch.setattr(
        "services.calibrate_service.SshClient",
        lambda *args, **kwargs: ssh_client,
    )
    if robot_client is not None:
        monkeypatch.setattr(
            "services.calibrate_service.RobotClient",
            lambda *args, **kwargs: robot_client,
        )
    outcomes = []
    worker = MissionEngineCalibrateWorker("HU_D04_01_075")
    worker.finished.connect(
        lambda success, detail: outcomes.append((success, detail))
    )
    worker.run()
    return outcomes


def test_missing_switch_service_stops_before_led_or_calibration(monkeypatch):
    service_list = SshResult(
        0,
        " * /joint/calibration [type:std_srvs/SetInt32, md5:test]\n",
        "2026-08-26 14:48:23 I/mrosservice: VERSION: test\n",
    )
    ssh_client = FakeSshClient([service_list])

    def unexpected_robot_client(*args, **kwargs):
        raise AssertionError("LED control must not run without the full service")

    monkeypatch.setattr(
        "services.calibrate_service.RobotClient",
        unexpected_robot_client,
    )
    outcomes = _run_worker(monkeypatch, ssh_client)

    assert outcomes[0][0] is False
    assert "未提供完整校零接口" in outcomes[0][1]
    assert "未关闭 SDK LED" in outcomes[0][1]
    assert "不会直接调用" in outcomes[0][1]
    assert len(ssh_client.commands) == 1
    assert "mrosservice list" in ssh_client.commands[0][0]
    assert ssh_client.closed


def test_failed_call_restores_led_control(monkeypatch):
    service_list = SshResult(
        0,
        f" * {MISSION_ENGINE_SWITCH_SERVICE} [type:std_srvs/SetString, md5:test]\n",
        "",
    )
    failed_call = SshResult(
        0,
        "",
        "ERROR: Cannot find service [/mission_engine/switch_state].\n",
    )
    ssh_client = FakeSshClient([service_list, failed_call])
    robot_client = FakeRobotClient()

    outcomes = _run_worker(monkeypatch, ssh_client, robot_client)

    assert outcomes[0][0] is False
    assert "未返回成功响应" in outcomes[0][1]
    assert "Cannot find service" in outcomes[0][1]
    assert "SDK LED 控制已恢复" in outcomes[0][1]
    assert robot_client.led_calls == [False, True]


def test_successful_call_keeps_mission_engine_led_control(monkeypatch):
    service_list = SshResult(
        0,
        f" * {MISSION_ENGINE_SWITCH_SERVICE} [type:std_srvs/SetString, md5:test]\n",
        "",
    )
    successful_call = SshResult(
        0,
        "{'result': 'success'}\n",
        "2026-08-26 14:48:23 I/mrosservice(63306/63306): startup noise\n",
    )
    ssh_client = FakeSshClient([service_list, successful_call])
    robot_client = FakeRobotClient()

    outcomes = _run_worker(monkeypatch, ssh_client, robot_client)

    assert outcomes == [
        (
            True,
            "SDK LED 控制已关闭: {'result': 'success'}\n"
            "已进入 MissionEngine Calibration，机器人应显示校零中并使用蓝色灯语。\n"
            "{'result': 'success'}",
        )
    ]
    assert robot_client.led_calls == [False]


def test_call_exception_restores_led_control(monkeypatch):
    service_list = SshResult(
        0,
        f" * {MISSION_ENGINE_SWITCH_SERVICE} [type:std_srvs/SetString, md5:test]\n",
        "",
    )
    ssh_client = RaisingSshClient([service_list])
    robot_client = FakeRobotClient()

    outcomes = _run_worker(monkeypatch, ssh_client, robot_client)

    assert outcomes[0][0] is False
    assert "MissionEngine 调用超时" in outcomes[0][1]
    assert "SDK LED 控制已恢复" in outcomes[0][1]
    assert robot_client.led_calls == [False, True]


def test_failed_call_restores_led_after_unconfirmed_disable(monkeypatch):
    service_list = SshResult(
        0,
        f" * {MISSION_ENGINE_SWITCH_SERVICE} [type:std_srvs/SetString, md5:test]\n",
        "",
    )
    failed_call = SshResult(1, "", "call failed\n")
    ssh_client = FakeSshClient([service_list, failed_call])
    robot_client = UnconfirmedLedRobotClient()

    outcomes = _run_worker(monkeypatch, ssh_client, robot_client)

    assert outcomes[0][0] is False
    assert "SDK LED 控制未关闭" in outcomes[0][1]
    assert "SDK LED 控制已恢复" in outcomes[0][1]
    assert robot_client.led_calls == [False, True]


def test_mros_output_parsing_rejects_error_even_with_success_word():
    output = (
        "2026-08-26 14:48:23 I/mrosservice(63306/63306): VERSION: test\n"
        "ERROR: Cannot find service [/mission_engine/switch_state]. success\n"
    )

    assert _mros_service_names(
        f" * {MISSION_ENGINE_SWITCH_SERVICE} [type:std_srvs/SetString, md5:test]"
    ) == {MISSION_ENGINE_SWITCH_SERVICE}
    assert not _mros_call_succeeded(output)