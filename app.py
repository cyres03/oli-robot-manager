"""
Application composition root.
Creates and wires all services, workers, and the MainWindow.
"""
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
from ui.main_window import MainWindow


class Application(QObject):
    def __init__(self):
        super().__init__()
        # 1. Initialize database
        db = DatabaseConnection()
        db.initialize_schema()

        # 2. Auto-detect accid from connected WiFi
        from config import detect_accid_from_wifi
        accid = detect_accid_from_wifi()
        ROBOT_CONFIG.ws_accid = accid or ""

        # 3. Create workers
        self.mcp_worker = McpWorker(ROBOT_CONFIG.websocket_url, accid)
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
        )

    @property
    def window(self) -> MainWindow:
        return self.main_window

    def shutdown(self):
        self.mcp_worker.stop()
        self.robot_monitor.stop()
