from services.connection_service import ConnectionService
from ui.widgets.status_bar_widget import StatusBarWidget


def test_mcp_not_applicable_is_neutral_and_does_not_block_connection(qtbot):
    service = ConnectionService()
    widget = StatusBarWidget(service)
    qtbot.addWidget(widget)

    service.update_wifi(True)
    service.update_mcp(None)
    service.update_ws(True)
    service.update_ssh(True)

    assert widget._indicators["mcp"].text().strip() == "MCP N/A"
    assert service.all_connected is True


def test_required_connection_failure_still_blocks_connection(qtbot):
    service = ConnectionService()
    widget = StatusBarWidget(service)
    qtbot.addWidget(widget)

    service.update_wifi(True)
    service.update_mcp(None)
    service.update_ws(False)
    service.update_ssh(True)

    assert service.all_connected is False