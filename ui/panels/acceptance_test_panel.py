"""Acceptance testing workbench for WiFi/SSH-based after-sales checks."""
import os
import re
import shlex
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from statistics import median
from urllib.parse import urljoin

import httpx
from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from config import ROBOT_CONFIG
from models.robot_profile import OLI_PROFILE, RobotProfile
from network.wifi_manager import WifiManager
from services import credential_store
from ui.panels.log_analyzer_panel import LogAnalyzerPanel
from ui.panels.power_cycle_panel import PowerCyclePanel
from workers.ssh_worker import SshWorker


BEIJING_TIMEZONE = timezone(timedelta(hours=8))
TIME_TOLERANCE_SECONDS = 60
TIME_SOURCE_MAX_DIFFERENCE_SECONDS = 15
TIME_SOURCE_URLS = (
    "https://www.baidu.com/",
    "https://www.microsoft.com/",
)
TIME_CHECK_COMMAND = (
    "date '+TIME=%Y-%m-%d %H:%M:%S %z'; "
    "printf 'ZONE='; timedatectl show -p Timezone --value"
)
PORTAL_PAGE_MARKERS = (
    ("LimX Robot Manager", "Robot Manager"),
    ("LimX Studio", "LimX Studio"),
    ("get_robot_info", "机器人信息 API"),
)


def _evaluate_portal_response(status_code: int, body: str) -> tuple[bool, str]:
    if status_code != 200:
        return False, "状态码异常"
    normalized_body = body.casefold()
    for marker, page_name in PORTAL_PAGE_MARKERS:
        if marker.casefold() in normalized_body:
            return True, page_name
    return False, "页面标识未识别"


def _fetch_network_beijing_time() -> tuple[datetime, float]:
    samples: list[tuple[datetime, float]] = []
    errors = []
    for source_url in TIME_SOURCE_URLS:
        try:
            response = httpx.get(
                f"{source_url}?oli_time_check={uuid.uuid4().hex}",
                timeout=6.0,
                follow_redirects=True,
                headers={"Cache-Control": "no-cache"},
            )
            response.raise_for_status()
            date_header = response.headers.get("date")
            if not date_header:
                raise ValueError("响应缺少 Date 头")
            source_time = parsedate_to_datetime(date_header)
            if source_time.tzinfo is None:
                source_time = source_time.replace(tzinfo=timezone.utc)
            samples.append((source_time.astimezone(timezone.utc), time.monotonic()))
        except Exception as error:
            errors.append(f"{source_url}: {error}")

    if not samples:
        raise RuntimeError("；".join(errors) or "无可用网络时间源")

    reference_tick = max(received_tick for _, received_tick in samples)
    adjusted_samples = [
        source_time + timedelta(seconds=reference_tick - received_tick)
        for source_time, received_tick in samples
    ]
    source_difference = (max(adjusted_samples) - min(adjusted_samples)).total_seconds()
    if source_difference > TIME_SOURCE_MAX_DIFFERENCE_SECONDS:
        raise RuntimeError(f"网络时间源相差 {source_difference:.0f} 秒")

    reference_timestamp = median(sample.timestamp() for sample in adjusted_samples)
    reference_time = datetime.fromtimestamp(reference_timestamp, timezone.utc).astimezone(BEIJING_TIMEZONE)
    return reference_time, reference_tick


def _current_reference_time(reference: tuple[datetime, float]) -> datetime:
    reference_time, reference_tick = reference
    elapsed = max(0.0, time.monotonic() - reference_tick)
    return reference_time + timedelta(seconds=elapsed)


def _evaluate_time_output(
    output: str,
    device_name: str,
    current_beijing_time: datetime,
) -> tuple[bool, str]:
    time_match = re.search(r"^TIME=(.+)$", output, flags=re.MULTILINE)
    zone_match = re.search(r"^ZONE=(.*)$", output, flags=re.MULTILINE)
    if not time_match or not zone_match:
        return False, f"无法读取{device_name}时间或时区"

    try:
        robot_time = datetime.strptime(time_match.group(1).strip(), "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return False, f"{device_name}时间格式异常"

    beijing_now = current_beijing_time
    if beijing_now.tzinfo is None:
        beijing_now = beijing_now.replace(tzinfo=BEIJING_TIMEZONE)
    else:
        beijing_now = beijing_now.astimezone(BEIJING_TIMEZONE)

    timezone_name = zone_match.group(1).strip()
    difference = abs((robot_time - beijing_now).total_seconds())
    passed = timezone_name == "Asia/Shanghai" and difference < TIME_TOLERANCE_SECONDS
    summary = (
        f"{device_name} {robot_time.astimezone(BEIJING_TIMEZONE):%Y-%m-%d %H:%M:%S}，"
        f"北京时间 {beijing_now:%Y-%m-%d %H:%M:%S}，"
        f"偏差 {difference:.0f} 秒，时区 {timezone_name or '未知'}"
    )
    return passed, summary


@dataclass(frozen=True)
class AcceptanceCheck:
    key: str
    category: str
    name: str
    tool: str
    kind: str
    target: str = ""
    command: str = ""
    url: str = ""


def build_acceptance_checks(profile: RobotProfile) -> list[AcceptanceCheck]:
    main = profile.main_node
    companion = profile.companion_nodes[0] if profile.companion_nodes else None
    portal = profile.service("portal")
    logs = profile.service("logs")
    mcp = profile.service("mcp")
    checks = {
        "wifi": AcceptanceCheck("wifi", "网络", "机器人 WiFi 识别", "本机 WiFi", "local"),
        "portal": AcceptanceCheck("portal", "网络", "机器人信息页 8080", "HTTP", "http", url=portal.url or ""),
        "logs": AcceptanceCheck("logs", "网络", "日志页面 8090", "HTTP", "http", url=logs.url or ""),
        "mcp": AcceptanceCheck(
            "mcp", "网络", "MCP 服务 18080", "HTTP" if mcp.supported else "该型号不支持",
            "http" if mcp.supported else "na", url=mcp.url or "",
        ),
        "main_ssh": AcceptanceCheck(
            "main_ssh", "SSH", f"{main.label} SSH 登录", f"{main.username}@{main.host}",
            "ssh", target="main", command="hostname; uname -a",
        ),
        "main_time": AcceptanceCheck(
            "main_time", main.label, f"{main.label}系统时间", f"{main.username}@{main.host}",
            "ssh", target="main", command=TIME_CHECK_COMMAND,
        ),
        "main_disk": AcceptanceCheck(
            "main_disk", main.label, f"{main.label}磁盘空间", "SSH", "ssh",
            target="main", command="df -h /",
        ),
        "main_process": AcceptanceCheck(
            "main_process", main.label, f"{main.label}机器人进程", "SSH", "ssh",
            target="main", command="ps -eo comm,args | grep -E 'limx|robot|mros' | grep -v grep | head -30",
        ),
        "imu": AcceptanceCheck(
            "imu", main.label, "IMU 频率", "SSH", "ssh", target="main",
            command="bash -c 'source /opt/limx/install/setup.bash && export MROS_IP_LIST=10.192.1.x && timeout --signal=KILL 8s /opt/limx/install/bin/mrostopic hz /ImuData' 2>&1",
        ),
    }
    if companion:
        camera_command = (
            "ps -eo comm,args | grep -Ei 'mroswebvideo|camera|cosa|opus|GestureMrosNode' | grep -v grep | head -40"
            if profile.key == "hu_l04_01"
            else "lsusb -t 2>&1; echo '---'; lsusb 2>&1"
        )
        checks.update({
            "companion_ssh": AcceptanceCheck(
                "companion_ssh", "SSH", f"{companion.label} SSH 登录",
                f"{companion.username}@{companion.host}", "ssh", target="companion",
                command="hostname; uname -a",
            ),
            "companion_time": AcceptanceCheck(
                "companion_time", companion.label, f"{companion.label}系统时间",
                f"{companion.username}@{companion.host}", "ssh", target="companion",
                command=TIME_CHECK_COMMAND,
            ),
            "cpu": AcceptanceCheck(
                "cpu", companion.label, f"{companion.label} CPU 核心数", "SSH", "ssh",
                target="companion", command="nproc",
            ),
            "camera": AcceptanceCheck(
                "camera", companion.label, "相机/视觉服务", "SSH", "ssh",
                target="companion", command=camera_command,
            ),
        })
    return [checks[key] for key in profile.acceptance_check_keys if key in checks]


class BeijingTimeWorker(QThread):
    time_ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def run(self):
        try:
            self.time_ready.emit(_fetch_network_beijing_time())
        except Exception as error:
            self.failed.emit(str(error))


class HttpCheckWorker(QThread):
    finished = pyqtSignal(int, str)
    failed = pyqtSignal(str)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        try:
            response = httpx.get(self.url, timeout=4.0, follow_redirects=True)
            self.finished.emit(response.status_code, response.text[:800])
        except Exception as error:
            self.failed.emit(str(error))


class RobotInfoWorker(QThread):
    finished = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, portal_url: str, parent=None):
        super().__init__(parent)
        self.portal_url = portal_url.rstrip("/")

    def run(self):
        try:
            response = httpx.get(f"{self.portal_url}/get_robot_info", timeout=6.0)
            response.raise_for_status()
            self.finished.emit(response.json())
        except Exception:
            try:
                response = httpx.get(f"{self.portal_url}/get_local_version", timeout=6.0)
                response.raise_for_status()
                self.finished.emit(response.json())
            except Exception as error:
                self.failed.emit(str(error))


class LogListWorker(QThread):
    finished = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, logs_url: str, parent=None):
        super().__init__(parent)
        self.logs_url = logs_url.rstrip("/") + "/"

    def run(self):
        try:
            response = httpx.get(f"{self.logs_url}log/", timeout=8.0, follow_redirects=True)
            response.raise_for_status()
            names = sorted(set(re.findall(r'href="([^"]+\.log(?:\.active)?)"', response.text)))
            self.finished.emit(names)
        except Exception as error:
            self.failed.emit(str(error))


class LogDownloadWorker(QThread):
    finished = pyqtSignal(str, str, str)
    failed = pyqtSignal(str)

    def __init__(self, logs_url: str, log_name: str, parent=None):
        super().__init__(parent)
        self.logs_url = logs_url.rstrip("/") + "/"
        self.log_name = log_name

    def run(self):
        try:
            url = urljoin(f"{self.logs_url}log/", self.log_name)
            response = httpx.get(url, timeout=30.0, follow_redirects=True)
            response.raise_for_status()
            content = response.text
            save_dir = os.path.join(os.path.expanduser("~"), "AppData", "Local", "OliRobotManager", "logs")
            os.makedirs(save_dir, exist_ok=True)
            save_path = os.path.join(save_dir, self.log_name)
            with open(save_path, "w", encoding="utf-8", errors="ignore") as file_handle:
                file_handle.write(content)
            self.finished.emit(self.log_name, save_path, content)
        except Exception as error:
            self.failed.emit(str(error))


class AcceptanceTestPanel(QWidget):
    ssh_connection_changed = pyqtSignal(bool)
    ssh_authorization_required = pyqtSignal(str, str, str)
    sudo_password_required = pyqtSignal(str, str, str)
    log_message = pyqtSignal(str, str)

    CHECKS = build_acceptance_checks(OLI_PROFILE)

    def __init__(self, power_cycle_service=None, parent=None, profile: RobotProfile | None = None):
        super().__init__(parent)
        self._profile = profile or ROBOT_CONFIG.active_profile or OLI_PROFILE
        self.CHECKS = build_acceptance_checks(self._profile)
        self._power_cycle_service = power_cycle_service
        self._workers: list[QThread] = []
        self._pending: list[int] = []
        self._running_index: int | None = None
        self._profile_generation = 0
        self._ssh_retry: tuple[int, AcceptanceCheck, str] | None = None
        self._pending_time_fix: tuple[int, AcceptanceCheck, str, str] | None = None
        self._build_ui()
        self._populate_checks()

    def apply_profile(self, profile: RobotProfile | None):
        self._profile_generation += 1
        self._ssh_retry = None
        self._pending_time_fix = None
        self._profile = profile or OLI_PROFILE
        self.CHECKS = build_acceptance_checks(self._profile)
        self._pending.clear()
        self._running_index = None
        self._populate_checks()
        self.detail_view.clear()
        self.summary_label.setText(f"{self._profile.display_name} · 就绪")

    def _build_ui(self):
        self.setStyleSheet(
            "QWidget#acceptancePanel { background: #F8F9FA; }"
            "QLabel#title { font-size: 20px; font-weight: 700; color: #1D2129; background: transparent; }"
            "QLabel#desc { color: #86909C; font-size: 13px; background: transparent; }"
            "QPushButton { background: #FFFFFF; color: #1D2129; border: 1px solid #E5E6EB; border-radius: 6px; padding: 8px 14px; }"
            "QPushButton:hover { background: #F2F3F5; border-color: #C9CDD4; }"
            "QPushButton#primaryBtn { background: #6C5CE7; color: #FFFFFF; border: none; font-weight: 700; }"
            "QPushButton#primaryBtn:hover { background: #5A4BD1; }"
            "QTableWidget { background: #FFFFFF; border: 1px solid #E5E6EB; border-radius: 8px; gridline-color: #E5E6EB; }"
            "QHeaderView::section { background: #F2F3F5; color: #4E5969; border: none; padding: 7px; font-weight: 700; }"
            "QTextEdit { background: #F7F8FA; border: 1px solid #E5E6EB; border-radius: 8px; font-family: Consolas, 'Courier New', monospace; font-size: 12px; }"
        )
        self.setObjectName("acceptancePanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        title = QLabel("售后验收测试工作台")
        title.setObjectName("title")
        layout.addWidget(title)
        desc = QLabel("集中执行可通过 WiFi / SSH 自动判断的验收项，并内嵌机器人日志分析。")
        desc.setObjectName("desc")
        layout.addWidget(desc)

        top_tools = QGridLayout()
        top_tools.setHorizontalSpacing(8)
        top_tools.setVerticalSpacing(8)

        self.version_labels = {}
        for index, (key, label) in enumerate([
            ("software_version", "主控软件版本"),
            ("pms_version", "分电板版本"),
            ("ecm_version", "主站板版本"),
            ("motor_version", "驱动器版本"),
        ]):
            label_widget = QLabel(label)
            label_widget.setStyleSheet("color: #86909C; background: transparent;")
            value_widget = QLabel("-")
            value_widget.setStyleSheet("color: #1D2129; font-weight: 700; background: #FFFFFF; border: 1px solid #E5E6EB; border-radius: 6px; padding: 7px 10px;")
            self.version_labels[key] = value_widget
            top_tools.addWidget(label_widget, 0, index)
            top_tools.addWidget(value_widget, 1, index)

        refresh_versions_btn = QPushButton("刷新 8080 版本")
        refresh_versions_btn.clicked.connect(self.refresh_robot_versions)
        top_tools.addWidget(refresh_versions_btn, 1, 4)

        self.log_combo = QComboBox()
        self.log_combo.setMinimumWidth(230)
        self.log_combo.setStyleSheet("background: #FFFFFF; border: 1px solid #E5E6EB; border-radius: 6px; padding: 6px 10px;")
        top_tools.addWidget(QLabel("8090 日志"), 2, 0)
        top_tools.addWidget(self.log_combo, 3, 0, 1, 2)
        refresh_logs_btn = QPushButton("刷新日志列表")
        refresh_logs_btn.clicked.connect(self.refresh_log_list)
        top_tools.addWidget(refresh_logs_btn, 3, 2)
        download_log_btn = QPushButton("下载并分析")
        download_log_btn.setObjectName("primaryBtn")
        download_log_btn.clicked.connect(self.download_selected_log)
        top_tools.addWidget(download_log_btn, 3, 3)
        self.log_status = QLabel("未加载日志列表")
        self.log_status.setStyleSheet("color: #86909C; background: transparent;")
        top_tools.addWidget(self.log_status, 3, 4)
        layout.addLayout(top_tools)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self.auto_tab = QWidget()
        auto_layout = QVBoxLayout(self.auto_tab)
        auto_layout.setContentsMargins(0, 10, 0, 0)
        auto_layout.setSpacing(8)

        button_bar = QHBoxLayout()
        self.run_all_btn = QPushButton("运行全部自动检查")
        self.run_all_btn.setObjectName("primaryBtn")
        self.run_all_btn.clicked.connect(self.run_all_checks)
        button_bar.addWidget(self.run_all_btn)

        self.run_selected_btn = QPushButton("运行选中项")
        self.run_selected_btn.clicked.connect(self.run_selected_check)
        button_bar.addWidget(self.run_selected_btn)
        button_bar.addStretch()

        self.summary_label = QLabel("就绪")
        self.summary_label.setStyleSheet("color: #4E5969; background: transparent;")
        button_bar.addWidget(self.summary_label)
        auto_layout.addLayout(button_bar)

        self.check_table = QTableWidget(0, 6)
        self.check_table.setHorizontalHeaderLabels(["模块", "测试项", "工具/目标", "状态", "摘要", "时间"])
        self.check_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.check_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.check_table.verticalHeader().setVisible(False)
        self.check_table.horizontalHeader().setStretchLastSection(True)
        self.check_table.setColumnWidth(0, 72)
        self.check_table.setColumnWidth(1, 170)
        self.check_table.setColumnWidth(2, 150)
        self.check_table.setColumnWidth(3, 76)
        self.check_table.setColumnWidth(4, 430)
        auto_layout.addWidget(self.check_table, 1)

        self.detail_view = QTextEdit()
        self.detail_view.setReadOnly(True)
        self.detail_view.setMaximumHeight(130)
        auto_layout.addWidget(self.detail_view)

        self.tabs.addTab(self.auto_tab, "自动验收")
        self.log_analyzer = LogAnalyzerPanel()
        self.tabs.addTab(self.log_analyzer, "日志分析")
        if self._power_cycle_service is not None:
            self.power_cycle_tab = PowerCyclePanel(self._power_cycle_service)
            self.tabs.addTab(self.power_cycle_tab, "断电恢复")

    def _populate_checks(self):
        self.check_table.setRowCount(len(self.CHECKS))
        for row_index, check in enumerate(self.CHECKS):
            values = [check.category, check.name, check.tool, "待执行", "", ""]
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column_index == 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.check_table.setItem(row_index, column_index, item)

    def run_all_checks(self):
        self._pending = list(range(len(self.CHECKS)))
        self._set_running(True)
        self.detail_view.clear()
        self._append_detail("开始运行自动验收检查...")
        self._run_next_check()

    def refresh_robot_versions(self):
        self._append_detail("正在从 8080 manager 读取版本信息...")
        generation = self._profile_generation
        worker = RobotInfoWorker(ROBOT_CONFIG.portal_url, self)
        worker.finished.connect(
            lambda info, current=generation:
            self._run_if_current(current, self._on_robot_info_loaded, info)
        )
        worker.failed.connect(
            lambda error, current=generation:
            self._run_if_current(
                current, self._append_detail, f"读取 8080 版本失败: {error}"
            )
        )
        self._workers.append(worker)
        worker.start()

    def _on_robot_info_loaded(self, info: dict):
        fields = {
            "software_version": "software_version",
            "pms_version": "pms_version",
            "ecm_version": "ecm_version",
            "motor_version": "motor_version",
        }
        for label_key, info_key in fields.items():
            value = str(info.get(info_key, "-")).strip() or "-"
            if label_key == "motor_version":
                value = self._summarize_motor_versions(value)
            self.version_labels[label_key].setText(value)
        self._append_detail("已从 8080 导入版本信息。")
        self.log_message.emit("[验收] 已导入 8080 版本信息", "pass")

    def _summarize_motor_versions(self, raw_value: str) -> str:
        pairs = re.findall(r"(\d+)\s*[:：]\s*([0-9A-Za-z._-]+)", raw_value)
        if not pairs:
            return raw_value[:97] + "..." if len(raw_value) > 100 else raw_value

        versions: dict[str, list[str]] = {}
        for motor_id, version in pairs:
            versions.setdefault(version, []).append(motor_id)

        if len(versions) == 1:
            version, motor_ids = next(iter(versions.items()))
            return f"全部 {version}（共 {len(motor_ids)} 个驱动器）"

        majority_version, majority_motors = max(versions.items(), key=lambda item: len(item[1]))
        different = []
        for version, motor_ids in versions.items():
            if version != majority_version:
                different.append(f"{','.join(motor_ids)}: {version}")
        return f"多数 {majority_version}（{len(majority_motors)} 个）；差异 {'; '.join(different)}"

    def refresh_log_list(self):
        self.log_status.setText("正在读取 8090 日志列表...")
        generation = self._profile_generation
        worker = LogListWorker(ROBOT_CONFIG.logs_url, self)
        worker.finished.connect(
            lambda names, current=generation:
            self._run_if_current(current, self._on_log_list_loaded, names)
        )
        worker.failed.connect(
            lambda error, current=generation:
            self._run_if_current(
                current, self.log_status.setText, f"读取失败: {error}"
            )
        )
        self._workers.append(worker)
        worker.start()

    def _on_log_list_loaded(self, log_names: list[str]):
        self.log_combo.clear()
        for log_name in log_names:
            self.log_combo.addItem(log_name)
        if log_names:
            self.log_combo.setCurrentIndex(len(log_names) - 1)
        self.log_status.setText(f"已加载 {len(log_names)} 个日志")
        self.log_message.emit(f"[验收] 8090 日志列表: {len(log_names)} 个", "info")

    def download_selected_log(self):
        log_name = self.log_combo.currentText().strip()
        if not log_name:
            self.log_status.setText("请先刷新并选择日志")
            return
        self.log_status.setText(f"正在下载 {log_name}...")
        generation = self._profile_generation
        worker = LogDownloadWorker(ROBOT_CONFIG.logs_url, log_name, self)
        worker.finished.connect(
            lambda name, path, content, current=generation:
            self._run_if_current(
                current, self._on_log_downloaded, name, path, content
            )
        )
        worker.failed.connect(
            lambda error, current=generation:
            self._run_if_current(
                current, self.log_status.setText, f"下载失败: {error}"
            )
        )
        self._workers.append(worker)
        worker.start()

    def _on_log_downloaded(self, log_name: str, save_path: str, content: str):
        self.log_status.setText(f"已下载: {save_path}")
        self.log_analyzer.analyze_text(content, save_path)
        self.tabs.setCurrentWidget(self.log_analyzer)
        self.log_message.emit(f"[日志] 已下载并分析 {log_name}", "pass")

    def run_selected_check(self):
        selected = self.check_table.currentRow()
        if selected < 0:
            return
        self._pending = [selected]
        self._set_running(True)
        self.detail_view.clear()
        self._run_next_check()

    def _run_next_check(self):
        if not self._pending:
            self._running_index = None
            self._set_running(False)
            self._update_summary()
            self.log_message.emit("[验收] 自动检查完成", "pass")
            return

        row_index = self._pending.pop(0)
        self._running_index = row_index
        check = self.CHECKS[row_index]
        self._set_row(row_index, "执行中", "正在检查...", "")
        self.log_message.emit(f"[验收] {check.name}", "command")

        if check.kind == "local":
            self._run_local_check(row_index, check)
        elif check.kind == "http":
            self._run_http_check(row_index, check)
        elif check.kind == "ssh":
            self._run_ssh_check(row_index, check)
        elif check.kind == "na":
            self._finish_na(row_index, "当前型号不提供此服务")

    def _run_local_check(self, row_index: int, check: AcceptanceCheck):
        if check.key == "wifi":
            ssid = WifiManager.get_robot_ssid() or WifiManager.get_current_ssid() or "未连接"
            passed = WifiManager.is_robot_wifi()
            summary = f"当前 WiFi: {ssid}"
            self._finish_check(row_index, passed, summary, summary)
            return
        self._finish_check(row_index, False, "未知本地检查", "")

    def _run_http_check(self, row_index: int, check: AcceptanceCheck):
        generation = self._profile_generation
        worker = HttpCheckWorker(check.url, self)
        worker.finished.connect(
            lambda status_code, body, current_row=row_index, current_check=check, current=generation:
            self._run_if_current(
                current, self._on_http_done,
                current_row, current_check, status_code, body,
            )
        )
        worker.failed.connect(
            lambda error, current_row=row_index, current=generation:
            self._run_if_current(
                current, self._finish_check, current_row, False, error, error,
            )
        )
        self._workers.append(worker)
        worker.start()

    def _on_http_done(self, row_index: int, check: AcceptanceCheck, status_code: int, body: str):
        passed = 200 <= status_code < 500
        summary = f"HTTP {status_code}"
        if check.key == "portal":
            passed, portal_page = _evaluate_portal_response(status_code, body)
            summary = f"HTTP {status_code} · {portal_page}"
        detail = body.strip()[:600] or summary
        self._finish_check(row_index, passed, summary, detail)

    def _run_ssh_check(
        self,
        row_index: int,
        check: AcceptanceCheck,
        robot_id: str = "",
    ):
        generation = self._profile_generation
        worker = self._create_ssh_worker(check.target, robot_id)
        worker.set_command(check.command)
        worker.command_finished.connect(
            lambda exit_code, current_row=row_index, current_check=check, current_worker=worker, current=generation:
            self._run_if_current(
                current, self._on_ssh_done,
                current_row, current_check, exit_code, current_worker.collected_output,
            )
        )
        worker.authentication_required.connect(
            lambda host, username, robot_id, current_row=row_index, current_check=check, current=generation:
            self._run_if_current(
                current, self._on_ssh_authentication_required,
                current_row, current_check, host, username, robot_id,
            )
        )
        worker.error_occurred.connect(
            lambda error, current_row=row_index, current=generation:
            self._run_if_current(current, self._on_ssh_error, current_row, error)
        )
        self._workers.append(worker)
        worker.start()

    def _on_ssh_authentication_required(
        self,
        row_index: int,
        check: AcceptanceCheck,
        host: str,
        username: str,
        robot_id: str,
    ):
        self._ssh_retry = (row_index, check, robot_id)
        self._set_row(row_index, "等待授权", "需要输入一次 SSH 密码", "")
        self.ssh_connection_changed.emit(False)
        self.ssh_authorization_required.emit(host, username, robot_id)

    def finish_ssh_authorization(self, success: bool, detail: str):
        retry = self._ssh_retry
        self._ssh_retry = None
        if not retry:
            return
        row_index, check, robot_id = retry
        if success:
            if ROBOT_CONFIG.ws_accid != robot_id:
                detail = (
                    f"机器人已从 {robot_id} 切换为 {ROBOT_CONFIG.ws_accid}，"
                    "原检查已取消"
                )
                self._finish_check(row_index, False, detail, detail)
                return
            self._set_row(row_index, "执行中", "密钥已授权，正在重试...", "")
            self._run_ssh_check(row_index, check, robot_id)
            return
        self._finish_check(row_index, False, detail, detail)

    def _create_ssh_worker(self, target: str, robot_id: str = "") -> SshWorker:
        if target == "main":
            return SshWorker(
                ROBOT_CONFIG.main_control_ip,
                ROBOT_CONFIG.main_control_user,
                list(ROBOT_CONFIG.main_control_passwords),
                self,
                robot_id=robot_id,
            )
        return SshWorker(
            ROBOT_CONFIG.perception_ip,
            ROBOT_CONFIG.perception_user,
            [ROBOT_CONFIG.perception_password],
            self,
            robot_id=robot_id,
        )

    def _on_ssh_done(self, row_index: int, check: AcceptanceCheck, exit_code: int, output: str):
        self.ssh_connection_changed.emit(True)
        if check.key in {"main_time", "companion_time"}:
            self._request_beijing_time(row_index, check, output, verification=False)
            return
        passed, summary = self._evaluate_ssh_output(check, output)
        self._finish_check(row_index, passed, summary, output.strip()[:1200])

    def _on_ssh_error(self, row_index: int, error: str):
        self.ssh_connection_changed.emit(False)
        self._finish_check(row_index, False, error, error)

    def _request_beijing_time(
        self,
        row_index: int,
        check: AcceptanceCheck,
        output: str,
        verification: bool,
    ):
        generation = self._profile_generation
        self._set_row(row_index, "执行中", "正在获取可信网络北京时间...", "")
        worker = BeijingTimeWorker(self)
        if verification:
            worker.time_ready.connect(
                lambda reference, current_row=row_index, current_check=check, current_output=output, current=generation:
                self._run_if_current(
                    current, self._on_time_verified,
                    current_row, current_check, current_output, reference,
                )
            )
            failure_summary = "无法获取可信网络北京时间，校时结果无法复验"
        else:
            worker.time_ready.connect(
                lambda reference, current_row=row_index, current_check=check, current_output=output, current=generation:
                self._run_if_current(
                    current, self._on_time_checked,
                    current_row, current_check, current_output, reference,
                )
            )
            failure_summary = "无法获取可信网络北京时间，未执行自动校时"
        worker.failed.connect(
            lambda error, current_row=row_index, summary=failure_summary, current=generation:
            self._run_if_current(
                current, self._finish_check,
                current_row, False, summary, error,
            )
        )
        self._workers.append(worker)
        worker.start()

    def _on_time_checked(
        self,
        row_index: int,
        check: AcceptanceCheck,
        output: str,
        reference: tuple[datetime, float],
    ):
        beijing_now = _current_reference_time(reference)
        passed, summary = _evaluate_time_output(output, check.category, beijing_now)
        if passed:
            self._finish_check(row_index, True, summary, output.strip()[:1200])
            return
        if not self._profile.allow_time_repair:
            self._finish_check(
                row_index,
                False,
                f"{summary}；当前型号仅检查，不自动校时",
                output.strip()[:1200],
            )
            return

        self._set_row(row_index, "执行中", f"{summary}，正在自动校时...", "")
        self._append_detail(f"[校时前] {check.name}\n{output.strip()}\n")
        beijing_time_text = _current_reference_time(reference).strftime("%Y-%m-%d %H:%M:%S")
        robot_id = ROBOT_CONFIG.ws_accid
        if check.target == "companion":
            self._pending_time_fix = (
                row_index, check, beijing_time_text, robot_id
            )
            self._set_row(
                row_index,
                "等待密码",
                "感知机校时需要一次 sudo 密码",
                "",
            )
            self.sudo_password_required.emit(
                ROBOT_CONFIG.perception_ip,
                ROBOT_CONFIG.perception_user,
                robot_id,
            )
            return

        self._run_time_fix(
            row_index,
            check,
            beijing_time_text,
            robot_id,
        )

    def submit_sudo_password(
        self,
        password: str,
        remember: bool = False,
        from_store: bool = False,
    ):
        pending = self._pending_time_fix
        self._pending_time_fix = None
        if not pending:
            return
        row_index, check, beijing_time_text, robot_id = pending
        if not password:
            self._finish_check(
                row_index,
                False,
                "已取消感知机 sudo 密码输入，未执行校时",
                "感知机时间未修改",
            )
            return
        if ROBOT_CONFIG.ws_accid != robot_id:
            detail = (
                f"机器人已从 {robot_id} 切换为 {ROBOT_CONFIG.ws_accid}，"
                "未执行旧目标校时"
            )
            self._finish_check(row_index, False, detail, detail)
            return
        self._run_time_fix(
            row_index,
            check,
            beijing_time_text,
            robot_id,
            password,
            remember,
            from_store,
        )

    def _run_time_fix(
        self,
        row_index: int,
        check: AcceptanceCheck,
        beijing_time_text: str,
        robot_id: str,
        sudo_password: str = "",
        remember: bool = False,
        from_store: bool = False,
    ):
        generation = self._profile_generation
        fix_commands = (
            "timedatectl set-timezone Asia/Shanghai && "
            f'date -s "{beijing_time_text}" && '
            "hwclock --systohc"
        )
        if check.target == "companion":
            quoted_commands = shlex.quote(fix_commands)
            command = (
                "if ! sudo -S -p '' -v; then "
                "echo __SUDO_AUTH_FAILED__; exit 40; fi; "
                f"if sudo -n bash -c {quoted_commands}; then "
                "echo __TIME_FIX_OK__; else "
                "echo __TIME_FIX_COMMAND_FAILED__; exit 41; fi"
            )
        else:
            command = (
                f"sudo bash -c {shlex.quote(fix_commands)} "
                "&& echo __TIME_FIX_OK__"
            )
        worker = self._create_ssh_worker(check.target, robot_id)
        worker.set_command(command)
        if check.target == "companion":
            worker.set_stdin_text(sudo_password + "\n")
            worker.set_transient_credential(
                sudo_password, remember, from_store
            )
        sudo_password = ""
        worker.command_finished.connect(
            lambda exit_code, current_row=row_index, current_check=check, current_worker=worker, current=generation:
            self._run_if_current(
                current,
                self._on_time_fixed,
                current_row, current_check, exit_code,
                current_worker.collected_output, robot_id,
                beijing_time_text, current_worker,
            )
        )
        worker.error_occurred.connect(
            lambda error, current_row=row_index, current=generation:
            self._run_if_current(
                current, self._finish_check,
                current_row, False, f"自动校时 SSH 失败: {error}", error,
            )
        )
        self._workers.append(worker)
        worker.start()

    def _on_time_fixed(
        self,
        row_index: int,
        check: AcceptanceCheck,
        exit_code: int,
        output: str,
        robot_id: str,
        beijing_time_text: str,
        worker: SshWorker,
    ):
        if exit_code != 0 or "__TIME_FIX_OK__" not in output:
            detail = output.strip() or "校时命令未返回成功标记"
            if check.target == "companion" and self._is_sudo_auth_failure(detail):
                if worker.stored_credential_invalid:
                    self.log_message.emit(
                        "[凭据] 已保存的感知机 sudo 密码失效，已删除",
                        "warn",
                    )
                self._pending_time_fix = (
                    row_index, check, beijing_time_text, robot_id
                )
                self._set_row(
                    row_index,
                    "等待密码",
                    "sudo 密码错误，请重新输入",
                    "",
                )
                self.sudo_password_required.emit(
                    ROBOT_CONFIG.perception_ip,
                    ROBOT_CONFIG.perception_user,
                    robot_id,
                )
                return
            self._finish_check(row_index, False, "自动校时失败", detail)
            return

        if check.target == "companion" and worker.credential_saved is not None:
            self.log_message.emit(
                "[凭据] 感知机密码已保存到系统凭据管理器"
                if worker.credential_saved
                else "[凭据] 系统凭据管理器不可用，密码未保存",
                "pass" if worker.credential_saved else "warn",
            )

        self._append_detail(f"[自动校时] {check.name}\n{output.strip()}\n")
        worker = self._create_ssh_worker(check.target, robot_id)
        worker.set_command(TIME_CHECK_COMMAND)
        generation = self._profile_generation
        worker.command_finished.connect(
            lambda exit_code, current_row=row_index, current_check=check, current_worker=worker, current=generation:
            self._run_if_current(
                current, self._request_beijing_time,
                current_row, current_check, current_worker.collected_output, True,
            )
        )
        worker.error_occurred.connect(
            lambda error, current_row=row_index, current=generation:
            self._run_if_current(
                current, self._finish_check,
                current_row, False, f"校时复验 SSH 失败: {error}", error,
            )
        )
        self._workers.append(worker)
        worker.start()

    _is_sudo_auth_failure = staticmethod(SshWorker._is_sudo_auth_failure)

    def _on_time_verified(
        self,
        row_index: int,
        check: AcceptanceCheck,
        output: str,
        reference: tuple[datetime, float],
    ):
        passed, summary = _evaluate_time_output(
            output,
            check.category,
            _current_reference_time(reference),
        )
        detail = f"[校时复验]\n{output.strip()}"
        if passed:
            summary = f"自动校时成功；{summary}"
        else:
            summary = f"自动校时后仍不一致；{summary}"
        self._finish_check(row_index, passed, summary, detail)

    def _evaluate_ssh_output(self, check: AcceptanceCheck, output: str) -> tuple[bool, str]:
        stripped = output.strip()
        if not stripped:
            return False, "无输出"
        if check.key in {"main_ssh", "companion_ssh"}:
            return True, stripped.splitlines()[0]
        if check.key == "cpu":
            cores = int(stripped) if stripped.isdigit() else 0
            return cores == ROBOT_CONFIG.expected_cpu_cores, f"检测到 {cores} 核，期望 {ROBOT_CONFIG.expected_cpu_cores} 核"
        if check.key == "camera":
            lower_output = stripped.lower()
            has_camera = any(keyword in lower_output for keyword in ("camera", "realsense", "imaging", "video"))
            if self._profile.key == "hu_l04_01":
                has_visual_service = any(
                    keyword in lower_output
                    for keyword in ("mroswebvideo", "cosa", "opus", "gesturemrosnode")
                )
                return has_camera or has_visual_service, "视觉/相机服务=" + ("是" if has_camera or has_visual_service else "否")
            has_usb3 = "5000" in stripped or "5000M" in stripped
            return has_camera and has_usb3, f"相机={'是' if has_camera else '否'}，USB3={'是' if has_usb3 else '否'}"
        if check.key == "imu":
            import re
            matches = re.findall(r"average rate:\s*([\d.]+)", stripped)
            frequency = float(matches[-1]) if matches else 0.0
            passed = abs(frequency - ROBOT_CONFIG.expected_imu_hz) <= ROBOT_CONFIG.imu_tolerance_hz
            return passed, f"IMU {frequency:.1f}Hz，期望 {ROBOT_CONFIG.expected_imu_hz:.0f}Hz"
        if check.key == "main_disk":
            return "%" in stripped, stripped.splitlines()[-1] if stripped.splitlines() else "已读取磁盘"
        return True, stripped.splitlines()[0]

    def _finish_check(self, row_index: int, passed: bool, summary: str, detail: str):
        status = "PASS" if passed else "FAIL"
        self._set_row(row_index, status, summary, datetime.now().strftime("%H:%M:%S"))
        self._append_detail(f"[{status}] {self.CHECKS[row_index].name}\n{detail}\n")
        self.log_message.emit(f"[验收] {self.CHECKS[row_index].name}: {status}", "pass" if passed else "error")
        self._run_next_check()

    def _finish_na(self, row_index: int, summary: str):
        self._set_row(row_index, "N/A", summary, datetime.now().strftime("%H:%M:%S"))
        self._append_detail(f"[N/A] {self.CHECKS[row_index].name}\n{summary}\n")
        self.log_message.emit(f"[验收] {self.CHECKS[row_index].name}: N/A", "info")
        self._run_next_check()

    def _set_row(self, row_index: int, status: str, summary: str, time_text: str):
        self.check_table.item(row_index, 3).setText(status)
        self.check_table.item(row_index, 4).setText(summary)
        self.check_table.item(row_index, 5).setText(time_text)
        status_item = self.check_table.item(row_index, 3)
        color_map = {
            "PASS": "#00B42A",
            "FAIL": "#F53F3F",
            "执行中": "#FF7D00",
            "等待授权": "#FF7D00",
            "等待密码": "#FF7D00",
            "N/A": "#C9CDD4",
            "待执行": "#86909C",
        }
        status_item.setForeground(Qt.GlobalColor.black)
        status_item.setBackground(QColor(color_map.get(status, "#F2F3F5")))

    def _append_detail(self, text: str):
        self.detail_view.append(text)

    def _set_running(self, running: bool):
        self.run_all_btn.setEnabled(not running)
        self.run_selected_btn.setEnabled(not running)
        self.summary_label.setText("执行中..." if running else "就绪")

    def _update_summary(self):
        passed = 0
        failed = 0
        not_applicable = 0
        for row_index in range(self.check_table.rowCount()):
            status = self.check_table.item(row_index, 3).text()
            if status == "PASS":
                passed += 1
            elif status == "FAIL":
                failed += 1
            elif status == "N/A":
                not_applicable += 1
        self.summary_label.setText(
            f"完成：PASS {passed} / FAIL {failed} / N/A {not_applicable}"
        )

    def _run_if_current(self, generation: int, callback, *args):
        if generation == self._profile_generation:
            return callback(*args)
        return None