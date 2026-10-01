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

        # Load actual XML model
        self._xml_model = self._load_xml_model()
        self.tree_view.setModel(self._xml_model)

        layout.addWidget(self.tree_view)

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
    def _load_xml_model() -> QStandardItemModel:
        """Parse sample.xml and build a QStandardItemModel."""
        model = QStandardItemModel()
        
        mono = QFont("Cascadia Code", 12)
        mono.setStyleHint(QFont.StyleHint.Monospace)

        def _create_item(text):
            item = QStandardItem(text)
            item.setFont(mono)
            item.setEditable(False)
            return item

        sample_path = Path(__file__).resolve().parent.parent / "sample.xml"
        
        try:
            tree = etree.parse(str(sample_path))
            xml_root = tree.getroot()

            def _recursive_build(xml_el):
                node = _create_item(xml_el.tag)
                for child in xml_el:
                    child_node = _recursive_build(child)
                    node.appendRow(child_node)
                return node

            parsed_root = _recursive_build(xml_root)
            model.appendRow(parsed_root)
            
        except Exception as e:
            # Fallback if something fails
            err_item = _create_item(f"Error loading XML: {e}")
            model.appendRow(err_item)

        return model
