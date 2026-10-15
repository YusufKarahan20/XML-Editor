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
from PyQt6.QtCore import Qt, QTimer



class JsonPreviewPanel(QWidget):
    """Right-side panel: read-only JSON preview."""

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

        # Panel header with title and Copy button
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)

        header = QLabel("JSON PREVIEW")
        header.setObjectName("panelHeader")
        header_layout.addWidget(header)
        header_layout.addStretch()

        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setToolTip("Copy JSON to clipboard")
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.setFixedHeight(22)
        self.copy_btn.setStyleSheet("""
            QPushButton {
                padding: 2px 10px;
                font-size: 11px;
                font-weight: bold;
                border-radius: 3px;
                border: 1px solid #c0c0c0;
                background-color: #f5f5f5;
                color: #333333;
            }
            QPushButton:hover {
                background-color: #e8e8e8;
                border-color: #999999;
            }
            QPushButton:pressed {
                background-color: #dcdcdc;
            }
        """)
        self.copy_btn.clicked.connect(self._copy_to_clipboard)
        header_layout.addWidget(self.copy_btn)

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
