from ui.widgets.status_banner import StatusBanner


def test_status_banner_displays_and_clears_robot_sn(qtbot):
    banner = StatusBanner()
    qtbot.addWidget(banner)

    assert banner.layout().indexOf(banner.sn_label) >= 0

    banner.update_status({
        "sn": "HU_D04_01_121",
        "robot_status": "Stand",
        "ability": "idle",
        "mode": "remote",
        "battery_pct": 80,
        "imu_status": "OK",
    })

    assert banner.sn_label.text() == "HU_D04_01_121"

    banner.set_disconnected()

    assert banner.sn_label.text() == ""