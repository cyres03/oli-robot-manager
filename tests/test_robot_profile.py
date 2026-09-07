from models.robot_profile import (
    CapabilityState,
    L04_PROFILE,
    OLI_PROFILE,
    TRON2_PROFILE,
    RobotIdentityStatus,
    resolve_robot_identity,
    resolve_robot_profile,
)


def test_resolves_l04_identity_and_read_only_profile():
    identity = resolve_robot_identity(
        ["HU_L04_01_091_5G"],
        "HU_L04_01_091",
    )

    assert identity.status == RobotIdentityStatus.READY
    assert identity.accid == "HU_L04_01_091"
    assert identity.profile is L04_PROFILE
    assert identity.profile.main_node.host == "10.192.1.2"
    assert identity.profile.companion_nodes[0].host == "10.192.1.4"
    assert identity.profile.expected_motor_count == 27
    assert identity.profile.service("mcp").supported is False
    assert identity.profile.allows_tool("get_motions") is True
    assert identity.profile.allows_tool("execute_motion") is False
    assert identity.profile.capability("movement") == CapabilityState.PENDING_VALIDATION


def test_same_robot_bands_are_one_target():
    identity = resolve_robot_identity(
        ["HU_L04_01_091_2.4G", "HU_L04_01_091_5G"],
        "HU_L04_01_091",
    )

    assert identity.status == RobotIdentityStatus.READY
    assert identity.ssid_accids == ("HU_L04_01_091",)


def test_multiple_robot_instances_require_selection():
    identity = resolve_robot_identity(
        ["HU_L04_01_091_5G", "HU_D04_01_121_5G"],
        None,
    )

    assert identity.status == RobotIdentityStatus.MULTIPLE_TARGETS
    assert identity.profile is None


def test_ssid_and_portal_mismatch_blocks_target():
    identity = resolve_robot_identity(
        ["HU_L04_01_091_5G"],
        "HU_L04_01_092",
    )

    assert identity.status == RobotIdentityStatus.MISMATCH
    assert identity.accid is None
    assert identity.profile is None


def test_unknown_model_is_not_treated_as_oli():
    identity = resolve_robot_identity(["HU_X99_01_001_5G"], "HU_X99_01_001")

    assert identity.status == RobotIdentityStatus.UNSUPPORTED
    assert identity.accid == "HU_X99_01_001"
    assert identity.profile is None


def test_oli_profile_preserves_existing_control_tools():
    assert resolve_robot_profile("HU_D04_01_121") is OLI_PROFILE
    assert OLI_PROFILE.service("mcp").supported is True
    assert OLI_PROFILE.allows_tool("execute_dance") is True


def test_resolves_tron2_identity_with_conservative_baseline():
    accid = "TRON2A_185"
    identity = resolve_robot_identity([accid], accid)

    assert identity.status == RobotIdentityStatus.READY
    assert identity.accid == accid
    assert identity.profile is TRON2_PROFILE
    assert TRON2_PROFILE.matches("TRON2A_185") is True
    assert TRON2_PROFILE.matches("TRON2A_invalid") is False
    assert resolve_robot_profile("WF_TRON2A_185") is None

    assert TRON2_PROFILE.main_node.host == "10.192.1.2"
    assert TRON2_PROFILE.main_node.ssh_enabled is False
    assert TRON2_PROFILE.companion_nodes[0].host == "10.192.1.4"
    assert TRON2_PROFILE.companion_nodes[0].ssh_enabled is True
    assert TRON2_PROFILE.companion_nodes[0].username == "guest"
    assert TRON2_PROFILE.service("portal").supported is True
    assert TRON2_PROFILE.service("websocket").supported is True
    assert TRON2_PROFILE.service("logs").supported is False
    assert TRON2_PROFILE.service("mcp").supported is False
    assert TRON2_PROFILE.allowed_tools == frozenset()
    assert TRON2_PROFILE.capability("movement") == CapabilityState.PENDING_VALIDATION
    assert TRON2_PROFILE.capability("calibration") == CapabilityState.UNSUPPORTED
    assert TRON2_PROFILE.capability("hand_fatigue") == CapabilityState.UNSUPPORTED


def test_tron2a_wifi_band_matches_portal_identity():
    for ssid in ("TRON2A_185_5G", "TRON2A_185_2.4G"):
        identity = resolve_robot_identity(
            [ssid],
            "TRON2A_185",
        )

        assert identity.status == RobotIdentityStatus.READY
        assert identity.accid == "TRON2A_185"
        assert identity.profile is TRON2_PROFILE


def test_tron2a_rejects_legacy_wf_portal_identity():
    identity = resolve_robot_identity(
        ["TRON2A_185_5G"],
        "WF_TRON2A_185",
    )

    assert identity.status == RobotIdentityStatus.MISMATCH
    assert identity.profile is None