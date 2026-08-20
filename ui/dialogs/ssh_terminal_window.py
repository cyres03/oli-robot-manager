"""Native SSH terminal launcher for interactive sessions."""
import os
import platform
import shlex
import subprocess

from network.ssh_client import (
    DEFAULT_ROBOT_KNOWN_HOSTS_PATH,
    DEFAULT_SSH_KEY_PATH,
    robot_host_key_alias,
)


def open_native_ssh_terminal(host: str, username: str, robot_id: str = ""):
    """Open a native OS terminal with an SSH session."""
    ssh_args = ["ssh"]
    if os.path.isfile(DEFAULT_SSH_KEY_PATH):
        ssh_args.extend(["-i", DEFAULT_SSH_KEY_PATH, "-o", "IdentitiesOnly=yes"])
    if robot_id:
        ssh_args.extend([
            "-o", f"HostKeyAlias={robot_host_key_alias(robot_id, host, username)}",
            "-o", f"UserKnownHostsFile={DEFAULT_ROBOT_KNOWN_HOSTS_PATH}",
            "-o", "StrictHostKeyChecking=yes",
        ])
    ssh_args.append(f"{username}@{host}")
    ssh_cmd = shlex.join(ssh_args)
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
                separator = "--" if term == "gnome-terminal" else "-e"
                subprocess.Popen([term, separator, *ssh_args])
                break
            except FileNotFoundError:
                continue
    elif system == "Darwin":
        subprocess.Popen([
            "osascript", "-e",
            f'tell app "Terminal" to do script "{ssh_cmd}"',
        ])
