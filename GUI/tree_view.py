"""
GUI/tree_view.py — XML Tree Panel

Provides the XmlTreePanel widget containing a QTreeView with a demo
QStandardItemModel.  The model is a placeholder that will be replaced
by the real XML tree model from XML/tree_model.py.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTreeView, QSizePolicy,
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

        # Placeholder demo model
        self._demo_model = self._create_demo_model()
        self.tree_view.setModel(self._demo_model)
        self.tree_view.expandAll()

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
    # Placeholder model
    # ------------------------------------------------------------------
    @staticmethod
    def _create_demo_model() -> QStandardItemModel:
        """Build a small demo tree to visualize the layout."""
        model = QStandardItemModel()

        mono = QFont("Cascadia Code", 12)
        mono.setStyleHint(QFont.StyleHint.Monospace)

        def _item(text):
            item = QStandardItem(text)
            item.setFont(mono)
            item.setEditable(False)
            return item

        root = _item("orders")
        order1 = _item("order")
        order1.appendRows([
            _item("customer"),
            _item("items"),
            _item("total"),
        ])
        order2 = _item("order")
        order2.appendRows([
            _item("customer"),
            _item("items"),
            _item("total"),
        ])
        root.appendRows([order1, order2])
        model.appendRow(root)

        return model
