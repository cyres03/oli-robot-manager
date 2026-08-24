"""
Application composition root.
Creates and wires all services, workers, and the MainWindow.
"""
from pathlib import Path
import sys

from PyQt6.QtCore import QObject
from config import ROBOT_CONFIG
from database.connection import DatabaseConnection
from workers.mcp_worker import McpWorker
from services.dance_service import DanceService
from services.health_check_service import HealthCheckService
from services.power_cycle_service import PowerCycleService
from services.connection_service import ConnectionService
from services.calibrate_service import CalibrateService
from services.robot_monitor import RobotMonitor
from services.managed_test_service import TestCaseService
from ui.main_window import MainWindow


def _resource_path(*parts: str) -> Path:
    base = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return base.joinpath(*parts)


class Application(QObject):
    def __init__(self):
        super().__init__()
        # 1. Initialize database
        db = DatabaseConnection()
        db.initialize_schema()

        # 2. Resolve robot identity and apply its profile
        from config import detect_robot_identity
        identity = detect_robot_identity()
        ROBOT_CONFIG.apply_identity(identity)

        # 3. Create workers
        allowed_tools = (
            ROBOT_CONFIG.active_profile.allowed_tools
            if ROBOT_CONFIG.active_profile else frozenset()
        )
        self.mcp_worker = McpWorker(
            ROBOT_CONFIG.websocket_url,
            ROBOT_CONFIG.ws_accid,
            allowed_tools=allowed_tools,
        )
        self.mcp_worker.update_target(
            ROBOT_CONFIG.ws_accid or None,
            allowed_tools,
            ROBOT_CONFIG.websocket_url,
            ROBOT_CONFIG.profile_key,
        )
        self.mcp_worker.start()

        # 4. Robot monitor (persistent WebSocket for status)
        self.robot_monitor = RobotMonitor()
        self.robot_monitor.start()

        # 5. Create services (inject dependencies)
        self.dance_service = DanceService(self.mcp_worker)
        self.connection_service = ConnectionService()
        self.health_service = HealthCheckService()
        self.power_cycle_service = PowerCycleService(self.health_service)
        self.calibrate_service = CalibrateService(self.mcp_worker)
        test_case_root = _resource_path("resources", "test_cases")
        self.test_case_service = TestCaseService(
            test_case_root / "cases.json",
            test_case_root,
        )

        # 6. Load persisted dance counts
        self.dance_service.load_all_counts()

        # 7. Create main window and inject services
        self.main_window = MainWindow()
        self.main_window.set_services(
            dance_service=self.dance_service,
            health_service=self.health_service,
            power_cycle_service=self.power_cycle_service,
            connection_service=self.connection_service,
            calibrate_service=self.calibrate_service,
            mcp_worker=self.mcp_worker,
            robot_monitor=self.robot_monitor,
            test_case_service=self.test_case_service,
        )
        self.main_window.apply_robot_identity(identity, initial=True)

    @property
    def window(self) -> MainWindow:
        return self.main_window

    def shutdown(self):
        self.test_case_service.shutdown()
        self.mcp_worker.stop()
        self.robot_monitor.stop()
