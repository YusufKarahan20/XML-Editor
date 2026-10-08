"""
GUI/center_panel.py — Center Tabbed Area
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTabWidget, QLabel
)

from GUI.grid_view import XmlGridPanel
from GUI.text_editor import XmlTextEditor
from GUI.tree_view import XmlTreePanel

class TextViewPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        self.text_edit = XmlTextEditor()
        
        layout.addWidget(self.text_edit)

    def load_xml(self, filepath: str):
        """Load the XML file text into the editor."""
        with open(filepath, "r", encoding="utf-8") as f:
            self.text_edit.setPlainText(f.read())

class CenterPanel(QWidget):
    """Center panel holding Text View, Grid View, and Tree View tabs."""
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
        self.tree_view = XmlTreePanel()
        
        self.tabs.addTab(self.text_view, "Text View")
        self.tabs.addTab(self.grid_view, "Grid View")
        self.tabs.addTab(self.tree_view, "Tree View")
        
        layout.addWidget(self.tabs)

    def load_xml(self, filepath: str, xml_root=None):
        """Distribute the filepath to child panels."""
        self.text_view.load_xml(filepath)
        if xml_root is not None:
            self.grid_view.load_xml(filepath, xml_root)
        else:
            self.grid_view.load_xml(filepath)
        self.tree_view.load_xml(filepath)

    def navigate_to_element(self, search_result):
        """Navigate to the element in Grid View."""
        self.tabs.setCurrentWidget(self.grid_view)
        self.grid_view.navigate_to_element(search_result)
