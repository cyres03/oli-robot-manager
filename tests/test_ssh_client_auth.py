import paramiko
import pytest

from network.ssh_client import SshAuthenticationError, SshClient


class FakeParamikoClient:
    def __init__(self, error):
        self.error = error
        self.connect_calls = []

    def connect(self, host, **kwargs):
        self.connect_calls.append((host, kwargs))
        raise self.error

    def close(self):
        pass


def test_password_connection_error_is_not_reported_as_wrong_password(
    tmp_path, monkeypatch
):
    key_path = tmp_path / "operator_key"
    key_path.write_text("unused", encoding="utf-8")
    attempts = [
        FakeParamikoClient(paramiko.AuthenticationException("key rejected")),
        FakeParamikoClient(TimeoutError("connection timed out")),
    ]
    client = SshClient(
        "10.192.1.2",
        "limx",
        ["correct-password"],
        key_path=str(key_path),
    )
    monkeypatch.setattr(client, "_create_client", lambda: attempts.pop(0))

    with pytest.raises(ConnectionError) as caught:
        client.connect()

    assert not isinstance(caught.value, SshAuthenticationError)
    assert "connection timed out" in str(caught.value)
    assert attempts == []


def test_explicit_empty_password_list_uses_key_only(tmp_path, monkeypatch):
    key_path = tmp_path / "operator_key"
    key_path.write_text("unused", encoding="utf-8")
    key_attempt = FakeParamikoClient(
        paramiko.AuthenticationException("key rejected")
    )
    client = SshClient(
        "10.192.1.2",
        "limx",
        [],
        key_path=str(key_path),
    )
    clients_created = []

    def create_client():
        clients_created.append(key_attempt)
        return key_attempt

    monkeypatch.setattr(client, "_create_client", create_client)

    with pytest.raises(SshAuthenticationError):
        client.connect()

    assert clients_created == [key_attempt]
    assert len(key_attempt.connect_calls) == 1


def test_password_enrollment_skips_existing_key(tmp_path, monkeypatch):
    key_path = tmp_path / "operator_key"
    key_path.write_text("unused", encoding="utf-8")
    password_attempt = FakeParamikoClient(
        paramiko.AuthenticationException("password rejected")
    )
    client = SshClient(
        "10.192.1.2",
        "limx",
        ["example#password"],
        key_path=str(key_path),
        use_key=False,
    )
    clients_created = []

    def create_client():
        clients_created.append(password_attempt)
        return password_attempt

    monkeypatch.setattr(client, "_create_client", create_client)

    with pytest.raises(SshAuthenticationError):
        client.connect()

    assert clients_created == [password_attempt]
    _, connect_options = password_attempt.connect_calls[0]
    assert connect_options["password"] == "example#password"
    assert "key_filename" not in connect_options


def test_missing_key_without_password_requests_authorization(tmp_path):
    client = SshClient(
        "10.192.1.2",
        "limx",
        [],
        key_path=str(tmp_path / "missing_key"),
    )

    with pytest.raises(SshAuthenticationError, match="no SSH key"):
        client.connect()