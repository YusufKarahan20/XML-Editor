from dataclasses import dataclass
from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import Qt, pyqtSignal

@dataclass
class SearchResult:
    matched_text: str
    tag: str
    match_type: str
    line_number: int
    location: str
    element: object  # Reference to the original lxml element

class SearchResultsPanel(QWidget):
    close_requested = pyqtSignal()
    result_selected = pyqtSignal(SearchResult)
    match_index_changed = pyqtSignal(int, int)  # (current_1_based, total_count)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(80)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Header
        self.header = QWidget()
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(8, 4, 8, 4)
        
        self.title = QLabel("Search Results")
        
        self.close_btn = QPushButton("×")
        self.close_btn.setFixedSize(20, 20)
        self.close_btn.setToolTip("Close Results")
        self.close_btn.clicked.connect(self.close_requested.emit)
        
        header_layout.addWidget(self.title)
        header_layout.addStretch()
        header_layout.addWidget(self.close_btn)
        
        # List widget
        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        
        layout.addWidget(self.header)
        layout.addWidget(self.list_widget)
        self.refresh_theme("light")

    def _navigate_to_row(self, row: int):
        self.list_widget.setCurrentRow(row)
        item = self.list_widget.item(row)
        if item:
            self.list_widget.scrollToItem(item)
            res = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(res, SearchResult):
                self.result_selected.emit(res)
            self.match_index_changed.emit(row + 1, self.list_widget.count())

    def _on_item_double_clicked(self, item: QListWidgetItem):
        res = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(res, SearchResult):
            self.result_selected.emit(res)
        row = self.list_widget.row(item)
        if row >= 0:
            self.match_index_changed.emit(row + 1, self.list_widget.count())

    def clear(self):
        """Clear all search results from the list widget."""
        self.list_widget.clear()

    def update_results(self, results: list[SearchResult]):
        self.list_widget.clear()
        for i, res in enumerate(results, 1):
            line_str = res.line_number if res.line_number is not None else "-"
            text = (f"{i}. {res.matched_text}\n"
                    f"   Tag: {res.tag}\n"
                    f"   Type: {res.match_type}\n"
                    f"   Line: {line_str} | Location: {res.location}")
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, res)
            self.list_widget.addItem(item)

    def select_previous(self):
        count = self.list_widget.count()
        if count == 0:
            return
        row = self.list_widget.currentRow()
        if row > 0:
            new_row = row - 1
        else:
            new_row = count - 1
        self._navigate_to_row(new_row)

    def select_next(self):
        count = self.list_widget.count()
        if count == 0:
            return
        row = self.list_widget.currentRow()
        if 0 <= row < count - 1:
            new_row = row + 1
        else:
            new_row = 0
        self._navigate_to_row(new_row)

    def refresh_theme(self, theme_name: str):
        """Updates search results panel colors from PALETTE."""
        from GUI.theme_manager import PALETTE
        pal = PALETTE.get(theme_name.lower(), PALETTE["light"])
        self.header.setStyleSheet(f"background: {pal['header_bg']}; border-top: 1px solid {pal['border_default']}; border-bottom: 1px solid {pal['border_default']};")
        self.title.setStyleSheet(f"font-weight: bold; border: none; background: transparent; color: {pal['text_primary']};")
        self.close_btn.setStyleSheet(f"border: none; font-weight: bold; font-size: 16px; background: transparent; color: {pal['text_muted']};")
        self.list_widget.setStyleSheet(f"""
            QListWidget {{
                border: none;
                background: {pal['panel_base']};
                color: {pal['text_primary']};
            }}
            QListWidget::item {{
                padding: 8px;
                border-bottom: 1px solid {pal['border_muted']};
                color: {pal['text_primary']};
            }}
            QListWidget::item:selected {{
                background: {pal['selection_bg']};
                color: {pal['selection_fg']};
            }}
            QListWidget::item:hover:!selected {{
                background: {pal['toolbar_btn_hover']};
            }}
        """)
