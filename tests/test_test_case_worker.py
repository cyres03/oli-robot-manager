from pathlib import Path
import hashlib
import os

from models.robot_profile import L04_PROFILE
from models.managed_case import (
    ArtifactDefinition,
    TestCaseDefinition as CaseDefinition,
    TestRunStatus as RunStatus,
    TestSource as Source,
)
from network.ssh_client import SshResult
from workers.managed_test_worker import TestCaseWorker as ManagedWorker


class FakeClient:
    instances = []
    exit_code = 0
    stdout = "node=luna\ncores=8"

    def __init__(self, host, username, passwords, robot_id):
        self.host = host
        self.username = username
        self.passwords = passwords
        self.robot_id = robot_id
        self.commands = []
        self.closed = False
        FakeClient.instances.append(self)

    def connect(self, timeout=10, cancel_event=None):
        del timeout, cancel_event
        return True

    def execute_managed(
        self,
        command,
        on_line,
        cancel_event,
        timeout,
        max_output_bytes,
        allocate_pty=False,
    ):
        self.commands.append(("managed", command))
        self.allocate_pty = allocate_pty
        for line in self.stdout.splitlines():
            on_line(line, "stdout")
        return SshResult(self.exit_code, self.stdout, "")

    def execute(self, command, timeout=30):
        self.commands.append(("execute", command))
        return SshResult(0, "", "")

    def close(self):
        self.closed = True


class FakeSftp:
    def __init__(self):
        self.uploads = []
        self.downloads = []

    def put(self, local_path, remote_path):
        self.uploads.append((local_path, remote_path))

    def get(self, remote_path, local_path):
        self.downloads.append((remote_path, local_path))

    def close(self):
        pass


class UploadConnection:
    def __init__(self, sftp):
        self.sftp = sftp

    def open_sftp(self):
        return self.sftp


def _case(role="main", expected="node=.*cores=[0-9]+"):
    return CaseDefinition(
        case_id="luna-node-snapshot",
        name="Luna 节点快照",
        product_key="hu_l04_01",
        target_role=role,
        source=Source.REMOTE_COMMAND,
        category="节点健康",
        timeout_seconds=20,
        command="hostname; nproc",
        expected_stdout_pattern=expected,
    )


def _worker(tmp_path, case):
    return ManagedWorker(
        case=case,
        profile=L04_PROFILE,
        accid="HU_L04_01_093",
        firmware="v1",
        passwords=[],
        result_root=tmp_path,
        resource_root=Path("resources/test_cases"),
        approved=True,
    )


def test_worker_runs_read_only_case_on_profile_node(tmp_path, monkeypatch):
    import workers.managed_test_worker as module

    FakeClient.instances.clear()
    monkeypatch.setattr(module, "SshClient", FakeClient)
    worker = _worker(tmp_path, _case("speech_vision"))
    results = []
    worker.completed.connect(results.append)

    worker.run()

    client = FakeClient.instances[-1]
    assert client.host == "10.192.1.4"
    assert client.username == "guest"
    assert client.robot_id == "HU_L04_01_093"
    assert results[0].status == RunStatus.PASS
    assert results[0].firmware == "v1"
    assert client.closed is True


def test_worker_reports_failed_output_assertion(tmp_path, monkeypatch):
    import workers.managed_test_worker as module

    FakeClient.instances.clear()
    monkeypatch.setattr(module, "SshClient", FakeClient)
    worker = _worker(tmp_path, _case(expected="required-marker"))
    results = []
    worker.completed.connect(results.append)

    worker.run()

    assert results[0].status == RunStatus.FAIL
    assert results[0].exit_code == 0


def test_worker_rejects_missing_profile_role_without_ssh(tmp_path, monkeypatch):
    import workers.managed_test_worker as module

    FakeClient.instances.clear()
    monkeypatch.setattr(module, "SshClient", FakeClient)
    worker = _worker(tmp_path, _case("missing-role"))
    results = []
    worker.completed.connect(results.append)

    worker.run()

    assert FakeClient.instances == []
    assert results[0].status == RunStatus.ERROR
    assert "没有节点角色" in results[0].detail


def test_worker_rejects_unsupported_firmware_without_ssh(tmp_path, monkeypatch):
    import workers.managed_test_worker as module

    FakeClient.instances.clear()
    monkeypatch.setattr(module, "SshClient", FakeClient)
    case = CaseDefinition(
        case_id="luna-versioned-case",
        name="版本约束",
        product_key="hu_l04_01",
        target_role="main",
        source=Source.REMOTE_COMMAND,
        category="测试",
        timeout_seconds=20,
        command="true",
        firmware_pattern=r"^unsupported$",
    )
    worker = _worker(tmp_path, case)
    results = []
    worker.completed.connect(results.append)

    worker.run()

    assert FakeClient.instances == []
    assert results[0].status == RunStatus.ERROR
    assert "不支持固件" in results[0].detail


def test_worker_cleans_temporary_session_directory(tmp_path, monkeypatch):
    import workers.managed_test_worker as module

    FakeClient.instances.clear()
    monkeypatch.setattr(module, "SshClient", FakeClient)
    case = CaseDefinition(
        case_id="luna-temp-command",
        name="临时目录测试",
        product_key="hu_l04_01",
        target_role="main",
        source=Source.REMOTE_COMMAND,
        category="测试",
        timeout_seconds=20,
        command="printf result > result.txt",
        artifacts=(),
    )
    worker = _worker(tmp_path, case)
    results = []
    worker.completed.connect(results.append)

    worker.run()

    client = FakeClient.instances[-1]
    assert results[0].status == RunStatus.PASS
    assert not any("mkdir -p" in command for _, command in client.commands)
    assert not any("rm -rf" in command for _, command in client.commands)


def test_worker_uploads_script_checks_sha_and_cleans_session(tmp_path, monkeypatch):
    import workers.managed_test_worker as module

    script = tmp_path / "sample.py"
    script.write_text("print('ok')", encoding="utf-8")
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    sftp = FakeSftp()

    class UploadClient(FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._client = UploadConnection(sftp)

        def execute(self, command, timeout=30):
            self.commands.append(("execute", command))
            if "sha256sum" in command:
                return SshResult(0, digest + "  test_script.py\n", "")
            return SshResult(0, "", "")

    FakeClient.instances.clear()
    monkeypatch.setattr(module, "SshClient", UploadClient)
    case = CaseDefinition(
        case_id="luna-local-script",
        name="本地脚本",
        product_key="hu_l04_01",
        target_role="main",
        source=Source.LOCAL_SCRIPT,
        category="测试",
        timeout_seconds=20,
    )
    worker = ManagedWorker(
        case=case,
        profile=L04_PROFILE,
        accid="HU_L04_01_093",
        firmware="v1",
        passwords=[],
        result_root=tmp_path / "results",
        resource_root=Path("resources/test_cases"),
        local_script_path=script,
        approved=True,
    )
    results = []
    worker.completed.connect(results.append)

    worker.run()

    client = FakeClient.instances[-1]
    assert results[0].status == RunStatus.PASS
    assert sftp.uploads[0][0] == str(script)
    assert "/tmp/oli-robot-manager/" in sftp.uploads[0][1]
    assert any("sha256sum" in command for _, command in client.commands)
    assert any("rm -rf -- /tmp/oli-robot-manager/" in command for _, command in client.commands)


def test_worker_reports_remote_cleanup_failure(tmp_path, monkeypatch):
    import workers.managed_test_worker as module

    class ArtifactSftp(FakeSftp):
        def get(self, remote_path, local_path):
            super().get(remote_path, local_path)
            Path(local_path).write_text("artifact", encoding="utf-8")

    class CleanupFailureClient(FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._client = UploadConnection(ArtifactSftp())

        def execute(self, command, timeout=30):
            self.commands.append(("execute", command))
            if command.startswith("rm -rf"):
                return SshResult(1, "", "permission denied")
            return SshResult(0, "", "")

    FakeClient.instances.clear()
    monkeypatch.setattr(module, "SshClient", CleanupFailureClient)
    case = CaseDefinition(
        case_id="luna-cleanup-failure",
        name="清理失败",
        product_key="hu_l04_01",
        target_role="main",
        source=Source.REMOTE_COMMAND,
        category="测试",
        timeout_seconds=20,
        command="printf artifact > artifact.txt",
        artifacts=(ArtifactDefinition("artifact.txt"),),
    )
    worker = _worker(tmp_path, case)
    results = []
    worker.completed.connect(results.append)

    worker.run()

    assert results[0].status == RunStatus.ERROR
    assert "远端临时目录清理失败" in results[0].detail


def test_downloaded_artifact_has_private_permissions(tmp_path, monkeypatch):
    import stat
    import workers.managed_test_worker as module

    class ArtifactSftp(FakeSftp):
        def get(self, remote_path, local_path):
            super().get(remote_path, local_path)
            Path(local_path).write_text("artifact", encoding="utf-8")

    class ArtifactClient(FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._client = UploadConnection(ArtifactSftp())

    FakeClient.instances.clear()
    monkeypatch.setattr(module, "SshClient", ArtifactClient)
    case = CaseDefinition(
        case_id="luna-private-artifact",
        name="私有产物",
        product_key="hu_l04_01",
        target_role="main",
        source=Source.REMOTE_COMMAND,
        category="测试",
        timeout_seconds=20,
        command="printf artifact > artifact.txt",
        artifacts=(ArtifactDefinition("artifact.txt", required=True),),
    )
    worker = _worker(tmp_path, case)
    results = []
    worker.completed.connect(results.append)

    worker.run()

    artifact_path = Path(results[0].artifacts[0])
    if os.name != "nt":
        assert stat.S_IMODE(artifact_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(artifact_path.stat().st_mode) == 0o600


def test_worker_preserves_artifact_paths_with_duplicate_basenames(tmp_path, monkeypatch):
    import workers.managed_test_worker as module

    class NestedArtifactSftp(FakeSftp):
        def get(self, remote_path, local_path):
            super().get(remote_path, local_path)
            Path(local_path).write_text(remote_path, encoding="utf-8")

    class NestedArtifactClient(FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._client = UploadConnection(NestedArtifactSftp())

    FakeClient.instances.clear()
    monkeypatch.setattr(module, "SshClient", NestedArtifactClient)
    case = CaseDefinition(
        case_id="luna-nested-artifacts",
        name="同名产物",
        product_key="hu_l04_01",
        target_role="main",
        source=Source.REMOTE_COMMAND,
        category="测试",
        timeout_seconds=20,
        command="true",
        artifacts=(
            ArtifactDefinition("main/result.json", required=True),
            ArtifactDefinition("vision/result.json", required=True),
        ),
    )
    worker = _worker(tmp_path, case)
    results = []
    worker.completed.connect(results.append)

    worker.run()

    relative_paths = {
        Path(path).relative_to(tmp_path / worker.session_id).as_posix()
        for path in results[0].artifacts
    }
    assert results[0].status == RunStatus.PASS
    assert relative_paths == {"main/result.json", "vision/result.json"}


def test_cancel_closes_client_outside_managed_execution(tmp_path):
    worker = _worker(tmp_path, _case())
    connecting_client = FakeClient("host", "user", [], "robot")
    worker._active_client = connecting_client
    worker._managed_execution = False

    worker.cancel()

    assert connecting_client.closed is True

    executing_worker = _worker(tmp_path, _case())
    executing_client = FakeClient("host", "user", [], "robot")
    executing_worker._active_client = executing_client
    executing_worker._managed_execution = True

    executing_worker.cancel()

    assert executing_client.closed is False