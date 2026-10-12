"""
GUI/filter_view.py — Dynamic Filter Explorer Sidebar Panel

Provides the left sidebar panel for navigating Targets, dynamic Field (Path) hierarchy,
and optional Condition & Value filters.
Updates Grid View in real-time without requiring a separate "Apply Filter" button.
"""

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QComboBox, QPushButton, QFrame,
    QScrollArea, QSizePolicy
)
from PyQt6.QtCore import pyqtSignal, Qt

from FILTER.engine import get_targets, get_next_level, get_values_for_path

class FilterPanel(QWidget):
    """
    Left-docked Filter Explorer panel.
    Emits filter_changed(target, path, condition, value) whenever selections change.
    """
    filter_changed = pyqtSignal(str, list, str, str)
    filter_applied = pyqtSignal(str, list, str, str)  # Kept for backward compatibility
    filter_cleared = pyqtSignal()
    close_requested = pyqtSignal()

    DEFAULT_CONDITION = "(Tümü / Filtresiz)"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_root = None
        self.path_combos = []  # List of QComboBox for the dynamic path
        self._updating = False
        self._setup_ui()

    def _setup_ui(self):
        self.setMinimumWidth(260)
        self.setMaximumWidth(420)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header bar
        header_frame = QFrame()
        header_frame.setStyleSheet("background: #e4e4e4; border-bottom: 1px solid #c8c8c8; padding: 4px;")
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(8, 4, 8, 4)

        title = QLabel("<b>Filter Explorer</b>")
        title.setStyleSheet("font-size: 13px; color: #1e1e1e;")
        header_layout.addWidget(title)

        header_layout.addStretch()

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(22, 22)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setStyleSheet("QPushButton { border: none; font-weight: bold; color: #555; } QPushButton:hover { color: #d9534f; }")
        btn_close.clicked.connect(self.close_requested.emit)
        header_layout.addWidget(btn_close)

        main_layout.addWidget(header_frame)

        # Scrollable container for dynamic controls
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        content_widget = QWidget()
        content_widget.setStyleSheet("background: transparent;")
        self.content_layout = QVBoxLayout(content_widget)
        self.content_layout.setContentsMargins(10, 10, 10, 10)
        self.content_layout.setSpacing(12)
        self.content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # ── 1. Target Section ─────────────────────────────────────────
        lbl_target_header = QLabel("TARGET (LISTE)")
        lbl_target_header.setStyleSheet("font-weight: bold; color: #666; font-size: 11px;")
        self.content_layout.addWidget(lbl_target_header)

        self.combo_target = QComboBox()
        self.combo_target.currentIndexChanged.connect(self._on_target_changed)
        self.content_layout.addWidget(self.combo_target)

        # ── 2. Field (Path) Section ──────────────────────────────────
        lbl_field_header = QLabel("FIELD (ALAN SEÇİMİ)")
        lbl_field_header.setStyleSheet("font-weight: bold; color: #666; font-size: 11px; margin-top: 4px;")
        self.content_layout.addWidget(lbl_field_header)

        self.path_container = QWidget()
        self.path_layout = QVBoxLayout(self.path_container)
        self.path_layout.setContentsMargins(0, 0, 0, 0)
        self.path_layout.setSpacing(6)
        self.content_layout.addWidget(self.path_container)

        # ── 3. Condition & Value Section ─────────────────────────────
        self.cond_frame = QFrame()
        self.cond_frame.setStyleSheet("QFrame { background: #f8f8f8; border: 1px solid #dcdcdc; border-radius: 4px; padding: 6px; }")
        cond_layout = QVBoxLayout(self.cond_frame)
        cond_layout.setContentsMargins(8, 8, 8, 8)
        cond_layout.setSpacing(8)

        lbl_cond_header = QLabel("KOŞUL & DEĞER (İSTEĞE BAĞLI)")
        lbl_cond_header.setStyleSheet("font-weight: bold; color: #666; font-size: 11px; border: none; background: transparent;")
        cond_layout.addWidget(lbl_cond_header)

        lbl_cond = QLabel("Condition:")
        lbl_cond.setStyleSheet("color: #444; font-size: 11px; border: none; background: transparent;")
        cond_layout.addWidget(lbl_cond)

        self.combo_condition = QComboBox()
        self.combo_condition.addItems([
            self.DEFAULT_CONDITION, "Equals", "Not Equals", "Contains", "Starts With", "Ends With"
        ])
        self.combo_condition.currentIndexChanged.connect(self._on_condition_changed)
        cond_layout.addWidget(self.combo_condition)

        lbl_val = QLabel("Value:")
        lbl_val.setStyleSheet("color: #444; font-size: 11px; border: none; background: transparent;")
        cond_layout.addWidget(lbl_val)

        self.combo_value = QComboBox()
        self.combo_value.currentIndexChanged.connect(self._on_value_changed)
        cond_layout.addWidget(self.combo_value)

        self.content_layout.addWidget(self.cond_frame)
        self.cond_frame.hide()

        # ── 4. Action Buttons ─────────────────────────────────────────
        self.btn_clear = QPushButton("Clear Filter")
        self.btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear.setStyleSheet("QPushButton { padding: 6px 12px; font-weight: bold; background: #e0e0e0; border: 1px solid #b8b8b8; border-radius: 3px; } QPushButton:hover { background: #d0d0d0; }")
        self.btn_clear.clicked.connect(self._on_clear_clicked)
        self.content_layout.addWidget(self.btn_clear)

        self.content_layout.addStretch()

        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area)

    def load_xml_root(self, root):
        """Loads a new XML root and populates the target dropdown."""
        self.current_root = root
        self._updating = True
        self.combo_target.blockSignals(True)
        self.combo_target.clear()

        self._clear_path_from_level(0)
        self.combo_condition.setCurrentIndex(0)
        self.combo_value.clear()
        self.cond_frame.hide()

        if root is not None:
            self.combo_target.addItem("-- Select Target --")
            targets = get_targets(root)
            if targets:
                self.combo_target.addItems(targets)

        self.combo_target.blockSignals(False)
        self._updating = False

    def _clear_path_from_level(self, level_index: int):
        """Removes all path combo boxes starting from level_index."""
        while len(self.path_combos) > level_index:
            item = self.path_combos.pop()
            container = item["container"]
            self.path_layout.removeWidget(container)
            container.deleteLater()

        # If path is cleared below a leaf, hide or adjust condition
        if not self.path_combos:
            self.cond_frame.hide()

    PLACEHOLDER_TEXT = "-- Tümü / Seçiniz --"

    def _get_current_path(self, up_to_index: int = None) -> list[str]:
        """Returns the current path sequence represented by the combo boxes."""
        if up_to_index is None:
            up_to_index = len(self.path_combos)
        path = []
        for i in range(up_to_index):
            text = self.path_combos[i]["combo"].currentText().strip()
            if text and not text.startswith("--"):
                path.append(text)
            else:
                # If combo is at placeholder "-- Tümü / Seçiniz --", path selection stops here
                break
        return path

    def _on_target_changed(self):
        """Called when Target combo box selection changes."""
        if self._updating:
            return

        self._updating = True
        self._clear_path_from_level(0)
        self.combo_condition.setCurrentIndex(0)
        self.combo_value.clear()
        self.cond_frame.hide()

        target = self.combo_target.currentText().strip()
        if target and target != "-- Select Target --" and self.current_root is not None:
            self._evaluate_level(target, [])
        self._updating = False

        if target == "-- Select Target --" or not target:
            self.filter_cleared.emit()
        else:
            self._emit_filter_changed()

    def _on_path_changed(self, index: int):
        """Called when a path level combo box selection changes."""
        if self._updating:
            return

        self._updating = True
        self._clear_path_from_level(index + 1)
        target = self.combo_target.currentText().strip()
        path = self._get_current_path()

        if target and target != "-- Select Target --" and self.current_root is not None:
            self._evaluate_level(target, path)
        self._updating = False

        self._emit_filter_changed()

    def _evaluate_level(self, target: str, current_path: list[str]):
        """Decides whether to add another path level or populate leaf condition/value."""
        next_levels = get_next_level(self.current_root, target, current_path)

        if next_levels:
            # It's a container node. Add next level combo box with placeholder default.
            level_container = QWidget()
            lvl_layout = QVBoxLayout(level_container)
            lvl_layout.setContentsMargins(0, 0, 0, 0)
            lvl_layout.setSpacing(2)

            level_num = len(self.path_combos) + 1
            lvl_label = QLabel(f"Level {level_num}:")
            lvl_label.setStyleSheet("color: #777; font-size: 11px;")
            lvl_layout.addWidget(lvl_label)

            combo = QComboBox()
            combo.addItem(self.PLACEHOLDER_TEXT)
            combo.addItems(next_levels)
            combo.setCurrentIndex(0)

            combo_index = len(self.path_combos)
            combo.currentIndexChanged.connect(lambda _, idx=combo_index: self._on_path_changed(idx))

            lvl_layout.addWidget(combo)
            self.path_combos.append({"combo": combo, "container": level_container})
            self.path_layout.addWidget(level_container)

            # Hide condition frame while waiting at container or placeholder
            self.cond_frame.hide()
        else:
            # It's a leaf node. Populate valid values and show condition section.
            if current_path:
                prev_val = self.combo_value.currentText().strip()
                values = get_values_for_path(self.current_root, target, current_path)

                self.combo_value.blockSignals(True)
                self.combo_value.clear()
                if values:
                    self.combo_value.addItems(values)
                self.combo_value.blockSignals(False)

                # Requirement 4: Verify if previous condition/value is still valid for the new selection
                if prev_val and prev_val in values:
                    self.combo_value.setCurrentText(prev_val)
                else:
                    # Previous value is invalid for new path, reset to unconditional view
                    self.combo_condition.blockSignals(True)
                    self.combo_condition.setCurrentIndex(0)
                    self.combo_condition.blockSignals(False)
                    self.combo_value.setEnabled(False)

                self.cond_frame.show()
            else:
                self.cond_frame.hide()

    def _on_condition_changed(self):
        """Called when condition changes."""
        if self._updating:
            return

        cond = self.combo_condition.currentText()
        if cond == self.DEFAULT_CONDITION:
            self.combo_value.setEnabled(False)
        else:
            self.combo_value.setEnabled(True)

        self._emit_filter_changed()

    def _on_value_changed(self):
        """Called when value changes."""
        if self._updating:
            return

        # Only emit if condition is active
        cond = self.combo_condition.currentText()
        if cond != self.DEFAULT_CONDITION:
            self._emit_filter_changed()

    def _emit_filter_changed(self):
        """Emits the filter_changed and filter_applied signals."""
        target = self.combo_target.currentText().strip()
        if not target or target == "-- Select Target --" or self.current_root is None:
            return

        path = self._get_current_path()
        condition = self.combo_condition.currentText()
        value = self.combo_value.currentText().strip()

        if condition == self.DEFAULT_CONDITION or not condition:
            condition = ""
            value = ""

        self.filter_changed.emit(target, path, condition, value)
        self.filter_applied.emit(target, path, condition, value)

    def _on_clear_clicked(self):
        """Clears all selections and restores original view."""
        self._updating = True
        self.combo_target.blockSignals(True)
        self.combo_target.setCurrentIndex(0)
        self.combo_target.blockSignals(False)

        self._clear_path_from_level(0)
        self.combo_condition.setCurrentIndex(0)
        self.combo_value.clear()
        self.cond_frame.hide()
        self._updating = False

        self.filter_cleared.emit()

