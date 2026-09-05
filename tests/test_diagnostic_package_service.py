import hashlib
import json
import os
from pathlib import Path
import stat
import zipfile

import pytest

from models.acceptance import (
    AcceptanceItemResult,
    AcceptanceItemStatus,
    AcceptanceSession,
    AcceptanceSessionPurpose,
    AcceptanceSessionStatus,
)
from services.diagnostic_package_service import (
    export_diagnostic_package,
    verify_diagnostic_package,
)


def _completed_diagnostic():
    session = AcceptanceSession.create(
        robot_accid="HU_D04_01_075",
        profile_key="oli",
        operator_name="tester",
        software_version="1.0.1",
        purpose=AcceptanceSessionPurpose.DIAGNOSTIC,
        problem_description="右臂启动失败 password=secret-value",
        robot_firmware="robot-oli-r-24.4.10",
        robot_versions={"ecm_version": "1.2.3"},
    )
    session.add_result(AcceptanceItemResult.create(
        check_key="main_ssh",
        category="SSH",
        name="主控 SSH 登录",
        status=AcceptanceItemStatus.PASS,
        summary="robot-main",
        detail="password=secret-value\nhostname=robot-main",
    ))
    session.finish(AcceptanceSessionStatus.COMPLETED)
    return session


def test_exported_package_contains_report_manifest_evidence_and_checksums(tmp_path):
    session = _completed_diagnostic()

    package_path = export_diagnostic_package(
        session,
        tmp_path / "diagnostics",
        secrets=("secret-value",),
    )

    with zipfile.ZipFile(package_path) as archive:
        assert archive.namelist() == [
            "checksums.sha256",
            "evidence/01-main_ssh.txt",
            "manifest.json",
            "report.md",
        ]
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["session"]["robot_accid"] == "HU_D04_01_075"
        assert manifest["session"]["purpose"] == "diagnostic"
        assert manifest["session"]["robot_versions"] == {"ecm_version": "1.2.3"}
        assert manifest["checks"][0]["status"] == "PASS"
        all_content = b"\n".join(archive.read(name) for name in archive.namelist())
        assert b"secret-value" not in all_content
        assert "机器人售后诊断报告" in archive.read("report.md").decode("utf-8")

        checksum_lines = archive.read("checksums.sha256").decode("utf-8").splitlines()
        expected = {}
        for line in checksum_lines:
            digest, name = line.split("  ", 1)
            expected[name] = digest
        for name in ("manifest.json", "report.md", "evidence/01-main_ssh.txt"):
            assert expected[name] == hashlib.sha256(archive.read(name)).hexdigest()

    if os.name != "nt":
        assert stat.S_IMODE(package_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(package_path.stat().st_mode) == 0o600
    assert verify_diagnostic_package(package_path) is True


def test_export_rejects_non_diagnostic_or_incomplete_sessions(tmp_path):
    acceptance = AcceptanceSession.create(
        robot_accid="HU_D04_01_075",
        profile_key="oli",
        operator_name="tester",
        software_version="1.0.1",
    )
    acceptance.finish(AcceptanceSessionStatus.COMPLETED)
    with pytest.raises(ValueError, match="仅诊断会话"):
        export_diagnostic_package(acceptance, tmp_path)

    diagnostic = _completed_diagnostic()
    diagnostic.status = AcceptanceSessionStatus.RUNNING
    with pytest.raises(ValueError, match="仅已完成"):
        export_diagnostic_package(diagnostic, tmp_path)


def test_export_rejects_unsafe_session_or_check_identifiers(tmp_path):
    session = _completed_diagnostic()
    session.session_id = "../escape"
    with pytest.raises(ValueError, match="会话 ID"):
        export_diagnostic_package(session, tmp_path)

    session = _completed_diagnostic()
    session.items[0] = AcceptanceItemResult(
        check_key="../escape",
        category=session.items[0].category,
        name=session.items[0].name,
        status=session.items[0].status,
        summary=session.items[0].summary,
        detail=session.items[0].detail,
        executed_at=session.items[0].executed_at,
    )
    with pytest.raises(ValueError, match="诊断项 ID"):
        export_diagnostic_package(session, tmp_path)


def test_export_rejects_profile_mismatch_and_inconsistent_counts(tmp_path):
    session = _completed_diagnostic()
    session.profile_key = "hu_l04_01"
    with pytest.raises(ValueError, match="Profile 与 ACCID"):
        export_diagnostic_package(session, tmp_path)

    session = _completed_diagnostic()
    session.pass_count = 99
    with pytest.raises(ValueError, match="汇总计数"):
        export_diagnostic_package(session, tmp_path)


def test_verifier_detects_tampered_manifest(tmp_path):
    package_path = export_diagnostic_package(
        _completed_diagnostic(),
        tmp_path,
        secrets=("secret-value",),
    )
    with zipfile.ZipFile(package_path) as archive:
        entries = {
            name: archive.read(name)
            for name in archive.namelist()
        }
    entries["manifest.json"] = b"tampered"
    with zipfile.ZipFile(package_path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)

    assert verify_diagnostic_package(package_path) is False


@pytest.mark.parametrize("unsafe_name", [
    "../outside.txt",
    "..\\outside.txt",
    "C:\\outside.txt",
    "\\\\server\\share\\file.txt",
    "/absolute.txt",
])
def test_verifier_rejects_cross_platform_unsafe_paths(tmp_path, unsafe_name):
    package_path = tmp_path / "unsafe.zip"
    payload = b"content"
    checksum = hashlib.sha256(payload).hexdigest()
    with zipfile.ZipFile(package_path, "w") as archive:
        archive.writestr(unsafe_name, payload)
        archive.writestr(
            "checksums.sha256",
            f"{checksum}  {unsafe_name}\n".encode("utf-8"),
        )

    assert verify_diagnostic_package(package_path) is False