"""Native SSH terminal launcher for interactive sessions."""
import platform
import subprocess


def open_native_ssh_terminal(host: str, username: str):
    """Open a native OS terminal with an SSH session."""
    ssh_cmd = f"ssh {username}@{host}"
    system = platform.system()

    if system == "Windows":
        try:
            subprocess.Popen(
                ["wt", "new-tab", "--title", f"SSH {username}@{host}",
                 "cmd", "/k", ssh_cmd],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        except FileNotFoundError:
            subprocess.Popen(
                ["cmd", "/c", "start", ssh_cmd],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
    elif system == "Linux":
        for term in ["gnome-terminal", "x-terminal-emulator", "xterm", "konsole"]:
            try:
                subprocess.Popen([term, "-e", ssh_cmd])
                break
            except FileNotFoundError:
                continue
    elif system == "Darwin":
        subprocess.Popen([
            "osascript", "-e",
            f'tell app "Terminal" to do script "{ssh_cmd}"',
        ])
