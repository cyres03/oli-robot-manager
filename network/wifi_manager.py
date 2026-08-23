"""
Cross-platform WiFi management. Supports multi-adapter setups.
"""
import platform
import subprocess
import re
import sys
from typing import Optional


def _no_window():
    """Suppress console window for subprocess calls in frozen Windows apps."""
    if sys.platform == "win32" and getattr(sys, 'frozen', False):
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def _run(args: list[str]) -> str:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
            **_no_window(),
        )
        return result.stdout
    except Exception:
        return ""


class WifiManager:
    @staticmethod
    def _get_pattern() -> re.Pattern:
        from config import ROBOT_CONFIG
        prefixes = [re.escape(prefix) for prefix in ROBOT_CONFIG.wifi_ssid_patterns if prefix]
        return re.compile(r"^(?:" + "|".join(prefixes) + r")", flags=re.IGNORECASE)

    @staticmethod
    def _get_all_interfaces() -> list[dict]:
        """Return connected WiFi interfaces on the current platform."""
        if platform.system() == "Linux":
            stdout = _run([
                "nmcli", "-t", "--escape", "no",
                "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status",
            ])
            ifaces = []
            for line in stdout.splitlines():
                parts = line.split(":", 3)
                if len(parts) != 4 or parts[1] != "wifi" or parts[2] != "connected":
                    continue
                device = parts[0]
                ssid = parts[3]
                signal = 0
                access_points = _run([
                    "nmcli", "-t", "--escape", "no",
                    "-f", "IN-USE,SSID,SIGNAL", "device", "wifi", "list",
                    "ifname", device,
                ])
                for access_point in access_points.splitlines():
                    if not access_point.startswith("*:"):
                        continue
                    active_parts = access_point.split(":", 2)
                    if len(active_parts) == 3:
                        ssid = active_parts[1]
                        signal = int(active_parts[2]) if active_parts[2].isdigit() else 0
                    break
                ifaces.append({
                    "name": device,
                    "ssid": ssid,
                    "description": "",
                    "state": "connected",
                    "signal": signal,
                })
            return ifaces

        if platform.system() != "Windows":
            return []

        # Parse netsh output in both Chinese and English Windows.
        stdout = _run(["netsh", "wlan", "show", "interfaces"])
        # Split by interface blocks: each block starts with a name line
        blocks = re.split(r"\n\s*\n", stdout)
        ifaces = []
        for block in blocks:
            if not block.strip():
                continue
            # Match field:value pairs - fields can be Chinese or English
            name_m = re.search(r"^\s*(?:Name|名称)\s*:\s*(.+)$", block, re.MULTILINE)
            if not name_m:
                continue
            ssid_m = re.search(r"^\s*SSID\s*:\s*(.*)$", block, re.MULTILINE)
            signal_m = re.search(r"^\s*(?:Signal|信号)\s*:\s*(\d+)%", block, re.MULTILINE)
            state_m = re.search(r"^\s*(?:State|状态)\s*:\s*(.+)$", block, re.MULTILINE)
            desc_m = re.search(r"^\s*(?:Description|说明)\s*:\s*(.+)$", block, re.MULTILINE)
            state = state_m.group(1).strip() if state_m else ""
            iface = {
                "name": name_m.group(1).strip(),
                "ssid": ssid_m.group(1).strip() if ssid_m else "",
                "description": desc_m.group(1).strip() if desc_m else "",
                "state": "connected" if state.lower() == "connected" or state == "已连接" else "disconnected",
                "signal": int(signal_m.group(1)) if signal_m else 0,
            }
            ifaces.append(iface)
        return ifaces

    @staticmethod
    def get_current_ssid() -> Optional[str]:
        """Return the robot SSID if any interface is connected to robot WiFi, else the first connected SSID."""
        try:
            return WifiManager._windows_get_ssid()
        except Exception:
            pass
        return None

    @staticmethod
    def is_robot_wifi() -> bool:
        """Check if ANY interface is connected to a robot WiFi network."""
        try:
            pattern = WifiManager._get_pattern()
            for iface in WifiManager._get_all_interfaces():
                ssid = iface.get("ssid", "")
                if ssid and pattern.match(ssid):
                    return True
        except Exception:
            pass
        return False

    @staticmethod
    def get_robot_ssid() -> Optional[str]:
        """Get the robot SSID from any connected interface."""
        try:
            pattern = WifiManager._get_pattern()
            for iface in WifiManager._get_all_interfaces():
                ssid = iface.get("ssid", "")
                if ssid and pattern.match(ssid):
                    return ssid
        except Exception:
            pass
        return None

    @staticmethod
    def connect_to_wifi(ssid: str, password: str) -> bool:
        system = platform.system()
        if system == "Windows":
            return WifiManager._windows_connect(ssid, password)
        elif system == "Linux":
            return WifiManager._linux_connect(ssid, password)
        elif system == "Darwin":
            return WifiManager._macos_connect(ssid, password)
        return False

    @staticmethod
    def _windows_connect(ssid: str, password: str) -> bool:
        import tempfile, os, time

        # Pick best interface: prefer USB/WLAN 2 for robot WiFi
        iface_name = None
        for iface in WifiManager._get_all_interfaces():
            desc = iface.get("description", "")
            if "USB" in desc.upper() or "2" in iface.get("name", ""):
                iface_name = iface.get("name")
                break
        if not iface_name:
            iface_name = "WLAN"

        # Method 1: Create profile if needed
        profile_xml = f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>{ssid}</name>
    <SSIDConfig>
        <SSID><name>{ssid}</name></SSID>
    </SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>auto</connectionMode>
    <MSM>
        <security>
            <authEncryption>
                <authentication>WPA2PSK</authentication>
                <encryption>AES</encryption>
                <useOneX>false</useOneX>
            </authEncryption>
            <sharedKey><keyType>passPhrase</keyType>
            <protected>false</protected>
            <keyMaterial>{password}</keyMaterial></sharedKey>
        </security>
    </MSM>
</WLANProfile>"""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".xml", delete=False, encoding="utf-8"
            ) as f:
                f.write(profile_xml)
                profile_path = f.name
            subprocess.run(
                ["netsh", "wlan", "add", "profile", f"filename={profile_path}"],
                capture_output=True,
                **_no_window(),
            )
            os.unlink(profile_path)
        except Exception:
            pass

        # Method 2: Connect with explicit interface
        subprocess.run(
            ["netsh", "wlan", "connect",
             f"name={ssid}", f"ssid={ssid}", f"interface={iface_name}"],
            capture_output=True,
            **_no_window(),
        )
        time.sleep(3)
        return WifiManager.get_current_ssid() == ssid

    @staticmethod
    def _linux_connect(ssid: str, password: str) -> bool:
        try:
            subprocess.run(
                ["nmcli", "dev", "wifi", "connect", ssid, "password", password],
                check=True, capture_output=True,
            )
            return True
        except subprocess.CalledProcessError:
            return False

    @staticmethod
    def _macos_connect(ssid: str, password: str) -> bool:
        try:
            subprocess.run(
                ["networksetup", "-setairportnetwork", "en0", ssid, password],
                check=True, capture_output=True,
            )
            return True
        except subprocess.CalledProcessError:
            return False

    # ---- WiFi scanning ----

    @staticmethod
    def _get_robot_interface_name() -> Optional[str]:
        """Get the interface name most likely to reach robot WiFi (prefer USB/WLAN 2)."""
        ifaces = WifiManager._get_all_interfaces()
        # First check: which interface is already connected to robot WiFi
        pattern = WifiManager._get_pattern()
        for iface in ifaces:
            ssid = iface.get("ssid", "")
            if ssid and pattern.match(ssid):
                return iface.get("name")
        # Second: prefer USB/WLAN 2 adapters (those are likely the external WiFi dongle)
        for iface in ifaces:
            name = iface.get("name", "")
            desc = iface.get("description", "")
            if "2" in name or "USB" in desc.upper():
                return name
        # Fallback: first connected interface
        for iface in ifaces:
            if iface.get("ssid"):
                return iface.get("name")
        return None

    @staticmethod
    def scan_networks() -> list[dict]:
        """Scan available WiFi networks on the interface that can reach the robot."""
        try:
            if platform.system() == "Windows":
                return WifiManager._windows_scan()
            elif platform.system() == "Linux":
                return WifiManager._linux_scan()
            elif platform.system() == "Darwin":
                return WifiManager._macos_scan()
        except Exception:
            pass
        return []

    @staticmethod
    def scan_robot_networks() -> list[dict]:
        """Scan and filter for robot WiFi networks only."""
        pattern = WifiManager._get_pattern()
        return [n for n in WifiManager.scan_networks() if pattern.match(n.get("ssid", ""))]

    @staticmethod
    def _windows_scan() -> list[dict]:
        """Scan ALL WiFi interfaces and merge results (deduplicate by SSID)."""
        all_networks = {}
        # Scan on all interfaces
        ifaces = WifiManager._get_all_interfaces()
        for iface in ifaces:
            name = iface.get("name", "")
            if not name:
                continue
            stdout = _run(["netsh", "wlan", "show", "networks", f"interface={name}", "mode=Bssid"])
            if not stdout:
                continue
            current_ssid = None
            for line in stdout.splitlines():
                m = re.search(r"SSID \d+ : (.+)", line)
                if m:
                    current_ssid = m.group(1).strip()
                sig = re.search(r"(?:Signal|信号)\s*:\s*(\d+)%", line)
                if sig and current_ssid:
                    # Keep best signal for each SSID
                    signal = int(sig.group(1))
                    if current_ssid not in all_networks or signal > all_networks[current_ssid]["signal"]:
                        all_networks[current_ssid] = {
                            "ssid": current_ssid,
                            "signal": signal,
                            "security": "WPA2",
                        }
        return list(all_networks.values())

    @staticmethod
    def _linux_scan() -> list[dict]:
        stdout = _run([
            "nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY",
            "dev", "wifi", "list", "--rescan", "yes",
        ])
        networks = []
        for line in stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and parts[0].strip():
                ssid = parts[0].strip()
                if not any(n["ssid"] == ssid for n in networks):
                    networks.append({
                        "ssid": ssid,
                        "signal": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0,
                        "security": parts[2].strip() if len(parts) > 2 else "WPA2",
                    })
        return networks

    @staticmethod
    def _macos_scan() -> list[dict]:
        stdout = _run(["/System/Library/PrivateFrameworks/Apple80211.framework/"
                        "Versions/Current/Resources/airport", "-s"])
        networks = []
        for line in stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 3:
                ssid = parts[0]
                if ssid and not any(n["ssid"] == ssid for n in networks):
                    networks.append({"ssid": ssid, "signal": 0, "security": "WPA2"})
        return networks

    # ---- Internal helpers ----

    @staticmethod
    def _windows_get_ssid() -> Optional[str]:
        """Return robot SSID from any interface, or first connected SSID."""
        ifaces = WifiManager._get_all_interfaces()
        pattern = WifiManager._get_pattern()
        # Prefer robot SSID
        for iface in ifaces:
            ssid = iface.get("ssid", "")
            if ssid and pattern.match(ssid):
                return ssid
        # Fallback: first connected
        for iface in ifaces:
            ssid = iface.get("ssid")
            if ssid:
                return ssid
        return None
