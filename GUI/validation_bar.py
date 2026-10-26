"""
GUI/validation_bar.py — XML Syntax Error Validation Bar
Displays live XML syntax errors at the bottom of the Text View with jump-to-error capability.
Supports single error compact mode and multi-error list mode.
"""

from __future__ import annotations

from typing import Optional
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QSizePolicy, QScrollArea, QWidget
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor, QMouseEvent

from XML.document_session import SyntaxErrorInfo


class ValidationBar(QFrame):
    """
    Bottom bar in Text View displaying XML syntax error details.
    Hidden when XML draft is valid.
    Emits navigate_requested(line, col) when user clicks to navigate.
    """
    navigate_requested = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("validationBar")
        self._line: int = 1
        self._column: int = 1
        self._message: str = ""
        self._errors: list[SyntaxErrorInfo] = []

        self._setup_ui()
        self.clear_error()

    def _setup_ui(self):
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("""
            QFrame#validationBar {
                background-color: #fdf2f2;
                border-top: 1px solid #f5c6cb;
                border-bottom: 1px solid #f5c6cb;
                padding: 0 4px;
            }
            QLabel {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                font-size: 11px;
            }
            QLabel#badge {
                background-color: #dc3545;
                color: #ffffff;
                font-weight: bold;
                border-radius: 3px;
                padding: 2px 6px;
            }
            QLabel#location {
                color: #721c24;
                font-weight: bold;
                padding-left: 4px;
            }
            QLabel#msg {
                color: #491217;
                padding-left: 4px;
            }
            QPushButton.jumpBtn {
                background-color: #ffffff;
                color: #721c24;
                border: 1px solid #dc3545;
                border-radius: 3px;
                padding: 2px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton.jumpBtn:hover {
                background-color: #f8d7da;
            }
            QPushButton.jumpBtn:pressed {
                background-color: #f1b0b7;
            }
            QScrollArea#errorScrollArea {
                background: transparent;
                border: none;
            }
        """)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(6, 4, 6, 4)
        self.main_layout.setSpacing(4)

        # -----------------------------------------------------------
        # 1. Single Error Container (Compact mode, 34px)
        # -----------------------------------------------------------
        self.single_container = QWidget(self)
        single_layout = QHBoxLayout(self.single_container)
        single_layout.setContentsMargins(0, 0, 0, 0)
        single_layout.setSpacing(6)

        self.badge_label = QLabel("Sözdizimi Hatası")
        self.badge_label.setObjectName("badge")
        single_layout.addWidget(self.badge_label)

        self.location_label = QLabel("Satır 1:")
        self.location_label.setObjectName("location")
        single_layout.addWidget(self.location_label)

        self.msg_label = QLabel("")
        self.msg_label.setObjectName("msg")
        self.msg_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.msg_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        single_layout.addWidget(self.msg_label)

        self.jump_btn = QPushButton("Hataya Git")
        self.jump_btn.setObjectName("jumpBtn")
        self.jump_btn.setProperty("class", "jumpBtn")
        self.jump_btn.setToolTip("Hatanın bulunduğu satıra git")
        self.jump_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.jump_btn.clicked.connect(self._on_jump_clicked)
        single_layout.addWidget(self.jump_btn)

        self.main_layout.addWidget(self.single_container)

        # -----------------------------------------------------------
        # 2. Multi Error Container (List mode)
        # -----------------------------------------------------------
        self.multi_container = QWidget(self)
        multi_layout = QVBoxLayout(self.multi_container)
        multi_layout.setContentsMargins(0, 0, 0, 0)
        multi_layout.setSpacing(2)

        self.multi_header_label = QLabel("Sözdizimi Hataları")
        self.multi_header_label.setObjectName("badge")
        self.multi_header_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        multi_layout.addWidget(self.multi_header_label)

        self.scroll_area = QScrollArea(self.multi_container)
        self.scroll_area.setObjectName("errorScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.scroll_widget = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_widget)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(2)
        self.scroll_area.setWidget(self.scroll_widget)

        multi_layout.addWidget(self.scroll_area)
        self.main_layout.addWidget(self.multi_container)

    def show_error(self, line: int, column: int, message: str):
        """Displays single error details and makes the bar visible."""
        err = SyntaxErrorInfo(line=line, column=column, message=message)
        self.show_errors([err])

    def show_errors(self, errors: list[SyntaxErrorInfo]):
        """Displays single or multiple errors in the validation bar."""
        if not errors:
            self.clear_error()
            return

        self._errors = list(errors)
        primary = errors[0]
        self._line = max(1, primary.line)
        self._column = max(1, primary.column)
        self._message = primary.message.strip() if primary.message else "Geçersiz XML sözdizimi."

        self.location_label.setText(f"Satır {self._line}:")
        self.msg_label.setText(self._message)
        self.msg_label.setToolTip(self._message)

        if len(errors) == 1:
            self.single_container.setVisible(True)
            self.multi_container.setVisible(False)
            self.setFixedHeight(34)
            self.badge_label.setText("Sözdizimi Hatası")
        else:
            self.single_container.setVisible(False)
            self.multi_container.setVisible(True)

            self.multi_header_label.setText(f"{len(errors)} Sözdizimi Hatası")

            # Rebuild multi-error list items
            while self.scroll_layout.count():
                item = self.scroll_layout.takeAt(0)
                widget = item.widget()
                if widget:
                    widget.deleteLater()

            for err in errors:
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(4, 2, 4, 2)
                row_layout.setSpacing(6)

                loc_lbl = QLabel(f"Satır {max(1, err.line)}:")
                loc_lbl.setStyleSheet("color: #721c24; font-weight: bold;")
                row_layout.addWidget(loc_lbl)

                err_msg = err.message.strip() if err.message else "Geçersiz XML sözdizimi."
                msg_lbl = QLabel(err_msg)
                msg_lbl.setStyleSheet("color: #491217;")
                msg_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                msg_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                msg_lbl.setToolTip(err_msg)
                row_layout.addWidget(msg_lbl)

                btn = QPushButton(" Hataya Git")
                btn.setProperty("class", "jumpBtn")
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #ffffff;
                        color: #721c24;
                        border: 1px solid #dc3545;
                        border-radius: 3px;
                        padding: 1px 8px;
                        font-size: 10px;
                        font-weight: 600;
                    }
                    QPushButton:hover {
                        background-color: #f8d7da;
                    }
                """)
                btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                btn.clicked.connect(lambda _, l=err.line, c=err.column: self.navigate_requested.emit(l, c))
                row_layout.addWidget(btn)

                self.scroll_layout.addWidget(row)

            # Compact dynamic height: 2 items -> ~70px, 3+ items -> max 115px with scrolling
            calc_height = min(115, 34 + len(errors) * 26)
            self.setFixedHeight(calc_height)

        self.setVisible(True)

    def clear_error(self):
        """Hides the bar and clears all error details."""
        self._errors = []
        self._line = 1
        self._column = 1
        self._message = ""
        self.location_label.setText("")
        self.msg_label.setText("")
        self.msg_label.setToolTip("")

        # Clear multi error items
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        self.single_container.setVisible(True)
        self.multi_container.setVisible(False)
        self.setFixedHeight(34)
        self.setVisible(False)

    def get_error_details(self) -> tuple[int, int, str]:
        """Returns primary error tuple (line, column, message)."""
        return self._line, self._column, self._message

    def get_all_errors(self) -> list[SyntaxErrorInfo]:
        """Returns all currently tracked syntax errors."""
        return list(self._errors)

    def _on_jump_clicked(self):
        self.navigate_requested.emit(self._line, self._column)

    def mousePressEvent(self, event: QMouseEvent):
        # Allow clicking on the bar (outside buttons/labels) in single mode to trigger jump
        if self.single_container.isVisible() and event.button() == Qt.MouseButton.LeftButton:
            self._on_jump_clicked()
        super().mousePressEvent(event)
