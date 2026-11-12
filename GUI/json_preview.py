"""
GUI/json_preview.py — JSON Preview Panel

A read-only text panel intended to display the JSON representation
of the currently loaded XML.  Uses QPlainTextEdit for performance
with large documents.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit, QSizePolicy,
    QPushButton, QApplication,
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt, QTimer, QSize, pyqtSignal
from GUI.icon_provider import IconProvider



class JsonPreviewPanel(QWidget):
    """Right-side panel: read-only JSON preview."""

    close_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Panel header with title, Copy button, and Close button
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        header = QLabel("JSON PREVIEW")
        header.setObjectName("panelHeader")
        header_layout.addWidget(header)
        header_layout.addStretch()

        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setIconSize(QSize(13, 13))
        self.copy_btn.setToolTip("Copy JSON to clipboard")
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.setFixedHeight(22)
        self.copy_btn.clicked.connect(self._copy_to_clipboard)
        header_layout.addWidget(self.copy_btn)

        self.btn_close = QPushButton()
        self.btn_close.setIcon(IconProvider.get_icon("x", color="#555555"))
        self.btn_close.setIconSize(QSize(14, 14))
        self.btn_close.setFixedSize(22, 22)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setToolTip("Close JSON Preview")
        self.btn_close.setStyleSheet("QPushButton { border: none; } QPushButton:hover { background-color: #d0d0d0; border-radius: 3px; }")
        self.btn_close.clicked.connect(self.close_requested.emit)
        header_layout.addWidget(self.btn_close)

        layout.addLayout(header_layout)

        # Text editor — read-only, monospaced
        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        mono = QFont("Cascadia Code", 12)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.text_edit.setFont(mono)

        layout.addWidget(self.text_edit)
        from GUI.theme_manager import ThemeManager
        self.refresh_theme(ThemeManager().get_saved_theme())

    def _copy_to_clipboard(self):
        """Copy the current preview text to system clipboard with visual feedback."""
        text = self.get_text()
        if text:
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(text)
                self.copy_btn.setText("Copied!")
                QTimer.singleShot(1500, lambda: self.copy_btn.setText("Copy"))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_json(self, text: str):
        """Set the JSON content to display."""
        self.text_edit.setPlainText(text)

    def clear(self):
        """Clear the preview."""
        self.text_edit.clear()

    def get_text(self) -> str:
        """Return the current text content."""
        return self.text_edit.toPlainText()

    def refresh_theme(self, theme_name: str):
        """Updates copy and close button styling and icon for active theme."""
        from GUI.theme_manager import PALETTE
        pal = PALETTE.get(theme_name.lower(), PALETTE["light"])
        self.copy_btn.setIcon(IconProvider.get_icon("copy", color=pal["json_copy_text"]))
        self.copy_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 2px 10px;
                font-size: 11px;
                font-weight: bold;
                border-radius: 3px;
                border: 1px solid {pal["json_copy_border"]};
                background-color: {pal["json_copy_bg"]};
                color: {pal["json_copy_text"]};
            }}
            QPushButton:hover {{
                background-color: {pal["toolbar_btn_hover"]};
                border-color: {pal["border_muted"]};
            }}
            QPushButton:pressed {{
                background-color: {pal["toolbar_btn_pressed"]};
            }}
        """)
        if hasattr(self, "btn_close") and self.btn_close:
            self.btn_close.setIcon(IconProvider.get_icon("x", color=pal["icon_muted"]))
            self.btn_close.setStyleSheet(f"QPushButton {{ border: none; }} QPushButton:hover {{ background-color: {pal['toolbar_btn_hover']}; border-radius: 3px; }}")
