"""
GUI/tree_view.py — XML Tree Panel

Provides the XmlTreePanel widget containing a QTreeView with a demo
QStandardItemModel.  The model is a placeholder that will be replaced
by the real XML tree model from XML/tree_model.py.
"""

from pathlib import Path
from lxml import etree
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTreeView, QSizePolicy, QMessageBox
)
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QFont, QIcon
from PyQt6.QtCore import Qt


class XmlTreePanel(QWidget):
    """Left-side panel: hierarchical XML tree view."""

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
        header = QLabel("XML TREE")
        header.setObjectName("panelHeader")
        layout.addWidget(header)

        # QTreeView — will later be connected to XML/tree_model.py
        self.tree_view = QTreeView()
        self.tree_view.setHeaderHidden(True)
        self.tree_view.setAnimated(True)
        self.tree_view.setIndentation(20)
        self.tree_view.setExpandsOnDoubleClick(True)
        self.tree_view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        # Empty model initially
        self._xml_model = QStandardItemModel()
        self.tree_view.setModel(self._xml_model)

        layout.addWidget(self.tree_view)

    def load_xml(self, filepath: str):
        """Load XML from filepath and update the tree model."""
        new_model = self._build_xml_model(filepath)
        self._xml_model = new_model
        self.tree_view.setModel(self._xml_model)

    # ------------------------------------------------------------------
    # Public API — for future integration
    # ------------------------------------------------------------------
    def set_model(self, model):
        """Replace the placeholder model with a real XML tree model."""
        self.tree_view.setModel(model)

    def get_tree_view(self) -> QTreeView:
        """Return the underlying QTreeView for external connections."""
        return self.tree_view

    # ------------------------------------------------------------------
    # XML loading
    # ------------------------------------------------------------------
    @staticmethod
    def _build_xml_model(filepath: str) -> QStandardItemModel:
        """Parse given XML file and build a QStandardItemModel."""
        model = QStandardItemModel()
        
        mono = QFont("Cascadia Code", 12)
        mono.setStyleHint(QFont.StyleHint.Monospace)

        def _create_item(text):
            item = QStandardItem(text)
            item.setFont(mono)
            item.setEditable(False)
            return item

        tree = etree.parse(filepath)
        xml_root = tree.getroot()

        def _recursive_build(xml_el):
            if isinstance(xml_el.tag, str):
                qname = etree.QName(xml_el)
                display_tag = f"{xml_el.prefix}:{qname.localname}" if xml_el.prefix else qname.localname
            else:
                display_tag = str(xml_el.tag)
                
            node = _create_item(display_tag)
            for child in xml_el:
                child_node = _recursive_build(child)
                node.appendRow(child_node)
            return node

        parsed_root = _recursive_build(xml_root)
        model.appendRow(parsed_root)

        return model
