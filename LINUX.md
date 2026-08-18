# Linux development and packaging

This project can run on Linux from source. A Linux executable must be built on Linux
or WSL; Windows PyInstaller builds cannot produce a real Linux binary.

## Ubuntu/Debian prerequisites

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip network-manager openssh-client \
  libxcb-cursor0 libxcb-xinerama0 libxkbcommon-x11-0
```

## Run from source

```bash
chmod +x scripts/linux_setup.sh scripts/linux_run.sh scripts/linux_build_portable.sh
./scripts/linux_setup.sh
./scripts/linux_run.sh
```

## Build a portable Linux package

Run this on Linux or WSL with GUI/runtime dependencies available:

```bash
./scripts/linux_build_portable.sh
```

Output:

```text
dist/OliRobotManager/OliRobotManager
```

You can zip the whole `dist/OliRobotManager` directory and move it to another
Linux machine with compatible system libraries.

## Notes

- Robot WiFi scan/connect on Linux uses `nmcli`, so NetworkManager must be installed.
- Native SSH launcher tries common Linux terminals: `gnome-terminal`,
  `x-terminal-emulator`, `xterm`, then `konsole`.
- If running over SSH without a desktop session, PyQt6 needs an X11/Wayland display.
- Every robot has its own SSH authorization even though the controller addresses are
  always `10.192.1.2` and `10.192.1.3`. On the first SSH-backed operation for each
  account, the app asks for that account's password once and installs the operator
  public key. The password is used only for that operation unless secure credential
  storage is explicitly enabled. Later SSH operations use
  `~/.ssh/oli_robot_manager_ed25519` without prompting.
- When "remember in the system credential manager" is enabled, sudo credentials are
  stored per robot/account in the desktop Secret Service, never in the repository or
  `config.local.json`. They can be removed from the app's Security Credentials settings.