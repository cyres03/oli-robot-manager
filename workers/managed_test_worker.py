"""Managed SSH test execution for profile-defined robot nodes."""
from dataclasses import replace
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
import shlex
import threading
import uuid

from PyQt6.QtCore import QThread, pyqtSignal

from models.robot_profile import RobotNode, RobotProfile
from models.managed_case import (
    TestCaseDefinition,
    TestRunResult,
    TestRunStatus,
    TestSource,
    normalize_artifact_path,
)
from network.ssh_client import (
    SshAuthenticationError,
    SshClient,
    SshExecutionCancelled,
)


class TestCaseWorker(QThread):
    output_line = pyqtSignal(str, str)
    completed = pyqtSignal(object)
    authentication_required = pyqtSignal(str, str, str)

    def __init__(
        self,
        case: TestCaseDefinition,
        profile: RobotProfile,
        accid: str,
        firmware: str,
        passwords: list[str],
        result_root: Path,
        resource_root: Path,
        local_script_path: Path | None = None,
        approved: bool = False,
        generation: int = 0,
        parent=None,
    ):
        super().__init__(parent)
        self.case = case
        self.profile = profile
        self.accid = accid
        self.firmware = firmware
        self.passwords = passwords
        self.result_root = result_root
        self.resource_root = resource_root
        self.local_script_path = local_script_path
        self.approved = approved
        self.generation = generation
        self.session_id = (
            datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        )
        self._cancel_event = threading.Event()
        self._client_lock = threading.Lock()
        self._active_client: SshClient | None = None
        self._managed_execution = False

    def cancel(self):
        self._cancel_event.set()
        with self._client_lock:
            client = self._active_client if not self._managed_execution else None
        if client:
            client.close()

    def run(self):
        started_at = datetime.now()
        node = self._resolve_node()
        target_host = node.host if node else ""
        client = None
        remote_dir = f"/tmp/oli-robot-manager/{self.session_id}"
        remote_dir_created = False
        result = None
        authorization_request = None
        try:
            self._validate(node)
            client = SshClient(
                node.host,
                node.username,
                self.passwords,
                robot_id=self.accid,
            )
            with self._client_lock:
                self._active_client = client
            client.connect(timeout=10, cancel_event=self._cancel_event)
            if self._cancel_event.is_set():
                raise SshExecutionCancelled("测试已取消")
            if self._needs_remote_dir():
                self._create_remote_dir(client, remote_dir)
                remote_dir_created = True
            command = self._prepare_command(client, remote_dir, remote_dir_created)
            with self._client_lock:
                self._managed_execution = True
            try:
                execution = client.execute_managed(
                    command,
                    self.output_line.emit,
                    self._cancel_event,
                    timeout=self.case.timeout_seconds,
                    max_output_bytes=1024 * 1024,
                    allocate_pty=self.case.requires_pty,
                )
            finally:
                with self._client_lock:
                    self._managed_execution = False
            if self._cancel_event.is_set():
                raise SshExecutionCancelled("测试已取消")
            downloaded_artifacts = self._download_artifacts(client, remote_dir)
            if self._cancel_event.is_set():
                raise SshExecutionCancelled("测试已取消")
            passed = execution.exit_code in self.case.expected_exit_codes
            if self.case.expected_stdout_pattern:
                passed = passed and bool(re.search(
                    self.case.expected_stdout_pattern,
                    execution.stdout,
                    flags=re.DOTALL,
                ))
            missing_required = [
                artifact.remote_path
                for artifact in self.case.artifacts
                if artifact.required
                and normalize_artifact_path(artifact.remote_path)
                not in downloaded_artifacts
            ]
            if missing_required:
                passed = False
            detail = "测试通过" if passed else "退出码、输出断言或必需产物不符合预期"
            if missing_required:
                detail += ": 缺少 " + ", ".join(missing_required)
            result = TestRunResult.create(
                session_id=self.session_id,
                case=self.case,
                accid=self.accid,
                firmware=self.firmware,
                target_host=target_host,
                status=TestRunStatus.PASS if passed else TestRunStatus.FAIL,
                started_at=started_at,
                exit_code=execution.exit_code,
                stdout=execution.stdout,
                stderr=execution.stderr,
                detail=detail,
                artifacts=tuple(downloaded_artifacts.values()),
            )
        except SshAuthenticationError:
            if node:
                authorization_request = (node.host, node.username, self.accid)
        except SshExecutionCancelled as error:
            result = TestRunResult.create(
                session_id=self.session_id,
                case=self.case,
                accid=self.accid,
                firmware=self.firmware,
                target_host=target_host,
                status=TestRunStatus.CANCELLED,
                started_at=started_at,
                detail=str(error),
            )
        except Exception as error:
            status = (
                TestRunStatus.CANCELLED
                if self._cancel_event.is_set()
                else TestRunStatus.ERROR
            )
            detail = "测试已取消" if status == TestRunStatus.CANCELLED else str(error)
            result = TestRunResult.create(
                session_id=self.session_id,
                case=self.case,
                accid=self.accid,
                firmware=self.firmware,
                target_host=target_host,
                status=status,
                started_at=started_at,
                detail=detail,
            )
        finally:
            with self._client_lock:
                self._managed_execution = False
                if self._active_client is client:
                    self._active_client = None
            cleanup_error = ""
            if client:
                if remote_dir_created and self.case.cleanup:
                    try:
                        cleanup = client.execute(
                            f"rm -rf -- {shlex.quote(remote_dir)}",
                            timeout=15,
                        )
                        if cleanup.exit_code != 0:
                            cleanup_error = f"退出码 {cleanup.exit_code}"
                    except Exception as error:
                        cleanup_error = str(error)
                try:
                    client.close()
                except Exception:
                    pass
            if cleanup_error and result:
                status = (
                    TestRunStatus.CANCELLED
                    if result.status == TestRunStatus.CANCELLED
                    else TestRunStatus.ERROR
                )
                result = replace(
                    result,
                    status=status,
                    detail=f"{result.detail}; 远端临时目录清理失败: {cleanup_error}",
                )

        if authorization_request:
            self.authentication_required.emit(*authorization_request)
        elif result:
            self.completed.emit(result)

    def _resolve_node(self) -> RobotNode | None:
        nodes = (self.profile.main_node, *self.profile.companion_nodes)
        return next((node for node in nodes if node.role == self.case.target_role), None)

    def _validate(self, node: RobotNode | None):
        if self.profile.key != self.case.product_key:
            raise ValueError(
                f"用例 {self.case.case_id} 属于 {self.case.product_key}，"
                f"当前型号为 {self.profile.key}"
            )
        if not self.accid:
            raise ValueError("机器人身份未识别")
        if node is None:
            raise ValueError(f"当前型号没有节点角色 {self.case.target_role}")
        if not self.case.supports_firmware(self.firmware):
            raise ValueError(
                f"测试用例 {self.case.case_id} 不支持固件 {self.firmware}"
            )
        self.case.validate_approval(self.approved)

    def _prepare_command(
        self,
        client: SshClient,
        remote_dir: str,
        uses_remote_dir: bool,
    ) -> str:
        if self.case.source == TestSource.REMOTE_COMMAND:
            payload = self.case.command
        else:
            local_path = self._local_script()
            remote_path = f"{remote_dir}/test_script{local_path.suffix}"
            self._upload_verified(client, local_path, remote_path)
            payload = " ".join([
                shlex.quote(self.case.interpreter),
                shlex.quote(remote_path),
                *(shlex.quote(argument) for argument in self.case.arguments),
            ])

        environment = " ".join([
            "env",
            f"OLI_ROBOT_ACCID={shlex.quote(self.accid)}",
            f"OLI_PROFILE_KEY={shlex.quote(self.profile.key)}",
            f"OLI_TARGET_ROLE={shlex.quote(self.case.target_role)}",
            f"OLI_TEST_SESSION={shlex.quote(self.session_id)}",
        ])
        if uses_remote_dir:
            payload = f"cd {shlex.quote(remote_dir)} && {environment} {payload}"
        else:
            payload = f"{environment} {payload}"
        return payload

    def _needs_remote_dir(self) -> bool:
        return self.case.source != TestSource.REMOTE_COMMAND or bool(self.case.artifacts)

    @staticmethod
    def _create_remote_dir(client: SshClient, remote_dir: str):
        created = client.execute(
            "umask 077; mkdir -p -- " + shlex.quote(remote_dir)
            + " && chmod 700 -- " + shlex.quote(remote_dir),
            timeout=15,
        )
        if created.exit_code != 0:
            raise RuntimeError(created.stderr or "无法创建远端测试目录")

    def _local_script(self) -> Path:
        if self.case.source == TestSource.BUNDLED_SCRIPT:
            path = (self.resource_root / self.case.script_path).resolve()
            root = self.resource_root.resolve()
            if root not in path.parents:
                raise ValueError("内置脚本路径超出资源目录")
        elif self.case.source == TestSource.LOCAL_SCRIPT:
            path = self.local_script_path.resolve() if self.local_script_path else None
        else:
            path = None
        if path is None or not path.is_file():
            raise FileNotFoundError("未选择有效的本地测试脚本")
        return path

    def _upload_verified(self, client: SshClient, local_path: Path, remote_path: str):
        local_hash = hashlib.sha256(local_path.read_bytes()).hexdigest()
        sftp = client._client.open_sftp()
        try:
            sftp.put(str(local_path), remote_path)
        finally:
            sftp.close()
        checksum = client.execute(
            f"chmod 600 -- {shlex.quote(remote_path)} && "
            f"sha256sum -- {shlex.quote(remote_path)}",
            timeout=15,
        )
        remote_hash = checksum.stdout.strip().split(" ", 1)[0]
        if checksum.exit_code != 0 or remote_hash != local_hash:
            raise RuntimeError("上传脚本 SHA-256 校验失败")

    def _download_artifacts(self, client: SshClient, remote_dir: str) -> dict[str, str]:
        if not self.case.artifacts:
            return {}
        local_dir = self.result_root / self.session_id
        local_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(local_dir, 0o700)
        saved = {}
        sftp = client._client.open_sftp()
        try:
            for artifact in self.case.artifacts:
                relative_path = normalize_artifact_path(artifact.remote_path)
                remote_path = f"{remote_dir}/{relative_path}"
                local_path = local_dir.joinpath(*relative_path.split("/"))
                local_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.chmod(local_path.parent, 0o700)
                try:
                    sftp.get(remote_path, str(local_path))
                except OSError:
                    local_path.unlink(missing_ok=True)
                    continue
                os.chmod(local_path, 0o600)
                saved[relative_path] = str(local_path)
        finally:
            sftp.close()
        return saved