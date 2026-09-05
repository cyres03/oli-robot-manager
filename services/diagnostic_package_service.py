"""Export completed read-only diagnostic sessions as auditable ZIP bundles."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import zipfile

from models.acceptance import (
    AcceptanceSession,
    AcceptanceSessionPurpose,
    AcceptanceSessionStatus,
    is_sensitive_field,
    redact_acceptance_detail,
)
from models.robot_profile import resolve_robot_profile


DIAGNOSTIC_PACKAGE_SCHEMA_VERSION = 1
SAFE_SESSION_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}")
SAFE_CHECK_KEY = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


def export_diagnostic_package(
    session: AcceptanceSession,
    output_root: Path,
    *,
    secrets=(),
) -> Path:
    _validate_session(session)
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        os.chmod(output_root, 0o700)

    entries = _build_entries(session, secrets)
    checksums = "".join(
        f"{hashlib.sha256(content).hexdigest()}  {name}\n"
        for name, content in sorted(entries.items())
    ).encode("utf-8")
    entries["checksums.sha256"] = checksums

    destination = output_root / f"diagnostic-{session.session_id}.zip"
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".diagnostic-",
            suffix=".zip.tmp",
            dir=output_root,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        with zipfile.ZipFile(
            temporary_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for name, content in sorted(entries.items()):
                info = zipfile.ZipInfo(name)
                info.date_time = (1980, 1, 1, 0, 0, 0)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(info, content)
        if os.name != "nt":
            os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, destination)
        temporary_path = None
        if os.name != "nt":
            os.chmod(destination, 0o600)
        return destination
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def verify_diagnostic_package(package_path: Path) -> bool:
    package_path = Path(package_path)
    with zipfile.ZipFile(package_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or "checksums.sha256" not in names:
            return False
        if any(not _safe_archive_name(name) for name in names):
            return False
        expected = {}
        for line in archive.read("checksums.sha256").decode("utf-8").splitlines():
            try:
                digest, name = line.split("  ", 1)
            except ValueError:
                return False
            if not re.fullmatch(r"[a-f0-9]{64}", digest) or name in expected:
                return False
            expected[name] = digest
        payload_names = set(names) - {"checksums.sha256"}
        if set(expected) != payload_names:
            return False
        return all(
            hashlib.sha256(archive.read(name)).hexdigest() == digest
            for name, digest in expected.items()
        )


def _safe_archive_name(name: str) -> bool:
    if not name or "\\" in name or "\0" in name:
        return False
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return False
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts


def _validate_session(session: AcceptanceSession):
    if session.purpose != AcceptanceSessionPurpose.DIAGNOSTIC:
        raise ValueError("仅诊断会话可以导出诊断包")
    if session.status != AcceptanceSessionStatus.COMPLETED:
        raise ValueError("仅已完成的诊断会话可以导出诊断包")
    if not SAFE_SESSION_ID.fullmatch(session.session_id):
        raise ValueError("诊断会话 ID 格式无效")
    if not session.robot_accid or session.robot_accid == "unknown":
        raise ValueError("诊断会话缺少已验证机器人 ACCID")
    profile = resolve_robot_profile(session.robot_accid)
    if profile is None or profile.key != session.profile_key:
        raise ValueError("诊断会话的 Profile 与 ACCID 不匹配")
    if not isinstance(session.robot_versions, dict):
        raise ValueError("诊断会话的版本快照格式无效")
    check_keys = [item.check_key for item in session.items]
    if len(check_keys) != len(set(check_keys)):
        raise ValueError("诊断会话包含重复检查项")
    expected_counts = (
        sum(item.status.value == "PASS" for item in session.items),
        sum(item.status.value == "FAIL" for item in session.items),
        sum(item.status.value == "N/A" for item in session.items),
    )
    if expected_counts != (
        session.pass_count,
        session.fail_count,
        session.not_applicable_count,
    ):
        raise ValueError("诊断会话汇总计数与检查项不一致")
    for item in session.items:
        if not SAFE_CHECK_KEY.fullmatch(item.check_key):
            raise ValueError(f"诊断项 ID 格式无效: {item.check_key}")


def _build_entries(session: AcceptanceSession, secrets) -> dict[str, bytes]:
    manifest = {
        "schema_version": DIAGNOSTIC_PACKAGE_SCHEMA_VERSION,
        "session": {
            "session_id": session.session_id,
            "robot_accid": session.robot_accid,
            "profile_key": session.profile_key,
            "operator_name": session.operator_name,
            "software_version": session.software_version,
            "robot_firmware": session.robot_firmware,
            "robot_versions": session.robot_versions,
            "started_at": session.started_at,
            "completed_at": session.completed_at,
            "status": session.status.value,
            "purpose": session.purpose.value,
            "problem_description": session.problem_description,
            "pass_count": session.pass_count,
            "fail_count": session.fail_count,
            "not_applicable_count": session.not_applicable_count,
        },
        "checks": [
            {
                **asdict(item),
                "status": item.status.value,
            }
            for item in session.items
        ],
    }
    manifest_text = json.dumps(
        _redact_structure(manifest, secrets),
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    report_text = redact_acceptance_detail(_render_report(session), secrets)
    entries = {
        "manifest.json": manifest_text.encode("utf-8"),
        "report.md": report_text.encode("utf-8"),
    }
    for index, item in enumerate(session.items, start=1):
        evidence = "\n".join((
            f"检查项: {item.name}",
            f"检查 ID: {item.check_key}",
            f"分类: {item.category}",
            f"状态: {item.status.value}",
            f"执行时间: {item.executed_at}",
            f"摘要: {item.summary}",
            f"备注: {item.note or '-'}",
            "",
            "详细证据:",
            item.detail,
            "",
        ))
        entries[f"evidence/{index:02d}-{item.check_key}.txt"] = (
            redact_acceptance_detail(evidence, secrets).encode("utf-8")
        )
    return entries


def _redact_structure(value, secrets):
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if is_sensitive_field(key)
                else _redact_structure(item, secrets)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_structure(item, secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_structure(item, secrets) for item in value)
    if isinstance(value, str):
        return redact_acceptance_detail(value, secrets)
    return value


def _render_report(session: AcceptanceSession) -> str:
    lines = [
        "# 机器人售后诊断报告",
        "",
        f"- 会话：{session.session_id}",
        f"- 机器人：{session.robot_accid}",
        f"- 型号 Profile：{session.profile_key}",
        f"- 机器人固件：{session.robot_firmware}",
        f"- Robot Manager：{session.software_version}",
        f"- 操作员：{session.operator_name}",
        f"- 开始：{session.started_at}",
        f"- 完成：{session.completed_at or '-'}",
        "",
        "## 故障描述",
        "",
        session.problem_description or "未填写",
        "",
        "## 版本快照",
        "",
    ]
    if session.robot_versions:
        lines.extend(
            f"- {key}: {value}"
            for key, value in sorted(session.robot_versions.items())
        )
    else:
        lines.append("- 未读取")
    lines.extend((
        "",
        "## 检查汇总",
        "",
        f"PASS {session.pass_count} / FAIL {session.fail_count} / "
        f"N/A {session.not_applicable_count}",
        "",
        "| 分类 | 检查项 | 状态 | 摘要 |",
        "|---|---|---|---|",
    ))
    for item in session.items:
        summary = item.summary.replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {item.category} | {item.name} | {item.status.value} | {summary} |"
        )
    lines.extend((
        "",
        "详细原始证据位于 evidence/ 目录，checksums.sha256 用于完整性校验。",
        "",
    ))
    return "\n".join(lines)