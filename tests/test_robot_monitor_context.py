from services.robot_monitor import RobotMonitor


def _robot_info_data(version="v1", sn="TRON2A_185"):
    return {
        "result": [{
            "name": "system_info",
            "values": [
                {"key": "robot_status", "value": "Damped"},
                {"key": "sn", "value": sn},
                {"key": "version", "value": version},
            ],
        }],
    }


def test_monitor_drops_status_from_old_target_generation(qapp):
    monitor = RobotMonitor()
    monitor.update_target("ws://old:5000", "HU_D04_01_001")
    old_generation = monitor.target_generation
    monitor.update_target("ws://new:5000", "TRON2A_185")
    statuses = []
    monitor.status_updated.connect(statuses.append)

    monitor._parse_robot_info(
        _robot_info_data("old-version"),
        old_generation,
        "ws://old:5000",
        "HU_D04_01_001",
    )

    assert statuses == []


def test_monitor_emits_current_target_context(qapp):
    monitor = RobotMonitor()
    monitor.update_target("ws://robot:5000", "TRON2A_185")
    generation = monitor.target_generation
    statuses = []
    monitor.status_updated.connect(statuses.append)

    monitor._parse_robot_info(
        _robot_info_data(),
        generation,
        "ws://robot:5000",
        "TRON2A_185",
    )

    assert statuses[0]["_target_generation"] == generation
    assert statuses[0]["_target_accid"] == "TRON2A_185"
    assert statuses[0]["_reported_accid"] == "TRON2A_185"
    assert statuses[0]["version"] == "v1"


def test_monitor_drops_status_with_mismatched_reported_sn(qapp):
    monitor = RobotMonitor()
    monitor.update_target("ws://robot:5000", "TRON2A_185")
    generation = monitor.target_generation
    statuses = []
    monitor.status_updated.connect(statuses.append)

    monitor._parse_robot_info(
        _robot_info_data(sn="HU_D04_01_001"),
        generation,
        "ws://robot:5000",
        "TRON2A_185",
    )

    assert statuses == []