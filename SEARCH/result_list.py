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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(250)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Header
        header = QWidget()
        header.setStyleSheet("background: #f0f0f0; border-top: 1px solid #ccc; border-bottom: 1px solid #ccc;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 4, 8, 4)
        
        title = QLabel("Search Results")
        title.setStyleSheet("font-weight: bold; border: none; background: transparent;")
        
        close_btn = QPushButton("×")
        close_btn.setFixedSize(20, 20)
        close_btn.setToolTip("Close Results")
        close_btn.setStyleSheet("border: none; font-weight: bold; font-size: 16px; background: transparent;")
        close_btn.clicked.connect(self.close_requested.emit)
        
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(close_btn)
        
        # List widget
        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet("""
            QListWidget {
                border: none;
                background: white;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #eee;
            }
            QListWidget::item:selected {
                background: #e0f0ff;
                color: black;
            }
        """)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        
        layout.addWidget(header)
        layout.addWidget(self.list_widget)

    def _on_item_double_clicked(self, item: QListWidgetItem):
        res = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(res, SearchResult):
            self.result_selected.emit(res)

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
        if self.isVisible() and self.list_widget.count() > 0:
            row = self.list_widget.currentRow()
            if row > 0:
                self.list_widget.setCurrentRow(row - 1)
            elif row == -1:
                self.list_widget.setCurrentRow(self.list_widget.count() - 1)

    def select_next(self):
        if self.isVisible() and self.list_widget.count() > 0:
            row = self.list_widget.currentRow()
            if row < self.list_widget.count() - 1:
                self.list_widget.setCurrentRow(row + 1)
            elif row == -1:
                self.list_widget.setCurrentRow(0)
