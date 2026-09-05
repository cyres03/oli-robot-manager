from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
import uuid


SENSITIVE_FIELD_NAMES = frozenset({
    "password",
    "passwd",
    "passphrase",
    "wifi_password",
    "router_admin_password",
    "sudo_password",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "client_secret",
})
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)(?P<prefix>(?<![A-Za-z0-9_])['\"]?"
    r"(?:password|passwd|passphrase|wifi[_-]?password|"
    r"router[_-]?admin[_-]?password|sudo[_-]?password|token|"
    r"access[_-]?token|refresh[_-]?token|api[_-]?key|apikey|client[_-]?secret)"
    r"['\"]?\s*[:=]\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)


def is_sensitive_field(name: object) -> bool:
    normalized = str(name).strip().lower().replace("-", "_")
    return normalized in SENSITIVE_FIELD_NAMES


class AcceptanceSessionStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class AcceptanceSessionPurpose(str, Enum):
    ACCEPTANCE = "acceptance"
    DIAGNOSTIC = "diagnostic"


class AcceptanceItemStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "N/A"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def redact_acceptance_detail(detail: str, secrets=()) -> str:
    sanitized = str(detail or "")
    sanitized = re.sub(
        r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
        "[REDACTED PRIVATE KEY]",
        sanitized,
        flags=re.DOTALL,
    )
    sanitized = re.sub(
        r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/-]+=*",
        r"\1[REDACTED]",
        sanitized,
    )
    sanitized = _SENSITIVE_ASSIGNMENT_RE.sub(
        _redact_assignment,
        sanitized,
    )
    unique_secrets = sorted(
        {str(secret) for secret in secrets if secret and len(str(secret)) >= 4},
        key=len,
        reverse=True,
    )
    for secret in unique_secrets:
        sanitized = sanitized.replace(secret, "[REDACTED]")
    return sanitized


def _redact_assignment(match: re.Match) -> str:
    value = match.group("value")
    if value.startswith(('"', "'")) and value.endswith(value[0]):
        replacement = f"{value[0]}[REDACTED]{value[0]}"
    else:
        replacement = "[REDACTED]"
    return f"{match.group('prefix')}{replacement}"


@dataclass(frozen=True)
class AcceptanceItemResult:
    check_key: str
    category: str
    name: str
    status: AcceptanceItemStatus
    summary: str
    detail: str
    executed_at: str
    note: str = ""

    @classmethod
    def create(
        cls,
        *,
        check_key: str,
        category: str,
        name: str,
        status: AcceptanceItemStatus,
        summary: str,
        detail: str,
        note: str = "",
        secrets=(),
    ) -> "AcceptanceItemResult":
        return cls(
            check_key=check_key,
            category=category,
            name=name,
            status=status,
            summary=redact_acceptance_detail(summary, secrets),
            detail=redact_acceptance_detail(detail, secrets),
            executed_at=utc_now_iso(),
            note=redact_acceptance_detail(note, secrets),
        )


@dataclass
class AcceptanceSession:
    session_id: str
    robot_accid: str
    profile_key: str
    operator_name: str
    software_version: str
    started_at: str
    purpose: AcceptanceSessionPurpose = AcceptanceSessionPurpose.ACCEPTANCE
    problem_description: str = ""
    robot_firmware: str = "unknown"
    robot_versions: dict[str, str] = field(default_factory=dict)
    package_path: str = ""
    completed_at: str | None = None
    status: AcceptanceSessionStatus = AcceptanceSessionStatus.RUNNING
    pass_count: int = 0
    fail_count: int = 0
    not_applicable_count: int = 0
    items: list[AcceptanceItemResult] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        *,
        robot_accid: str,
        profile_key: str,
        operator_name: str,
        software_version: str,
        purpose: AcceptanceSessionPurpose = AcceptanceSessionPurpose.ACCEPTANCE,
        problem_description: str = "",
        robot_firmware: str = "unknown",
        robot_versions: dict[str, str] | None = None,
        secrets=(),
    ) -> "AcceptanceSession":
        now = datetime.now(timezone.utc)
        return cls(
            session_id=f"{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}",
            robot_accid=robot_accid,
            profile_key=profile_key,
            operator_name=operator_name,
            software_version=software_version,
            started_at=now.isoformat(timespec="seconds"),
            purpose=purpose,
            problem_description=redact_acceptance_detail(
                problem_description,
                secrets,
            ),
            robot_firmware=robot_firmware or "unknown",
            robot_versions={
                str(key): redact_acceptance_detail(value, secrets)
                for key, value in (robot_versions or {}).items()
            },
        )

    def add_result(self, result: AcceptanceItemResult):
        self.items = [item for item in self.items if item.check_key != result.check_key]
        self.items.append(result)
        self._update_counts()

    def finish(self, status: AcceptanceSessionStatus):
        self.status = status
        self.completed_at = utc_now_iso()
        self._update_counts()

    def _update_counts(self):
        self.pass_count = sum(
            item.status == AcceptanceItemStatus.PASS for item in self.items
        )
        self.fail_count = sum(
            item.status == AcceptanceItemStatus.FAIL for item in self.items
        )
        self.not_applicable_count = sum(
            item.status == AcceptanceItemStatus.NOT_APPLICABLE
            for item in self.items
        )