"""
GUI/center_panel.py — Center Tabbed Area
"""
from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTabWidget, QPlainTextEdit, QLabel
)
from PyQt6.QtGui import QFont

from GUI.grid_view import XmlGridPanel

class TextViewPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        
        sample_path = Path(__file__).resolve().parent.parent / "sample.xml"
        try:
            with open(sample_path, "r", encoding="utf-8") as f:
                self.text_edit.setPlainText(f.read())
        except Exception as e:
            self.text_edit.setPlainText(f"(Dosya bulunamadı veya okunamadı: {sample_path})\nHata: {e}")
            
        self.text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        
        mono = QFont("Cascadia Code", 12)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.text_edit.setFont(mono)
        
        layout.addWidget(self.text_edit)

class CenterPanel(QWidget):
    """Center panel holding Text View and Grid View tabs."""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        
        header = QLabel("XML VIEWER")
        header.setObjectName("panelHeader")
        layout.addWidget(header)
        
        self.tabs = QTabWidget()
        
        self.text_view = TextViewPanel()
        self.grid_view = XmlGridPanel()
        
        self.tabs.addTab(self.text_view, "Text View")
        self.tabs.addTab(self.grid_view, "Grid View")
        
        layout.addWidget(self.tabs)
