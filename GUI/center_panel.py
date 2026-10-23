"""
GUI/center_panel.py — Center Tabbed Area
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTabWidget, QLabel
)
from PyQt6.QtCore import pyqtSignal

from GUI.grid_view import XmlGridPanel
from GUI.text_editor import XmlTextEditor
from GUI.tree_view import XmlTreePanel

class TextViewPanel(QWidget):
    xml_content_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        self.text_edit = XmlTextEditor()
        self.text_edit.xml_content_changed.connect(self.xml_content_changed.emit)
        layout.addWidget(self.text_edit)

    def load_xml(self, filepath: str):
        """Load the XML file text into the editor."""
        with open(filepath, "r", encoding="utf-8-sig") as f:
            self.text_edit.setPlainText(f.read())

    def set_xml_text(self, text: str):
        """Set in-memory XML text into the editor."""
        self.text_edit.setPlainText(text)

    def get_xml_text(self) -> str:
        """Get the current XML text from the editor."""
        return self.text_edit.toPlainText()

    def navigate_to_element(self, search_result):
        """Navigate to element or value in Text View."""
        self.text_edit.navigate_to_element(search_result)

    def clear_search_highlight(self) -> bool:
        """Clear active search highlight in Text View."""
        return self.text_edit.clear_search_highlight()

class CenterPanel(QWidget):
    """Center panel holding Text View, Grid View, and Tree View tabs."""
    xml_content_changed = pyqtSignal(str)

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
        self.text_view.xml_content_changed.connect(self.xml_content_changed.emit)
        self.grid_view = XmlGridPanel()
        self.tree_view = XmlTreePanel()
        
        self.tabs.addTab(self.text_view, "Text View")
        self.tabs.addTab(self.grid_view, "Grid View")
        self.tabs.addTab(self.tree_view, "Tree View")
        self.tabs.setCurrentWidget(self.grid_view)
        
        self._active_search_result = None
        self._is_syncing_tab = False
        self.tabs.currentChanged.connect(self._on_tab_changed)

        layout.addWidget(self.tabs)

    def load_xml(self, filepath: str, xml_root=None):
        """Distribute the filepath to child panels."""
        if xml_root is not None:
            self.grid_view.load_xml(filepath, xml_root)
            self.tree_view.load_xml(filepath, xml_root)
        else:
            self.grid_view.load_xml(filepath)
            self.tree_view.load_xml(filepath)
        self.text_view.load_xml(filepath)

    def load_xml_from_text(self, xml_text: str, xml_root=None, source_name: str = ""):
        """Distribute in-memory XML text and root to child panels."""
        if xml_root is not None:
            self.grid_view.load_xml(source_name, xml_root)
            self.tree_view.load_xml(source_name, xml_root)
        else:
            self.grid_view.load_xml(source_name)
            self.tree_view.load_xml(source_name)
        if self.text_view.get_xml_text() != xml_text:
            self.text_view.set_xml_text(xml_text)

    def revert_text(self, text: str):
        """Revert text in the editor without triggering changes."""
        self.text_view.set_xml_text(text)

    def set_active_search_result(self, search_result):
        """Store the active search result without altering tabs or views."""
        self._active_search_result = search_result

    def _focus_current_view_on_active_result(self):
        """Focus the currently selected tab on the saved active search result."""
        if self._active_search_result is None:
            return
        current = self.tabs.currentWidget()
        if current == self.text_view:
            self.text_view.navigate_to_element(self._active_search_result)
        elif current == self.grid_view:
            self.grid_view.navigate_to_element(self._active_search_result)

    def navigate_to_element(self, search_result):
        """Navigate to the element in the currently active view, falling back to Grid View from Tree View."""
        self.set_active_search_result(search_result)
        current = self.tabs.currentWidget()
        if current == self.tree_view:
            self._is_syncing_tab = True
            try:
                self.tabs.setCurrentWidget(self.grid_view)
            finally:
                self._is_syncing_tab = False
            self.grid_view.navigate_to_element(search_result)
        else:
            self._focus_current_view_on_active_result()

    def _on_tab_changed(self, index: int):
        """Synchronize active search result when switching tabs without re-triggering search or index changes."""
        if self._is_syncing_tab or self._active_search_result is None:
            return
        self._is_syncing_tab = True
        try:
            self._focus_current_view_on_active_result()
        finally:
            self._is_syncing_tab = False

    def clear_search_highlight(self) -> bool:
        """Clear active search highlight in both Grid View and Text View."""
        self._active_search_result = None
        grid_cleared = self.grid_view.clear_highlight()
        text_cleared = self.text_view.clear_search_highlight()
        return grid_cleared or text_cleared
