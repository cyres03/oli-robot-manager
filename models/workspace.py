"""Product workspaces sharing one application shell and robot core."""
from dataclasses import dataclass

from models.robot_profile import RobotProfile


@dataclass(frozen=True)
class WorkspaceRoute:
    key: str
    label: str


@dataclass(frozen=True)
class WorkspaceDefinition:
    key: str
    display_name: str
    profile_key: str | None
    default_route: str
    routes: tuple[WorkspaceRoute, ...]

    def route(self, key: str) -> WorkspaceRoute | None:
        return next((route for route in self.routes if route.key == key), None)


CONNECTION_WORKSPACE = WorkspaceDefinition(
    key="connection",
    display_name="连接工作区",
    profile_key=None,
    default_route="settings",
    routes=(WorkspaceRoute("settings", "连接设置"),),
)

OLI_WORKSPACE = WorkspaceDefinition(
    key="oli",
    display_name="Oli 工作区",
    profile_key="oli",
    default_route="dance_library",
    routes=(
        WorkspaceRoute("dance_library", "Oli 舞蹈与动作"),
        WorkspaceRoute("controls", "Oli 基础控制"),
        WorkspaceRoute("acceptance", "Oli 验收"),
        WorkspaceRoute("log_analysis", "日志诊断"),
        WorkspaceRoute("health_check", "健康检查"),
        WorkspaceRoute("calibrate", "校零与 Backlash"),
        WorkspaceRoute("settings", "设置"),
    ),
)

LUNA_WORKSPACE = WorkspaceDefinition(
    key="luna",
    display_name="Luna 工作区",
    profile_key="hu_l04_01",
    default_route="dance_library",
    routes=(
        WorkspaceRoute("dance_library", "Luna 资源库"),
        WorkspaceRoute("controls", "状态与查询"),
        WorkspaceRoute("test_cases", "测试用例"),
        WorkspaceRoute("acceptance", "Luna 验收"),
        WorkspaceRoute("log_analysis", "日志诊断"),
        WorkspaceRoute("health_check", "节点健康"),
        WorkspaceRoute("settings", "设置"),
    ),
)

WORKSPACES = (OLI_WORKSPACE, LUNA_WORKSPACE)


def resolve_workspace(profile: RobotProfile | None) -> WorkspaceDefinition:
    if profile is None:
        return CONNECTION_WORKSPACE
    return next(
        (workspace for workspace in WORKSPACES if workspace.profile_key == profile.key),
        CONNECTION_WORKSPACE,
    )