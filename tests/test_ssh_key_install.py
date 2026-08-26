import pytest

from network.ssh_client import SshAuthenticationError, SshResult
from network.ssh_key_manager import (
    SshKeyVerificationError,
    SshKeyWriteError,
    install_operator_key,
)
from workers.ssh_key_install_worker import SshKeyInstallWorker


def test_install_operator_key_distinguishes_key_verification_failure(
    tmp_path, monkeypatch
):
    key_path = tmp_path / "operator_key"
    key_path.with_suffix(".pub").write_text(
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest operator@test\n",
        encoding="utf-8",
    )
    clients = []

    class FakeSshClient:
        def __init__(self, host, username, passwords, **kwargs):
            del host, username, kwargs
            self.passwords = passwords
            clients.append(self)

        def connect(self, timeout):
            del timeout
            if not self.passwords:
                raise SshAuthenticationError("public key rejected")

        def execute(self, command, timeout):
            del command, timeout
            return SshResult(0, "", "")

        def close(self):
            pass

    monkeypatch.setattr(
        "network.ssh_key_manager.ensure_operator_key",
        lambda requested_path: requested_path,
    )
    monkeypatch.setattr("network.ssh_key_manager.SshClient", FakeSshClient)

    with pytest.raises(SshKeyVerificationError, match="主控拒绝") as caught:
        install_operator_key(
            "10.192.1.2",
            "limx",
            "correct-password",
            "HU_D04_01_075",
            key_path=str(key_path),
        )

    assert [client.passwords for client in clients] == [
        ["correct-password"],
        [],
    ]
    assert caught.value.error_code == "key_rejected"


def test_worker_does_not_report_key_verification_as_wrong_password(monkeypatch):
    def fail_verification(*args, **kwargs):
        del args, kwargs
        raise SshKeyVerificationError("项目密钥复验失败", "key_connection")

    monkeypatch.setattr(
        "workers.ssh_key_install_worker.install_operator_key",
        fail_verification,
    )
    worker = SshKeyInstallWorker(
        "10.192.1.2",
        "limx",
        "correct-password",
        "HU_D04_01_075",
    )
    outcomes = []
    worker.completed.connect(
        lambda success, detail, error_code: outcomes.append(
            (success, detail, error_code)
        )
    )

    worker.run()

    assert outcomes == [
        (False, "项目密钥复验失败", "key_connection")
    ]


def test_install_operator_key_retries_transient_verification_failure(
    tmp_path, monkeypatch
):
    key_path = tmp_path / "operator_key"
    key_path.with_suffix(".pub").write_text(
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest operator@test\n",
        encoding="utf-8",
    )
    verification_attempts = 0
    commands = []

    class FakeSshClient:
        def __init__(self, host, username, passwords, **kwargs):
            del host, username, kwargs
            self.passwords = passwords

        def connect(self, timeout):
            nonlocal verification_attempts
            del timeout
            if not self.passwords:
                verification_attempts += 1
                if verification_attempts < 3:
                    raise ConnectionError("temporary route failure")

        def execute(self, command, timeout):
            del timeout
            commands.append(command)
            return SshResult(0, "", "")

        def close(self):
            pass

    retry_delays = []
    monkeypatch.setattr(
        "network.ssh_key_manager.ensure_operator_key",
        lambda requested_path: requested_path,
    )
    monkeypatch.setattr("network.ssh_key_manager.SshClient", FakeSshClient)
    monkeypatch.setattr(
        "network.ssh_key_manager.time.sleep", retry_delays.append
    )

    install_operator_key(
        "10.192.1.2",
        "limx",
        "correct-password",
        "HU_D04_01_075",
        key_path=str(key_path),
    )

    assert verification_attempts == 3
    assert retry_delays == [0.25, 0.25]
    assert "set -eu" in commands[0]
    assert "chmod go-w" in commands[0]
    assert "printf '\\n%s\\n'" in commands[0]


def test_install_operator_key_reports_remote_write_failure(tmp_path, monkeypatch):
    key_path = tmp_path / "operator_key"
    key_path.with_suffix(".pub").write_text(
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest operator@test\n",
        encoding="utf-8",
    )

    class FakeSshClient:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def connect(self, timeout):
            del timeout

        def execute(self, command, timeout):
            del command, timeout
            return SshResult(1, "", "chmod: Operation not permitted\n")

        def close(self):
            pass

    monkeypatch.setattr(
        "network.ssh_key_manager.ensure_operator_key",
        lambda requested_path: requested_path,
    )
    monkeypatch.setattr("network.ssh_key_manager.SshClient", FakeSshClient)

    with pytest.raises(SshKeyWriteError, match="密码正确") as caught:
        install_operator_key(
            "10.192.1.2",
            "limx",
            "correct-password",
            "HU_D04_01_075",
            key_path=str(key_path),
        )

    assert "Operation not permitted" in str(caught.value)