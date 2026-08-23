import json
import os
from dataclasses import dataclass, field

import httpx
from models.robot_profile import (
    RobotIdentity,
    RobotProfile,
    extract_robot_accid,
    resolve_robot_identity,
)


LOCAL_CONFIG_PATH = os.environ.get(
    "OLI_CONFIG_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.local.json"),
)


def _load_local_config() -> dict:
    try:
        with open(LOCAL_CONFIG_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取本地配置 {LOCAL_CONFIG_PATH}: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"本地配置必须是 JSON 对象: {LOCAL_CONFIG_PATH}")
    return data


_LOCAL_CONFIG = _load_local_config()


def _local_secret(key: str, env_name: str) -> str:
    env_value = os.environ.get(env_name)
    if env_value is not None:
        return env_value
    value = _LOCAL_CONFIG.get(key, "")
    return str(value) if value is not None else ""


def _main_control_passwords() -> tuple[str, ...]:
    env_value = os.environ.get("OLI_MAIN_CONTROL_PASSWORDS")
    if env_value is not None:
        return tuple(value for value in env_value.split(os.pathsep) if value)

    value = _LOCAL_CONFIG.get("main_control_passwords", [])
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, list):
        return tuple(str(item) for item in value if item is not None)
    raise RuntimeError("main_control_passwords 必须是字符串或字符串数组")


def _router_admin_password() -> str:
    password = _local_secret("router_admin_password", "OLI_ROUTER_ADMIN_PASSWORD")
    return password or _local_secret("wifi_password", "OLI_WIFI_PASSWORD")


def detect_accid_from_robot_portal(timeout: float = 2.0) -> str | None:
    try:
        response = httpx.get("http://10.192.1.2:8080/get_robot_info", timeout=timeout, follow_redirects=True)
        if response.is_success:
            data = response.json()
            sn = data.get("sn") if isinstance(data, dict) else None
            if sn:
                return extract_robot_accid(sn)
    except Exception:
        pass

    try:
        response = httpx.get("http://10.192.1.2:8080/get_local_version", timeout=timeout, follow_redirects=True)
        if response.is_success:
            data = response.json()
            sn = data.get("sn") if isinstance(data, dict) else None
            if sn:
                return extract_robot_accid(sn)
    except Exception:
        pass

    try:
        response = httpx.get("http://10.192.1.2:8080", timeout=timeout, follow_redirects=True)
        if response.is_success:
            return extract_robot_accid(response.text)
    except Exception:
        pass
    return None


def detect_robot_identity(timeout: float = 2.0) -> RobotIdentity:
    from network.wifi_manager import WifiManager

    connected_ssids = WifiManager.get_connected_robot_ssids()
    if not connected_ssids:
        return resolve_robot_identity((), None)

    portal_accid = detect_accid_from_robot_portal(timeout=timeout)
    return resolve_robot_identity(connected_ssids, portal_accid)


def detect_accid_from_wifi() -> str | None:
    """Auto-detect robot accid from WiFi or robot info portal.

    Examples:
    HU_D04_01_303_5G -> HU_D04_01_303
    HU_D04_01_303_2.4G -> HU_D04_01_303
    WF_TRON2A_001 -> WF_TRON2A_001
    """
    identity = detect_robot_identity()
    return identity.accid if identity.ready else None


@dataclass
class RobotConfig:
    mcp_url: str = "http://10.192.1.2:18080/mcp"
    websocket_url: str = "ws://10.192.1.2:5000"
    ws_accid: str = ""
    ollama_url: str = "http://10.192.1.3:11434/api/generate"
    main_control_user: str = "limx"
    main_control_ip: str = "10.192.1.2"
    main_control_passwords: tuple[str, ...] = field(default_factory=_main_control_passwords)
    perception_user: str = "guest"
    perception_ip: str = "10.192.1.3"
    perception_password: str = field(
        default_factory=lambda: _local_secret("perception_password", "OLI_PERCEPTION_PASSWORD")
    )
    wifi_ssid_patterns: tuple[str, ...] = (
        "HU_D",
        "HU_L04_01",
        "WF_TRON2A",
        "WF_TRON2",
        "WF_",
    )
    wifi_password: str = field(
        default_factory=lambda: _local_secret("wifi_password", "OLI_WIFI_PASSWORD")
    )
    router_admin_password: str = field(default_factory=_router_admin_password)
    expected_cpu_cores: int = 8
    expected_imu_hz: float = 500.0
    imu_tolerance_hz: float = 20.0
    power_cycle_countdown_seconds: int = 300
    cpu_fix_max_retries: int = 3
    active_profile: RobotProfile | None = field(default=None, repr=False)
    profile_key: str = ""
    model_name: str = "未识别"
    portal_url: str = "http://10.192.1.2:8080"
    logs_url: str = "http://10.192.1.2:8090"
    mcp_supported: bool = False
    expected_motor_count: int | None = None
    allow_cpu_repair: bool = False
    allow_time_repair: bool = False

    def apply_identity(self, identity: RobotIdentity) -> bool:
        if not identity.ready or not identity.accid or not identity.profile:
            self.clear_identity()
            return False

        profile = identity.profile
        companion = profile.companion_nodes[0] if profile.companion_nodes else None
        self.active_profile = profile
        self.profile_key = profile.key
        self.model_name = profile.display_name
        self.ws_accid = identity.accid
        self.main_control_ip = profile.main_node.host
        self.main_control_user = profile.main_node.username
        if companion:
            self.perception_ip = companion.host
            self.perception_user = companion.username
            self.expected_cpu_cores = companion.expected_cpu_cores or self.expected_cpu_cores
        self.portal_url = profile.service("portal").url or ""
        self.logs_url = profile.service("logs").url or ""
        self.websocket_url = profile.service("websocket").url or ""
        self.mcp_url = profile.service("mcp").url or ""
        self.mcp_supported = profile.service("mcp").supported
        self.expected_motor_count = profile.expected_motor_count
        self.expected_imu_hz = profile.expected_imu_hz
        self.allow_cpu_repair = profile.allow_cpu_repair
        self.allow_time_repair = profile.allow_time_repair
        return True

    def clear_identity(self):
        self.active_profile = None
        self.profile_key = ""
        self.model_name = "未识别"
        self.ws_accid = ""
        self.mcp_supported = False
        self.expected_motor_count = None
        self.allow_cpu_repair = False
        self.allow_time_repair = False


@dataclass
class AppConfig:
    data_dir: str = field(default_factory=lambda: os.path.join(
        os.path.expanduser("~"), ".oli-robot-manager"))

    def __post_init__(self):
        os.makedirs(self.data_dir, exist_ok=True)

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "oli_manager.db")


ROBOT_CONFIG = RobotConfig()
APP_CONFIG = AppConfig()
