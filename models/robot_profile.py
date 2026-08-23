import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class CapabilityState(str, Enum):
    SUPPORTED = "supported"
    PENDING_VALIDATION = "pending_validation"
    UNSUPPORTED = "unsupported"


class RobotIdentityStatus(str, Enum):
    READY = "ready"
    NO_TARGET = "no_target"
    MULTIPLE_TARGETS = "multiple_targets"
    MISMATCH = "mismatch"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class RobotNode:
    role: str
    label: str
    host: str
    username: str
    expected_cpu_cores: int | None = None


@dataclass(frozen=True)
class ServiceEndpoint:
    key: str
    label: str
    url: str | None

    @property
    def supported(self) -> bool:
        return self.url is not None


@dataclass(frozen=True)
class RobotProfile:
    key: str
    display_name: str
    model_prefixes: tuple[str, ...]
    ssid_prefixes: tuple[str, ...]
    main_node: RobotNode
    companion_nodes: tuple[RobotNode, ...]
    service_endpoints: tuple[ServiceEndpoint, ...]
    expected_motor_count: int | None
    expected_imu_hz: float
    allowed_tools: frozenset[str]
    capabilities: tuple[tuple[str, CapabilityState], ...]
    acceptance_check_keys: tuple[str, ...]
    allow_cpu_repair: bool = False
    allow_time_repair: bool = False

    def matches(self, identifier: str) -> bool:
        normalized = identifier.upper()
        return any(
            normalized == prefix.upper() or normalized.startswith(prefix.upper() + "_")
            for prefix in self.model_prefixes
        )

    def service(self, key: str) -> ServiceEndpoint:
        endpoint = next((item for item in self.service_endpoints if item.key == key), None)
        if endpoint is None:
            raise KeyError(key)
        return endpoint

    def capability(self, key: str) -> CapabilityState:
        return dict(self.capabilities).get(key, CapabilityState.UNSUPPORTED)

    def allows_tool(self, tool_name: str) -> bool:
        return tool_name in self.allowed_tools


@dataclass(frozen=True)
class RobotIdentity:
    status: RobotIdentityStatus
    accid: str | None = None
    profile: RobotProfile | None = None
    ssid_accids: tuple[str, ...] = ()
    portal_accid: str | None = None
    message: str = ""

    @property
    def ready(self) -> bool:
        return self.status == RobotIdentityStatus.READY


ALL_ROBOT_TOOLS = frozenset({
    "calibrate",
    "get_dances",
    "get_motions",
    "execute_dance",
    "execute_motion",
    "set_walk_velocity",
    "set_walk_mode",
    "set_motion_engine",
    "get_action_library_status",
    "prepare",
    "damping",
    "zero_torque",
    "sit_down",
    "standup",
    "lie_down",
    "safe_stop",
    "audio_get_wakeup",
    "audio_wakeup_control",
    "audio_set_volume",
    "enable_led_control",
    "led_control",
})

L04_READ_ONLY_TOOLS = frozenset({
    "get_dances",
    "get_motions",
    "get_action_library_status",
    "audio_get_wakeup",
})


OLI_PROFILE = RobotProfile(
    key="oli",
    display_name="Oli",
    model_prefixes=("HU_D04_01",),
    ssid_prefixes=("HU_D04_01",),
    main_node=RobotNode("main", "主控", "10.192.1.2", "limx"),
    companion_nodes=(
        RobotNode("perception", "感知", "10.192.1.3", "guest", expected_cpu_cores=8),
    ),
    service_endpoints=(
        ServiceEndpoint("portal", "机器人信息页", "http://10.192.1.2:8080"),
        ServiceEndpoint("logs", "日志服务", "http://10.192.1.2:8090"),
        ServiceEndpoint("websocket", "WebSocket SDK", "ws://10.192.1.2:5000"),
        ServiceEndpoint("mcp", "MCP", "http://10.192.1.2:18080/mcp"),
    ),
    expected_motor_count=None,
    expected_imu_hz=500.0,
    allowed_tools=ALL_ROBOT_TOOLS,
    capabilities=(
        ("status", CapabilityState.SUPPORTED),
        ("read_only_queries", CapabilityState.SUPPORTED),
        ("movement", CapabilityState.SUPPORTED),
        ("action_execution", CapabilityState.SUPPORTED),
        ("calibration", CapabilityState.SUPPORTED),
        ("backlash", CapabilityState.SUPPORTED),
        ("mcp", CapabilityState.SUPPORTED),
    ),
    acceptance_check_keys=(
        "wifi", "portal", "logs", "mcp", "main_ssh", "companion_ssh",
        "main_time", "companion_time", "main_disk", "main_process", "cpu",
        "camera", "imu",
    ),
    allow_cpu_repair=True,
    allow_time_repair=True,
)


L04_PROFILE = RobotProfile(
    key="hu_l04_01",
    display_name="Luna L04",
    model_prefixes=("HU_L04_01",),
    ssid_prefixes=("HU_L04_01",),
    main_node=RobotNode("main", "主控", "10.192.1.2", "limx", expected_cpu_cores=4),
    companion_nodes=(
        RobotNode("speech_vision", "语音/视觉伴随节点", "10.192.1.4", "guest", expected_cpu_cores=8),
    ),
    service_endpoints=(
        ServiceEndpoint("portal", "机器人信息页", "http://10.192.1.2:8080"),
        ServiceEndpoint("logs", "日志服务", "http://10.192.1.2:8090"),
        ServiceEndpoint("websocket", "WebSocket SDK", "ws://10.192.1.2:5000"),
        ServiceEndpoint("mcp", "MCP", None),
    ),
    expected_motor_count=27,
    expected_imu_hz=500.0,
    allowed_tools=L04_READ_ONLY_TOOLS,
    capabilities=(
        ("status", CapabilityState.SUPPORTED),
        ("read_only_queries", CapabilityState.SUPPORTED),
        ("audio_query", CapabilityState.SUPPORTED),
        ("movement", CapabilityState.PENDING_VALIDATION),
        ("action_execution", CapabilityState.PENDING_VALIDATION),
        ("calibration", CapabilityState.PENDING_VALIDATION),
        ("backlash", CapabilityState.PENDING_VALIDATION),
        ("audio_control", CapabilityState.PENDING_VALIDATION),
        ("led_control", CapabilityState.PENDING_VALIDATION),
        ("claw", CapabilityState.UNSUPPORTED),
        ("ub_manipulation", CapabilityState.UNSUPPORTED),
        ("wb_manipulation", CapabilityState.UNSUPPORTED),
        ("mcp", CapabilityState.UNSUPPORTED),
    ),
    acceptance_check_keys=(
        "wifi", "portal", "logs", "mcp", "main_ssh", "companion_ssh",
        "main_time", "companion_time", "main_disk", "main_process", "cpu",
        "camera", "imu",
    ),
)


ROBOT_PROFILES = (OLI_PROFILE, L04_PROFILE)


def _normalize_robot_identifier(value: str) -> str:
    return re.sub(r"(?:_(?:5G|2\.4G))$", "", value.strip(), flags=re.IGNORECASE)


def extract_robot_accid(text: str | None) -> str | None:
    if not text:
        return None

    normalized = _normalize_robot_identifier(text)
    patterns = (
        r"(HU_[A-Z0-9]+(?:_[A-Z0-9]+){1,3})",
        r"(WF_TRON2[A-Z]?_\d+)",
        r"(WF_[A-Z0-9]+(?:_[A-Z0-9]+)+)",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def resolve_robot_profile(identifier: str | None) -> RobotProfile | None:
    accid = extract_robot_accid(identifier)
    if not accid:
        return None
    return next((profile for profile in ROBOT_PROFILES if profile.matches(accid)), None)


def resolve_robot_identity(
    connected_ssids: Iterable[str],
    portal_identifier: str | None,
) -> RobotIdentity:
    ssid_accids = tuple(dict.fromkeys(
        accid.upper()
        for accid in (extract_robot_accid(ssid) for ssid in connected_ssids)
        if accid
    ))

    if not ssid_accids:
        return RobotIdentity(
            RobotIdentityStatus.NO_TARGET,
            message="未连接机器人 WiFi",
        )
    if len(ssid_accids) > 1:
        return RobotIdentity(
            RobotIdentityStatus.MULTIPLE_TARGETS,
            ssid_accids=ssid_accids,
            message="检测到多个已连接机器人，请选择目标机器人",
        )

    ssid_accid = ssid_accids[0]
    portal_accid = extract_robot_accid(portal_identifier)
    if portal_identifier and not portal_accid:
        return RobotIdentity(
            RobotIdentityStatus.MISMATCH,
            ssid_accids=ssid_accids,
            message="机器人信息页返回了无法识别的 SN",
        )
    if portal_accid and portal_accid.upper() != ssid_accid:
        return RobotIdentity(
            RobotIdentityStatus.MISMATCH,
            ssid_accids=ssid_accids,
            portal_accid=portal_accid.upper(),
            message=f"WiFi 身份 {ssid_accid} 与机器人 SN {portal_accid.upper()} 不一致",
        )

    profile = resolve_robot_profile(ssid_accid)
    if profile is None:
        return RobotIdentity(
            RobotIdentityStatus.UNSUPPORTED,
            accid=ssid_accid,
            ssid_accids=ssid_accids,
            portal_accid=portal_accid.upper() if portal_accid else None,
            message=f"暂不支持机器人型号 {ssid_accid}",
        )

    return RobotIdentity(
        RobotIdentityStatus.READY,
        accid=ssid_accid,
        profile=profile,
        ssid_accids=ssid_accids,
        portal_accid=portal_accid.upper() if portal_accid else None,
        message=f"已识别 {profile.display_name} ({ssid_accid})",
    )