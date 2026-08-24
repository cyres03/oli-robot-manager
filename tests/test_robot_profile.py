from models.robot_profile import (
    CapabilityState,
    L04_PROFILE,
    OLI_PROFILE,
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