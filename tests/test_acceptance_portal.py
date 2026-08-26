import pytest

from ui.panels.acceptance_test_panel import _evaluate_portal_response


@pytest.mark.parametrize(
    ("status_code", "body", "expected", "page_name"),
    [
        (200, "<title>LimX Robot Manager</title>", True, "Robot Manager"),
        (200, "<title>LimX Studio</title>", True, "LimX Studio"),
        (200, '<a href="/get_robot_info">info</a>', True, "机器人信息 API"),
        (200, "<title>Unrelated Device</title>", False, "页面标识未识别"),
        (503, "<title>LimX Studio</title>", False, "状态码异常"),
    ],
)
def test_evaluate_portal_response(
    status_code,
    body,
    expected,
    page_name,
):
    assert _evaluate_portal_response(status_code, body) == (expected, page_name)