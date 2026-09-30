"""
GUI/json_preview.py — JSON Preview Panel

A read-only text panel intended to display the JSON representation
of the currently loaded XML.  Uses QPlainTextEdit for performance
with large documents.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPlainTextEdit, QSizePolicy,
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt
import json


# Demo JSON that matches the demo tree / grid data
_DEMO_JSON = json.dumps(
    {
        "orders": {
            "order": [
                {
                    "customer": {"@id": "123"},
                    "items": [
                        {"item": {"@sku": "A-456"}},
                        {"item": {"@sku": "B-789"}},
                    ],
                    "total": {"@currency": "USD", "#text": "299.99"},
                },
            ]
        }
    },
    indent=2,
    ensure_ascii=False,
)


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

        # Panel header (styled via QSS objectName: panelHeader)
        header = QLabel("JSON PREVIEW")
        header.setObjectName("panelHeader")
        layout.addWidget(header)

        # Text editor — read-only, monospaced
        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        mono = QFont("Cascadia Code", 12)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.text_edit.setFont(mono)

        # Show demo content
        self.text_edit.setPlainText(_DEMO_JSON)

        layout.addWidget(self.text_edit)

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
