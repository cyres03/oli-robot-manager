"""Sequencer editor for building dance/motion/walk chains."""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget,
    QListWidgetItem, QComboBox, QSpinBox, QLabel,
)
from PyQt6.QtCore import pyqtSignal
from models.dance import SequenceStep, DanceSequence


class SequencerEditor(QWidget):
    execute_sequence_clicked = pyqtSignal(DanceSequence)
    save_sequence_clicked = pyqtSignal(str, list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._steps: list[SequenceStep] = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self.step_list = QListWidget()
        self.step_list.setAlternatingRowColors(True)
        self.step_list.setStyleSheet(
            "QListWidget { background: #F7F8FA; border: 1px solid #E5E6EB; border-radius: 6px; color: #1D2129; }"
        )
        layout.addWidget(self.step_list)

        add_bar = QHBoxLayout()
        add_bar.addWidget(QLabel("类型:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["dance", "motion", "walk"])
        add_bar.addWidget(self.type_combo)

        add_bar.addWidget(QLabel("名称:"))
        self.name_combo = QComboBox()
        self.name_combo.setEditable(True)
        self.name_combo.setMinimumWidth(120)
        add_bar.addWidget(self.name_combo)

        add_bar.addWidget(QLabel("延迟(ms):"))
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 30000)
        self.delay_spin.setValue(1000)
        self.delay_spin.setSuffix("ms")
        add_bar.addWidget(self.delay_spin)

        self.add_step_btn = QPushButton("+ 添加步骤")
        self.add_step_btn.clicked.connect(self._add_step)
        add_bar.addWidget(self.add_step_btn)
        layout.addLayout(add_bar)

        action_bar = QHBoxLayout()
        self.remove_btn = QPushButton("删除选中")
        self.remove_btn.clicked.connect(self._remove_step)
        self.clear_btn = QPushButton("清空全部")
        self.clear_btn.clicked.connect(self._clear_all)
        self.save_btn = QPushButton("保存序列")
        self.save_btn.clicked.connect(self._save_sequence)
        self.execute_btn = QPushButton("执行序列")
        self.execute_btn.setStyleSheet(
            "QPushButton { background: #6C5CE7; color: #fff; padding: 8px 16px; "
            "border-radius: 6px; font-weight: bold; border: none; }"
            "QPushButton:hover { background: #5A4BD1; }")
        self.execute_btn.clicked.connect(self._execute)

        action_bar.addWidget(self.remove_btn)
        action_bar.addWidget(self.clear_btn)
        action_bar.addStretch()
        action_bar.addWidget(self.save_btn)
        action_bar.addWidget(self.execute_btn)
        layout.addLayout(action_bar)

    def _add_step(self):
        step = SequenceStep(
            type=self.type_combo.currentText(),
            name=self.name_combo.currentText(),
            delay_ms=self.delay_spin.value(),
        )
        self._steps.append(step)
        display = f"[{step.type}] {step.name} → 等待 {step.delay_ms}ms"
        self.step_list.addItem(QListWidgetItem(display))

    def _remove_step(self):
        row = self.step_list.currentRow()
        if row >= 0:
            self.step_list.takeItem(row)
            del self._steps[row]

    def _clear_all(self):
        self.step_list.clear()
        self._steps.clear()

    def _save_sequence(self):
        self.save_sequence_clicked.emit("未命名序列", self._steps)

    def _execute(self):
        seq = DanceSequence(name="当前序列", steps=list(self._steps))
        self.execute_sequence_clicked.emit(seq)

    def load_sequence(self, sequence: DanceSequence):
        self._clear_all()
        self._steps = list(sequence.steps)
        for step in self._steps:
            display = f"[{step.type}] {step.name} → 等待 {step.delay_ms}ms"
            self.step_list.addItem(QListWidgetItem(display))

    def set_dance_names(self, names: list[str]):
        current = self.name_combo.currentText()
        self.name_combo.clear()
        self.name_combo.addItems(names)
        if current in names:
            self.name_combo.setCurrentText(current)

    @property
    def steps(self) -> list[SequenceStep]:
        return list(self._steps)
