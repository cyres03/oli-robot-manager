import pytest

from network.ssh_client import SshAuthenticationError, SshResult
from network.ssh_key_manager import (
    SshKeyVerificationError,
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

    with pytest.raises(SshKeyVerificationError, match="密码已验证"):
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


def test_worker_does_not_report_key_verification_as_wrong_password(monkeypatch):
    def fail_verification(*args, **kwargs):
        del args, kwargs
        raise SshKeyVerificationError("项目密钥复验失败")

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
        (False, "项目密钥复验失败", "key_verification")
    ]