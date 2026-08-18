"""Secure per-robot credentials backed by the operating system keyring."""
try:
    import keyring
    from keyring.errors import KeyringError, PasswordDeleteError
except ImportError:
    keyring = None

    class KeyringError(Exception):
        pass

    class PasswordDeleteError(KeyringError):
        pass


SERVICE_NAME = "Oli Robot Manager"


def _account(robot_id: str, host: str, username: str) -> str:
    return f"robot={robot_id}|host={host}|user={username}"


def is_available() -> bool:
    if keyring is None:
        return False
    try:
        backend = keyring.get_keyring()
        module_name = backend.__class__.__module__
        return (
            not module_name.endswith((".fail", ".null"))
            and backend.priority > 0
        )
    except Exception:
        return False


def backend_name() -> str:
    if not is_available():
        return "不可用"
    backend = keyring.get_keyring()
    module_name = backend.__class__.__module__
    if module_name.endswith(".Windows"):
        return "Windows Credential Manager"
    if module_name.endswith(".SecretService"):
        return "Secret Service"
    if module_name.endswith(".macOS"):
        return "macOS Keychain"
    return backend.__class__.__name__


def get_password(robot_id: str, host: str, username: str) -> str | None:
    if not robot_id or not is_available():
        return None
    try:
        return keyring.get_password(SERVICE_NAME, _account(robot_id, host, username))
    except KeyringError:
        return None


def set_password(robot_id: str, host: str, username: str, password: str) -> bool:
    if not robot_id or not password or not is_available():
        return False
    try:
        keyring.set_password(
            SERVICE_NAME,
            _account(robot_id, host, username),
            password,
        )
        return True
    except KeyringError:
        return False


def delete_password(robot_id: str, host: str, username: str) -> bool:
    if not robot_id or not is_available():
        return False
    try:
        keyring.delete_password(
            SERVICE_NAME,
            _account(robot_id, host, username),
        )
        return True
    except PasswordDeleteError:
        return False
    except KeyringError:
        return False


def clear_robot_passwords(robot_id: str, accounts: list[tuple[str, str]]) -> int:
    return sum(
        delete_password(robot_id, host, username)
        for host, username in accounts
    )