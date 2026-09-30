"""
GUI/search_panel.py — Search Toolbar Widget

A compact search bar with input field, search button, prev/next
navigation, and a result count label.
All callbacks are placeholders — the real search engine (SEARCH/)
will be connected later.
"""

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QLineEdit, QPushButton, QLabel, QSizePolicy,
)
from PyQt6.QtGui import QIcon
from PyQt6.QtCore import Qt, pyqtSignal


class SearchPanel(QWidget):
    """Inline search bar for the toolbar area."""

    # Signals that the main window (or search engine) can connect to
    search_requested = pyqtSignal(str)     # emitted with the query text
    next_requested = pyqtSignal()
    previous_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------
    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Search icon label
        search_icon_label = QLabel("🔍")
        search_icon_label.setStyleSheet("font-size: 14px; padding: 0 2px; background: transparent;")
        layout.addWidget(search_icon_label)

        # Search input
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search XML...")
        self.search_input.setMinimumWidth(220)
        self.search_input.setMaximumWidth(360)
        self.search_input.setClearButtonEnabled(True)
        self.search_input.returnPressed.connect(self._on_search)
        layout.addWidget(self.search_input)

        # Search button
        self.btn_search = QPushButton("Search")
        self.btn_search.setFixedWidth(70)
        self.btn_search.clicked.connect(self._on_search)
        self.btn_search.setToolTip("Search XML content")
        layout.addWidget(self.btn_search)

        # Previous result
        self.btn_prev = QPushButton("◀")
        self.btn_prev.setFixedWidth(32)
        self.btn_prev.setToolTip("Previous result")
        self.btn_prev.clicked.connect(self.previous_requested.emit)
        layout.addWidget(self.btn_prev)

        # Next result
        self.btn_next = QPushButton("▶")
        self.btn_next.setFixedWidth(32)
        self.btn_next.setToolTip("Next result")
        self.btn_next.clicked.connect(self.next_requested.emit)
        layout.addWidget(self.btn_next)

        # Result count
        self.result_label = QLabel("0 matches")
        self.result_label.setStyleSheet(
            "color: #808080; font-size: 12px; padding: 0 6px; background: transparent;"
        )
        layout.addWidget(self.result_label)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_result_count(self, count: int):
        """Update the displayed result count."""
        self.result_label.setText(f"{count} match{'es' if count != 1 else ''}")

    def get_query(self) -> str:
        """Return the current search query text."""
        return self.search_input.text().strip()

    def clear(self):
        """Reset the search bar."""
        self.search_input.clear()
        self.result_label.setText("0 matches")

    # ------------------------------------------------------------------
    # Internal slots
    # ------------------------------------------------------------------
    def _on_search(self):
        query = self.get_query()
        if query:
            self.search_requested.emit(query)
