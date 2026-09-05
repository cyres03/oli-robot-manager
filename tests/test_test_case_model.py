import json
from pathlib import Path

import pytest

from models.managed_case import (
    HAND_FATIGUE_CAPABILITY,
    HAND_FATIGUE_RUNNER,
    TestCaseDefinition as CaseDefinition,
    TestRisk as Risk,
    TestSource as Source,
    load_test_cases,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_builtin_cases_include_profile_scoped_hand_fatigue():
    cases = load_test_cases(PROJECT_ROOT / "resources/test_cases/cases.json")

    assert [case.case_id for case in cases] == [
        "luna-mros-node-health",
        "luna-speech-vision-snapshot",
        "luna-hand-fatigue",
        "oli-hand-fatigue",
    ]
    luna_cases = [case for case in cases if case.product_key == "hu_l04_01"]
    assert {case.target_role for case in luna_cases} == {"main", "speech_vision"}
    assert len(luna_cases) == 3
    assert all("10.192." not in case.command for case in cases)
    assert all(case.is_first_phase_safe for case in cases[:2])
    mros_case = cases[0]
    assert mros_case.source == Source.BUNDLED_SCRIPT
    assert mros_case.target_role == "main"
    assert mros_case.script_path == "scripts/mros_node_health.sh"
    assert mros_case.arguments == (".",)
    assert mros_case.requires_pty is True
    assert cases[1].requires_pty is False
    assert cases[1].category == "伴随节点"
    for fatigue_case in cases[2:]:
        assert fatigue_case.source == Source.BUILTIN_RUNNER
        assert fatigue_case.runner == HAND_FATIGUE_RUNNER
        assert fatigue_case.required_capability == HAND_FATIGUE_CAPABILITY
        assert fatigue_case.risks == frozenset({Risk.HARDWARE_CONTROL})
        assert fatigue_case.arguments == ("7200", "10")
        assert fatigue_case.requires_confirmation is True
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


def test_hand_fatigue_runner_requires_capability_and_hardware_risk(tmp_path):
    manifest = tmp_path / "cases.json"
    manifest.write_text(json.dumps({
        "cases": [{
            "id": "luna-hand-fatigue",
            "name": "双灵巧手疲劳测试",
            "product_key": "hu_l04_01",
            "target_role": "main",
            "source": "builtin_runner",
            "runner": HAND_FATIGUE_RUNNER,
            "required_capability": HAND_FATIGUE_CAPABILITY,
            "category": "灵巧手",
            "timeout_seconds": 7500,
        }],
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="hardware_control"):
        load_test_cases(manifest)


def test_manifest_rejects_unknown_builtin_runner(tmp_path):
    manifest = tmp_path / "cases.json"
    manifest.write_text(json.dumps({
        "cases": [{
            "id": "unknown-runner",
            "name": "Unknown",
            "product_key": "hu_l04_01",
            "target_role": "main",
            "source": "builtin_runner",
            "runner": "unknown",
        }],
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="内置运行器无效"):
        load_test_cases(manifest)


def test_manifest_rejects_hand_fatigue_on_non_main_node(tmp_path):
    manifest = tmp_path / "cases.json"
    manifest.write_text(json.dumps({
        "cases": [{
            "id": "wrong-hand-node",
            "name": "Wrong node",
            "product_key": "hu_l04_01",
            "target_role": "speech_vision",
            "source": "builtin_runner",
            "runner": HAND_FATIGUE_RUNNER,
            "required_capability": HAND_FATIGUE_CAPABILITY,
            "risks": ["hardware_control"],
        }],
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="必须使用 main 节点"):
        load_test_cases(manifest)


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