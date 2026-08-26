"""One-time SSH public-key enrollment for newly connected robots."""
import os
import shlex
import socket

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from network.ssh_client import DEFAULT_SSH_KEY_PATH, SshClient


class SshKeyVerificationError(ConnectionError):
    pass


def ensure_operator_key(key_path: str = DEFAULT_SSH_KEY_PATH) -> str:
    key_path = os.path.expanduser(key_path)
    public_key_path = key_path + ".pub"
    os.makedirs(os.path.dirname(key_path), mode=0o700, exist_ok=True)

    if os.path.isfile(key_path) and os.path.isfile(public_key_path):
        return key_path
    if os.path.exists(public_key_path) and not os.path.exists(key_path):
        raise RuntimeError(f"SSH 公钥存在但私钥缺失: {key_path}")

    if os.path.isfile(key_path):
        with open(key_path, "rb") as private_key_file:
            private_key = serialization.load_ssh_private_key(
                private_key_file.read(), password=None
            )
    else:
        private_key = Ed25519PrivateKey.generate()
        private_key_bytes = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        )
        descriptor = os.open(
            key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(descriptor, "wb") as private_key_file:
            private_key_file.write(private_key_bytes)

    public_key_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH,
    )
    comment = f"oli-robot-manager@{socket.gethostname()}"
    with open(public_key_path, "wb") as public_key_file:
        public_key_file.write(public_key_bytes + b" " + comment.encode("utf-8") + b"\n")
    os.chmod(key_path, 0o600)
    os.chmod(public_key_path, 0o644)
    return key_path


def install_operator_key(
    host: str,
    username: str,
    password: str,
    robot_id: str,
    key_path: str = DEFAULT_SSH_KEY_PATH,
    timeout: int = 10,
) -> None:
    if not password:
        raise ValueError("SSH 密码不能为空")

    key_path = ensure_operator_key(key_path)
    with open(key_path + ".pub", "r", encoding="utf-8") as public_key_file:
        public_key = public_key_file.read().strip()
    if not public_key.startswith("ssh-ed25519 "):
        raise RuntimeError("项目 SSH 公钥格式无效")

    client = SshClient(
        host,
        username,
        [password],
        key_path=key_path,
        robot_id=robot_id,
        use_key=False,
    )
    try:
        client.connect(
            timeout=timeout,
        )
        quoted_key = shlex.quote(public_key)
        command = (
            "umask 077; mkdir -p \"$HOME/.ssh\"; chmod 700 \"$HOME/.ssh\"; "
            "touch \"$HOME/.ssh/authorized_keys\"; "
            "chmod 600 \"$HOME/.ssh/authorized_keys\"; "
            f"key={quoted_key}; "
            "grep -qxF \"$key\" \"$HOME/.ssh/authorized_keys\" || "
            "printf '%s\\n' \"$key\" >> \"$HOME/.ssh/authorized_keys\""
        )
        result = client.execute(command, timeout=timeout)
        if result.exit_code != 0:
            raise RuntimeError(
                result.stderr.strip()
                or f"写入 authorized_keys 失败(exit={result.exit_code})"
            )
    finally:
        client.close()

    verifier = SshClient(
        host,
        username,
        [],
        key_path=key_path,
        robot_id=robot_id,
    )
    try:
        verifier.connect(timeout=timeout)
    except Exception as error:
        raise SshKeyVerificationError(
            "SSH 密码已验证且公钥已写入，但项目密钥复验失败: "
            f"{error}"
        ) from error
    finally:
        verifier.close()
