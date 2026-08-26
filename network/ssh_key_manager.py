"""One-time SSH public-key enrollment for newly connected robots."""
import os
import shlex
import socket
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from network.ssh_client import (
    DEFAULT_SSH_KEY_PATH,
    SshAuthenticationError,
    SshClient,
    SshRobotMismatchError,
)


_KEY_VERIFICATION_ATTEMPTS = 3
_KEY_VERIFICATION_RETRY_DELAY_SECONDS = 0.25


class SshKeyVerificationError(ConnectionError):
    def __init__(self, message: str, error_code: str = "key_verification"):
        super().__init__(message)
        self.error_code = error_code


class SshKeyWriteError(ConnectionError):
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
            "set -eu; umask 077; chmod go-w \"$HOME\"; "
            "mkdir -p \"$HOME/.ssh\"; chmod 700 \"$HOME/.ssh\"; "
            "touch \"$HOME/.ssh/authorized_keys\"; "
            "chmod 600 \"$HOME/.ssh/authorized_keys\"; "
            f"key={quoted_key}; "
            "grep -qxF \"$key\" \"$HOME/.ssh/authorized_keys\" || "
            "printf '\\n%s\\n' \"$key\" >> \"$HOME/.ssh/authorized_keys\"; "
            "grep -qxF \"$key\" \"$HOME/.ssh/authorized_keys\""
        )
        result = client.execute(command, timeout=timeout)
        if result.exit_code != 0:
            error_detail = " ".join(result.stderr.split())
            if len(error_detail) > 240:
                error_detail = error_detail[:237] + "..."
            raise SshKeyWriteError(
                "SSH 密码正确，但无法写入远端公钥。"
                + (
                    f"远端返回: {error_detail}"
                    if error_detail
                    else f"远端命令退出码: {result.exit_code}"
                )
            )
    finally:
        client.close()

    last_error: Exception | None = None
    for attempt in range(_KEY_VERIFICATION_ATTEMPTS):
        verifier = SshClient(
            host,
            username,
            [],
            key_path=key_path,
            robot_id=robot_id,
        )
        try:
            verifier.connect(timeout=min(timeout, 5))
            return
        except SshAuthenticationError as error:
            raise SshKeyVerificationError(
                "SSH 密码正确，但主控拒绝刚写入的项目公钥。"
                "请重新授权；若仍失败，请检查远端 .ssh 目录所有权。",
                "key_rejected",
            ) from error
        except SshRobotMismatchError as error:
            raise SshKeyVerificationError(
                "SSH 密码正确且公钥已写入，但复验时机器人连接已切换。"
                "请确认当前机器人后重试。",
                "robot_mismatch",
            ) from error
        except Exception as error:
            last_error = error
            if attempt + 1 < _KEY_VERIFICATION_ATTEMPTS:
                time.sleep(_KEY_VERIFICATION_RETRY_DELAY_SECONDS)
        finally:
            verifier.close()

    raise SshKeyVerificationError(
        "SSH 密码正确且公钥已写入，但无法建立新的复验连接。"
        "请确认电脑仍连接机器人网络后重试。",
        "key_connection",
    ) from last_error
