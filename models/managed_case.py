"""Declarative test cases executed against profile-defined robot nodes."""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import json
from pathlib import Path, PurePosixPath
import re


class TestSource(str, Enum):
    REMOTE_COMMAND = "remote_command"
    BUNDLED_SCRIPT = "bundled_script"
    LOCAL_SCRIPT = "local_script"


class TestRisk(str, Enum):
    TEMPORARY_WRITE = "temporary_write"
    HARDWARE_CONTROL = "hardware_control"
    SUDO = "sudo"
    RESTART = "restart"
    PERSISTENT_WRITE = "persistent_write"


class TestRunStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"


HIGH_RISK_FLAGS = frozenset({
    TestRisk.HARDWARE_CONTROL,
    TestRisk.SUDO,
    TestRisk.RESTART,
    TestRisk.PERSISTENT_WRITE,
})


@dataclass(frozen=True)
class ArtifactDefinition:
    remote_path: str
    required: bool = False


def normalize_artifact_path(remote_path: str) -> str:
    value = str(remote_path).strip()
    path = PurePosixPath(value)
    normalized = path.as_posix()
    if (
        not value
        or "\\" in value
        or "\0" in value
        or path.is_absolute()
        or ".." in path.parts
        or normalized in {"", "."}
        or normalized != value
    ):
        raise ValueError(f"产物路径必须是规范的 POSIX 会话相对路径: {remote_path}")
    return normalized


@dataclass(frozen=True)
class TestCaseDefinition:
    case_id: str
    name: str
    product_key: str
    target_role: str
    source: TestSource
    category: str
    timeout_seconds: int
    command: str = ""
    script_path: str = ""
    interpreter: str = "python3"
    arguments: tuple[str, ...] = ()
    expected_exit_codes: tuple[int, ...] = (0,)
    expected_stdout_pattern: str = ""
    firmware_pattern: str = ""
    artifacts: tuple[ArtifactDefinition, ...] = ()
    risks: frozenset[TestRisk] = frozenset()
    cleanup: bool = True

    @property
    def confirmation_reasons(self) -> tuple[str, ...]:
        reasons = {flag.value for flag in self.risks & HIGH_RISK_FLAGS}
        if self.source in {TestSource.REMOTE_COMMAND, TestSource.LOCAL_SCRIPT}:
            reasons.add(self.source.value)
        return tuple(sorted(reasons))

    @property
    def requires_confirmation(self) -> bool:
        return bool(self.confirmation_reasons)

    @property
    def is_first_phase_safe(self) -> bool:
        return not bool(self.risks & HIGH_RISK_FLAGS)

    def validate_approval(self, approved: bool = False):
        if self.requires_confirmation and not approved:
            reasons = ", ".join(self.confirmation_reasons)
            raise PermissionError(f"测试用例 {self.case_id} 需要执行确认: {reasons}")

    def supports_firmware(self, firmware: str) -> bool:
        if not self.firmware_pattern:
            return True
        return bool(re.search(self.firmware_pattern, firmware or ""))


@dataclass(frozen=True)
class TestRunResult:
    session_id: str
    case_id: str
    profile_key: str
    accid: str
    firmware: str
    target_role: str
    target_host: str
    status: TestRunStatus
    started_at: str
    completed_at: str
    exit_code: int | None
    stdout: str
    stderr: str
    detail: str
    artifacts: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        case: TestCaseDefinition,
        accid: str,
        firmware: str,
        target_host: str,
        status: TestRunStatus,
        started_at: datetime,
        exit_code: int | None = None,
        stdout: str = "",
        stderr: str = "",
        detail: str = "",
        artifacts: tuple[str, ...] = (),
    ) -> "TestRunResult":
        return cls(
            session_id=session_id,
            case_id=case.case_id,
            profile_key=case.product_key,
            accid=accid,
            firmware=firmware,
            target_role=case.target_role,
            target_host=target_host,
            status=status,
            started_at=started_at.isoformat(timespec="seconds"),
            completed_at=datetime.now().isoformat(timespec="seconds"),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            detail=detail,
            artifacts=artifacts,
        )


def _safe_bundled_path(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve()
    root = root.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"脚本路径超出测试资源目录: {relative_path}")
    return candidate


def _parse_case(raw: object, resource_root: Path) -> TestCaseDefinition:
    if not isinstance(raw, dict):
        raise ValueError("测试用例必须是 JSON 对象")
    case_id = str(raw.get("id", "")).strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", case_id):
        raise ValueError(f"无效测试用例 ID: {case_id}")

    try:
        source = TestSource(str(raw.get("source", "")))
        risks = frozenset(TestRisk(str(value)) for value in raw.get("risks", []))
    except ValueError as error:
        raise ValueError(f"测试用例 {case_id} 的枚举字段无效: {error}") from error

    command = str(raw.get("command", "")).strip()
    script_path = str(raw.get("script_path", "")).strip()
    if source == TestSource.REMOTE_COMMAND and not command:
        raise ValueError(f"测试用例 {case_id} 缺少 command")
    if source == TestSource.BUNDLED_SCRIPT:
        if not script_path:
            raise ValueError(f"测试用例 {case_id} 缺少 script_path")
        path = _safe_bundled_path(resource_root, script_path)
        if not path.is_file():
            raise ValueError(f"测试脚本不存在: {script_path}")

    timeout_seconds = int(raw.get("timeout_seconds", 30))
    if not 1 <= timeout_seconds <= 86400:
        raise ValueError(f"测试用例 {case_id} timeout_seconds 超出范围")

    expected_exit_codes = tuple(int(value) for value in raw.get("expected_exit_codes", [0]))
    expected_pattern = str(raw.get("expected_stdout_pattern", ""))
    if expected_pattern:
        re.compile(expected_pattern)
    firmware_pattern = str(raw.get("firmware_pattern", ""))
    if firmware_pattern:
        re.compile(firmware_pattern)

    artifacts = tuple(
        ArtifactDefinition(
            remote_path=normalize_artifact_path(item.get("remote_path", "")),
            required=bool(item.get("required", False)),
        )
        for item in raw.get("artifacts", [])
        if isinstance(item, dict)
    )
    artifact_paths = [artifact.remote_path for artifact in artifacts]
    if len(artifact_paths) != len(set(artifact_paths)):
        raise ValueError(f"测试用例 {case_id} 的产物路径不能重复")
    cleanup = raw.get("cleanup", True)
    if not isinstance(cleanup, bool):
        raise ValueError(f"测试用例 {case_id} 的 cleanup 必须是布尔值")
    if not cleanup:
        raise ValueError(f"测试用例 {case_id} 必须清理远端会话目录")
    return TestCaseDefinition(
        case_id=case_id,
        name=str(raw.get("name", case_id)).strip(),
        product_key=str(raw.get("product_key", "")).strip(),
        target_role=str(raw.get("target_role", "")).strip(),
        source=source,
        category=str(raw.get("category", "通用")).strip(),
        timeout_seconds=timeout_seconds,
        command=command,
        script_path=script_path,
        interpreter=str(raw.get("interpreter", "python3")).strip() or "python3",
        arguments=tuple(str(value) for value in raw.get("arguments", [])),
        expected_exit_codes=expected_exit_codes,
        expected_stdout_pattern=expected_pattern,
        firmware_pattern=firmware_pattern,
        artifacts=artifacts,
        risks=risks,
        cleanup=cleanup,
    )


def load_test_cases(manifest_path: Path) -> list[TestCaseDefinition]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list):
        raise ValueError("测试用例清单必须包含 cases 数组")
    resource_root = manifest_path.parent
    cases = [_parse_case(raw, resource_root) for raw in data["cases"]]
    identifiers = [case.case_id for case in cases]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("测试用例 ID 不能重复")
    return cases