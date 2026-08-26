import pytest

from ui.main_window import ssh_authorization_error_title


@pytest.mark.parametrize(
    ("error_code", "title"),
    [
        ("authentication", "SSH 密码验证失败"),
        ("key_write", "SSH 公钥写入失败"),
        ("key_rejected", "SSH 公钥被拒绝"),
        ("key_connection", "SSH 密钥复验未完成"),
        ("robot_mismatch", "机器人连接已切换"),
        ("connection", "SSH 连接失败"),
        ("unknown", "SSH 密钥授权失败"),
    ],
)
def test_ssh_authorization_error_title(error_code, title):
    assert ssh_authorization_error_title(error_code) == title