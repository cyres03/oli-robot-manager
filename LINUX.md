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