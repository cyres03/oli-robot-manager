from models.robot_profile import L04_PROFILE, OLI_PROFILE
from ui.widgets.sidebar import Sidebar


def test_sidebar_uses_l04_main_and_companion_nodes(qtbot):
    sidebar = Sidebar()
    qtbot.addWidget(sidebar)

    sidebar.apply_profile(L04_PROFILE)

    assert sidebar._ssh_buttons[0].text() == "  主控 (limx@10.192.1.2)"
    assert sidebar._ssh_buttons[1].text() == "  语音/视觉伴随节点 (guest@10.192.1.4)"
    assert not sidebar._ssh_buttons[0].isHidden()
    assert not sidebar._ssh_buttons[1].isHidden()


def test_sidebar_switches_back_to_oli_topology(qtbot):
    sidebar = Sidebar()
    qtbot.addWidget(sidebar)

    sidebar.apply_profile(L04_PROFILE)
    sidebar.apply_profile(OLI_PROFILE)

    assert sidebar._ssh_buttons[1].text() == "  感知 (guest@10.192.1.3)"


def test_sidebar_hides_ssh_when_identity_is_unresolved(qtbot):
    sidebar = Sidebar()
    qtbot.addWidget(sidebar)

    sidebar.apply_profile(None)

    assert sidebar.ssh_section.isHidden()
    assert all(button.isHidden() for button in sidebar._ssh_buttons)