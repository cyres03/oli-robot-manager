import json
from pathlib import Path

import pytest

from models.managed_case import (
    TestCaseDefinition as CaseDefinition,
    TestRisk as Risk,
    TestSource as Source,
    load_test_cases,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_builtin_luna_cases_target_profile_roles_without_ips():
    cases = load_test_cases(PROJECT_ROOT / "resources/test_cases/cases.json")

    assert [case.case_id for case in cases] == [
        "luna-mros-node-health",
        "luna-speech-vision-snapshot",
    ]
    assert {case.target_role for case in cases} == {"main", "speech_vision"}
    assert all(case.product_key == "hu_l04_01" for case in cases)
    assert all("10.192." not in case.command for case in cases)
    assert all(case.is_first_phase_safe for case in cases)
    mros_case = cases[0]
    assert mros_case.source == Source.BUNDLED_SCRIPT
    assert mros_case.target_role == "main"
    assert mros_case.script_path == "scripts/mros_node_health.sh"
    assert mros_case.arguments == (".",)
    assert mros_case.requires_pty is True
    assert cases[1].requires_pty is False
    assert cases[1].category == "伴随节点"
    script = (PROJECT_ROOT / "resources/test_cases" / mros_case.script_path).read_text(
        encoding="utf-8"
    )
    assert "mrosconsole 2>&1 | grep -E -m 200 --" in script


def test_high_risk_case_requires_explicit_approval():
    case = CaseDefinition(
        case_id="luna-hand-fatigue",
        name="双灵巧手疲劳测试",
        product_key="hu_l04_01",
        target_role="main",
        source=Source.LOCAL_SCRIPT,
        category="灵巧手",
        timeout_seconds=7200,
        risks=frozenset({Risk.HARDWARE_CONTROL}),
    )

    with pytest.raises(PermissionError, match="hardware_control"):
        case.validate_approval(False)
    case.validate_approval(True)


def test_remote_command_requires_confirmation_even_when_read_only():
    case = CaseDefinition(
        case_id="luna-read-only-command",
        name="只读命令",
        product_key="hu_l04_01",
        target_role="main",
        source=Source.REMOTE_COMMAND,
        category="节点健康",
        timeout_seconds=10,
        command="hostname",
    )

    assert case.is_first_phase_safe is True
    assert case.confirmation_reasons == ("remote_command",)
    with pytest.raises(PermissionError, match="remote_command"):
        case.validate_approval(False)
    case.validate_approval(True)


def test_case_can_restrict_supported_firmware():
    case = CaseDefinition(
        case_id="luna-versioned-case",
        name="版本约束",
        product_key="hu_l04_01",
        target_role="main",
        source=Source.REMOTE_COMMAND,
        category="测试",
        timeout_seconds=10,
        command="true",
        firmware_pattern=r"^robot-luna-r-1\.2\.",
    )

    assert case.supports_firmware("robot-luna-r-1.2.12") is True
    assert case.supports_firmware("robot-luna-r-2.0.0") is False


def test_bundled_script_cannot_escape_resource_directory(tmp_path):
    manifest = tmp_path / "cases.json"
    manifest.write_text(json.dumps({
        "cases": [{
            "id": "bad-script",
            "name": "Bad",
            "product_key": "hu_l04_01",
            "target_role": "main",
            "source": "bundled_script",
            "script_path": "../outside.py"
        }]
    }), encoding="utf-8")
    (tmp_path.parent / "outside.py").write_text("print('bad')", encoding="utf-8")

    with pytest.raises(ValueError, match="超出测试资源目录"):
        load_test_cases(manifest)


@pytest.mark.parametrize("artifact_path", ["../result.json", "a\\result.json", "a//result.json"])
def test_manifest_rejects_unsafe_artifact_paths(tmp_path, artifact_path):
    manifest = tmp_path / "cases.json"
    manifest.write_text(json.dumps({
        "cases": [{
            "id": "bad-artifact",
            "name": "Bad artifact",
            "product_key": "hu_l04_01",
            "target_role": "main",
            "source": "remote_command",
            "command": "true",
            "artifacts": [{"remote_path": artifact_path}],
        }]
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="POSIX"):
        load_test_cases(manifest)


def test_manifest_rejects_duplicate_artifact_paths(tmp_path):
    manifest = tmp_path / "cases.json"
    manifest.write_text(json.dumps({
        "cases": [{
            "id": "duplicate-artifact",
            "name": "Duplicate artifact",
            "product_key": "hu_l04_01",
            "target_role": "main",
            "source": "remote_command",
            "command": "true",
            "artifacts": [
                {"remote_path": "a/result.json"},
                {"remote_path": "a/result.json"},
            ],
        }]
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="不能重复"):
        load_test_cases(manifest)


def test_manifest_requires_remote_session_cleanup(tmp_path):
    manifest = tmp_path / "cases.json"
    manifest.write_text(json.dumps({
        "cases": [{
            "id": "unsafe-cleanup",
            "name": "Unsafe cleanup",
            "product_key": "hu_l04_01",
            "target_role": "main",
            "source": "remote_command",
            "command": "true",
            "cleanup": False,
        }]
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="必须清理"):
        load_test_cases(manifest)