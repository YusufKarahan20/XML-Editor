"""
GUI/grid_view.py — Advanced XML Grid Panel Prototype

Implements Altova XMLSpy-style Grid View:
1. Normal Nested Grid: Container widgets for non-repeating elements.
2. Table Display: Grid/Table layout for repeating elements.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from lxml import etree

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame,
    QGridLayout, QPushButton, QSizePolicy, QMessageBox, QLineEdit,
    QMenu, QDialog, QApplication
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QUndoStack, QUndoCommand


def _is_node_editable(node: _GridNode, session: Optional[object] = None) -> bool:
    """
    Validates whether a grid node is safely editable.
    Requirements:
      1. Valid XML session draft (if session provided, is_draft_valid must be True).
      2. Must be an element node (node_type == 'element') or element_text (node_type == 'element_text').
      3. Not a namespace declaration, prefix, or synthetic non-leaf node (@xmlns, xmlns:*).
      4. Must have an associated lxml element with a valid tag string.
      5. Must not have any child elements in DOM (len(lxml_element) == 0).
      6. Must not have any child nodes in Grid model (len(node.children) == 0).
      7. Must not have meaningful tail text (mixed content).
    """
    if session is not None and hasattr(session, "is_draft_valid") and not session.is_draft_valid:
        return False
    if node is None:
        return False

    # Leaf text node under local namespace declaration element
    if getattr(node, "node_type", "element") == "element_text":
        if node.lxml_element is None or not isinstance(node.lxml_element.tag, str):
            return False
        if len(node.lxml_element) > 0:
            return False
        return True

    if getattr(node, "node_type", "element") != "element":
        return False
    if node.element in ("@xmlns", "Text") or node.element.startswith("xmlns:") or node.element == "xmlns":
        return False
    if node.lxml_element is None or not isinstance(node.lxml_element.tag, str):
        return False
    if len(node.lxml_element) > 0:
        return False
    if len(node.children) > 0:
        return False
    if node.lxml_element.tail and node.lxml_element.tail.strip():
        return False
    return True


class SetNodeTextCommand(QUndoCommand):
    """Command for updating the text value of a leaf node in Grid View."""

    def __init__(
        self,
        node: _GridNode,
        element: etree._Element,
        old_text: Optional[str],
        new_text: str,
        session: Optional[object],
        grid_panel: XmlGridPanel,
        old_node_val: str,
    ):
        super().__init__()
        self.node = node
        self.element = element
        self.old_text = old_text
        self.new_text = new_text
        self.session = session
        self.grid_panel = grid_panel
        self.old_node_val = old_node_val
        self._failed = False
        self.setText(f"Edit {node.element}: {new_text}")

    def redo(self):
        if self._failed or self.grid_panel.is_undo_redo_blocked():
            return

        # 1. Update live DOM element
        self.element.text = self.new_text

        # 2. Update document session draft
        if self.session is not None:
            success = self.session.update_from_dom()
            if not success:
                # Rollback DOM
                self.element.text = self.old_text
                rollback_success = True
                if self.session is not None:
                    rollback_success = self.session.update_from_dom()

                self._failed = True
                self._critical_failure = not rollback_success
                if not rollback_success:
                    self.grid_panel.lock_undo_redo(
                        "KRİTİK HATA: Değer güncelleme serileştirme hatası aldı ve DOM rollback işlemi de başarısız oldu. "
                        "Belge oturumu tutarsız olabilir. Lütfen belgenizi derhal farklı kaydedin veya yeniden yükleyin.",
                        critical=True
                    )
                else:
                    self.grid_panel.lock_undo_redo(
                        "İşlem Hatası: Düğüm değeri güncellenirken serileştirme hatası oluştu. Değişiklik geri alındı. "
                        "Geçmiş bütünlüğünü korumak için Grid Undo/Redo kilitlendi.",
                        critical=False
                    )
                self.grid_panel._rebuild()
                return

        # 3. Update Grid model
        self.node.value = self.new_text

        # 4. Filter check, signals, and view refresh
        self.grid_panel._on_node_text_updated(self.node, self.old_node_val, self.new_text)

    def undo(self):
        if self._failed or self.grid_panel.is_undo_redo_blocked():
            return

        # 1. Rollback DOM element (preserving None)
        self.element.text = self.old_text

        # 2. Update document session draft
        if self.session is not None:
            success = self.session.update_from_dom()
            if not success:
                # Rollback DOM to new_text so state is not half-applied
                self.element.text = self.new_text
                rollback_success = True
                if self.session is not None:
                    rollback_success = self.session.update_from_dom()

                self._failed = True
                self._critical_failure = not rollback_success
                if not rollback_success:
                    self.grid_panel.lock_undo_redo(
                        "KRİTİK HATA: Geri alma serileştirme hatası aldı ve DOM rollback işlemi de başarısız oldu. "
                        "Belge oturumu tutarsız olabilir. Lütfen belgenizi derhal farklı kaydedin veya yeniden yükleyin.",
                        critical=True
                    )
                else:
                    self.grid_panel.lock_undo_redo(
                        "Geri Alma Hatası: Geri alma sırasında serileştirme hatası oluştu. Durum korundu. "
                        "Geçmiş bütünlüğünü korumak için Grid Undo/Redo kilitlendi.",
                        critical=False
                    )
                self.grid_panel._rebuild()
                return

        # 3. Update Grid model
        self.node.value = self.old_node_val

        # 4. Filter check, signals, and view refresh
        self.grid_panel._on_node_text_updated(self.node, self.new_text, self.old_node_val)


class GridCellEditor(QLineEdit):
    """Inline cell editor for editable leaf nodes in Grid View."""

    def __init__(self, node: _GridNode, grid_panel: XmlGridPanel, original_label: Optional[QLabel], parent=None):
        super().__init__(node.value or "", parent)
        self.node = node
        self.grid_panel = grid_panel
        self.original_label = original_label
        self._is_committing = False

        self.setStyleSheet("""
            QLineEdit {
                background: #ffffff;
                color: #1e1e1e;
                border: 2px solid #0066cc;
                border-radius: 2px;
                padding: 1px 4px;
                font-family: inherit;
                font-size: 11px;
            }
        """)
        self.selectAll()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.commit()
            event.accept()
        elif event.key() == Qt.Key.Key_Escape:
            self.cancel()
            event.accept()
        else:
            super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        if not self._is_committing:
            self.commit()

    def commit(self):
        if self._is_committing:
            return
        self._is_committing = True
        new_val = self.text()
        self.grid_panel.commit_cell_edit(self.node, new_val, self.original_label, self)

    def cancel(self):
        if self._is_committing:
            return
        self._is_committing = True
        self.grid_panel.cancel_cell_edit(self.original_label, self)


def get_element_xml_path(element: Optional[etree._Element], expected_root: Optional[etree._Element] = None) -> str:
    """
    Constructs a canonical, human-readable XML path for the given lxml element.
    Format: /Root/Customer/Address/City
    If identical tag siblings (same expanded tag in DOM) exist under a parent,
    a 1-based index is appended: e.g. /Root/Orders/Order[2]/Items/Item[1]/Price
    Namespace prefixes are preserved using the element's bound prefix if present:
    e.g. /soapenv:Envelope/soapenv:Body/ns1:Order
    If expected_root is provided, verifies that element is connected to expected_root.
    Returns empty string if element is None, detached from expected_root, or invalid.
    """
    if element is None or not isinstance(getattr(element, "tag", None), str):
        return ""

    path_parts = []
    current = element
    reached_root = False

    while current is not None and isinstance(getattr(current, "tag", None), str):
        # 1. Resolve display tag with prefix if bound, else localname
        qname = etree.QName(current)
        display_tag = f"{current.prefix}:{qname.localname}" if current.prefix else qname.localname

        # 2. Sibling indexing: only compare elements with the EXACT same expanded tag (child.tag == current.tag)
        parent = current.getparent()
        if parent is not None:
            same_tag_siblings = [
                ch for ch in parent
                if isinstance(getattr(ch, "tag", None), str) and ch.tag == current.tag
            ]
            if len(same_tag_siblings) > 1:
                index = same_tag_siblings.index(current) + 1
                display_tag = f"{display_tag}[{index}]"

        path_parts.append(display_tag)

        if expected_root is not None and current is expected_root:
            reached_root = True
            break

        current = parent

    if expected_root is not None and not reached_root:
        return ""

    path_parts.reverse()
    return "/" + "/".join(path_parts) if path_parts else ""


class XmlPathDialog(QDialog):
    """Small and clean modal dialog displaying the canonical XML Path with copy-to-clipboard."""

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self.setWindowTitle("XML Path")
        self.setModal(True)
        self.setMinimumWidth(480)
        self.setFixedHeight(140)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        header_layout = QHBoxLayout()
        header_label = QLabel("XML Element Path:")
        header_label.setStyleSheet("font-weight: 600; color: #333333; font-size: 12px;")
        header_layout.addWidget(header_label)

        self.copied_feedback = QLabel("")
        self.copied_feedback.setStyleSheet("color: #2e7d32; font-weight: bold; font-size: 11px;")
        header_layout.addStretch()
        header_layout.addWidget(self.copied_feedback)
        layout.addLayout(header_layout)

        self.path_edit = QLineEdit(path, self)
        self.path_edit.setReadOnly(True)
        self.path_edit.setCursorPosition(0)
        self.path_edit.setStyleSheet("""
            QLineEdit {
                background: #f8f9fa;
                border: 1px solid #ced4da;
                border-radius: 3px;
                padding: 6px 8px;
                font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
                font-size: 12px;
                color: #212529;
            }
            QLineEdit:focus {
                border-color: #80bdff;
                background: #ffffff;
            }
        """)
        layout.addWidget(self.path_edit)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.copy_btn = QPushButton("Kopyala", self)
        self.copy_btn.setFixedWidth(90)
        self.copy_btn.setStyleSheet("""
            QPushButton {
                background-color: #0066cc;
                color: white;
                border: none;
                border-radius: 3px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #0052a3;
            }
            QPushButton:pressed {
                background-color: #003d7a;
            }
        """)
        self.copy_btn.clicked.connect(self._copy_to_clipboard)
        btn_layout.addWidget(self.copy_btn)

        self.close_btn = QPushButton("Kapat", self)
        self.close_btn.setFixedWidth(80)
        self.close_btn.setStyleSheet("""
            QPushButton {
                background-color: #f1f3f4;
                color: #333333;
                border: 1px solid #dadce0;
                border-radius: 3px;
                padding: 5px 12px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #e8eaed;
            }
            QPushButton:pressed {
                background-color: #dadce0;
            }
        """)
        self.close_btn.clicked.connect(self._on_close)
        btn_layout.addWidget(self.close_btn)

        layout.addLayout(btn_layout)

    def _on_close(self):
        self.close()

    def _copy_to_clipboard(self):
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(self.path)
            self.copied_feedback.setText("✓ Panoya kopyalandı")


class GridCellLabel(QLabel):
    """Clickable and editable label for leaf node values in Grid View."""

    def __init__(self, node: _GridNode, grid_panel: Optional[XmlGridPanel], parent=None):
        super().__init__(node.value or "", parent)
        self.node = node
        self.grid_panel = grid_panel
        self.setStyleSheet("QLabel { background: transparent; border: none; color: #1e1e1e; }")
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        session = getattr(grid_panel, "doc_session", None) if grid_panel else None
        if _is_node_editable(node, session):
            self.setToolTip("Değeri düzenlemek için çift tıklayın veya F2 tuşuna basın. Değeri silmek için Delete tuşuna basın.")
            self.setCursor(Qt.CursorShape.IBeamCursor)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.grid_panel:
            session = getattr(self.grid_panel, "doc_session", None)
            if _is_node_editable(self.node, session):
                self.grid_panel.start_cell_edit(self.node, self)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if self.grid_panel:
            session = getattr(self.grid_panel, "doc_session", None)
            if event.key() == Qt.Key.Key_F2:
                if _is_node_editable(self.node, session):
                    self.grid_panel.start_cell_edit(self.node, self)
                    event.accept()
                    return
            elif event.key() == Qt.Key.Key_Delete:
                if _is_node_editable(self.node, session):
                    self.grid_panel.commit_delete_value(self.node, self)
                    event.accept()
                    return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        if not self.grid_panel:
            super().contextMenuEvent(event)
            return

        session = getattr(self.grid_panel, "doc_session", None)
        menu = QMenu(self.grid_panel if self.grid_panel is not None else self)
        edit_act = None
        del_val_act = None

        if _is_node_editable(self.node, session):
            edit_act = menu.addAction("Değeri Düzenle (F2)")
            del_val_act = menu.addAction("Değeri Sil / Boşalt (Delete)")

        show_path_act = menu.addAction("Path'i Göster")

        if not menu.actions():
            super().contextMenuEvent(event)
            return

        action = menu.exec(event.globalPos())
        if action == edit_act:
            self.grid_panel.start_cell_edit(self.node, self)
        elif action == del_val_act:
            self.grid_panel.commit_delete_value(self.node, self)
        elif action == show_path_act:
            self.show_path_dialog()

    def show_path_dialog(self):
        element = getattr(self.node, "lxml_element", None)
        expected_root = None
        if self.grid_panel is not None:
            expected_root = getattr(self.grid_panel, "_current_root", None)
            if expected_root is None:
                session = getattr(self.grid_panel, "doc_session", None)
                if session is not None:
                    expected_root = getattr(session, "dom_root", None)

        path = get_element_xml_path(element, expected_root=expected_root)
        if not path:
            QMessageBox.warning(
                self.grid_panel if self.grid_panel is not None else self,
                "XML Path",
                "Seçilen hücre için geçerli bir XML path bilgisi üretilemedi."
            )
            return

        dlg = XmlPathDialog(path, parent=self.grid_panel if self.grid_panel is not None else self)
        dlg.exec()


class GridElementLabel(QLabel):
    """Read-only selectable label for XML element tags in Grid View."""

    def __init__(self, node: _GridNode, grid_panel: Optional[XmlGridPanel], parent=None):
        super().__init__(f"<b>{node.element}</b>", parent)
        self.node = node
        self.grid_panel = grid_panel
        self.setStyleSheet("QLabel { background: transparent; border: none; color: #1e1e1e; }")
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        if not self.grid_panel:
            super().contextMenuEvent(event)
            return

        menu = QMenu(self.grid_panel if self.grid_panel is not None else self)
        copy_act = menu.addAction("Kopyala")
        select_all_act = menu.addAction("Tümünü Seç")
        menu.addSeparator()
        show_path_act = menu.addAction("Path'i Göster")

        action = menu.exec(event.globalPos())
        if action == copy_act:
            self.copy_text()
        elif action == select_all_act:
            self.select_all_text()
        elif action == show_path_act:
            self.show_path_dialog()

    def copy_text(self):
        text_to_copy = self.selectedText() if self.hasSelectedText() else self.node.element
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(text_to_copy)

    def select_all_text(self):
        self.setFocus()
        self.setSelection(0, len(self.node.element))

    def show_path_dialog(self):
        element = getattr(self.node, "lxml_element", None)
        expected_root = None
        if self.grid_panel is not None:
            expected_root = getattr(self.grid_panel, "_current_root", None)
            if expected_root is None:
                session = getattr(self.grid_panel, "doc_session", None)
                if session is not None:
                    expected_root = getattr(session, "dom_root", None)

        path = get_element_xml_path(element, expected_root=expected_root)
        if not path:
            QMessageBox.warning(
                self.grid_panel if self.grid_panel is not None else self,
                "XML Path",
                "Seçilen element için geçerli bir XML path bilgisi üretilemedi."
            )
            return

        dlg = XmlPathDialog(path, parent=self.grid_panel if self.grid_panel is not None else self)
        dlg.exec()


class _GridNode:
    """Lightweight node holding element name, optional value, and children."""
    __slots__ = ("element", "value", "children", "is_expanded", "lxml_element", "node_type")

    def __init__(self, element: str, value: str = "", children: list[_GridNode] | None = None, node_type: str = "element"):
        self.element = element
        self.value = value
        self.children = children or []
        self.is_expanded = False
        self.lxml_element = None
        self.node_type = node_type

    def append_child(self, child: _GridNode) -> _GridNode:
        self.children.append(child)
        return child


class CommentWidget(QFrame):
    """Renders an XML comment with Altova XMLSpy-style green styling."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("commentWidget")
        self.setStyleSheet("""
            QFrame#commentWidget {
                background-color: #f6fcf6;
                border: 1px solid #c8e6c9;
                border-radius: 3px;
            }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        badge = QLabel("Comment")
        badge.setStyleSheet("color: #2e7d32; font-weight: bold; font-size: 11px; background: transparent;")

        comment_label = QLabel(text)
        comment_label.setStyleSheet("color: #1b5e20; font-family: monospace; font-size: 11px; background: transparent;")
        comment_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        comment_label.setWordWrap(True)

        layout.addWidget(badge)
        layout.addWidget(comment_label)
        layout.addStretch()


def _get_consecutive_chunks(children: list[_GridNode]) -> list[list[_GridNode]]:
    """Groups consecutive child nodes with the same tag into chunks.
    Comments and nodes with differing tags each form separate chunks.
    @xmlns nodes are excluded as they are rendered separately.
    """
    chunks: list[list[_GridNode]] = []
    current_chunk: list[_GridNode] = []

    for c in children:
        if c.element == "@xmlns":
            continue
        if getattr(c, "node_type", "element") == "comment":
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = []
            chunks.append([c])
        else:
            if not current_chunk:
                current_chunk.append(c)
            elif (getattr(current_chunk[0], "node_type", "element") == "element"
                  and current_chunk[0].element == c.element):
                current_chunk.append(c)
            else:
                chunks.append(current_chunk)
                current_chunk = [c]

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


# =====================================================================
#  Widget Components
# =====================================================================
class HeaderWidget(QWidget):
    """Clickable header for the ExpandableFrame."""
    def __init__(self, node: _GridNode, toggle_callback, grid_panel: Optional[XmlGridPanel] = None, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.node = node
        self.toggle_callback = toggle_callback
        self.grid_panel = grid_panel
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)
        
        self.toggle_btn = QPushButton("-" if node.is_expanded else "+")
        self.toggle_btn.setFixedSize(16, 16)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setStyleSheet("""
            QPushButton {
                border: 1px solid #a0a0a0;
                background: #f0f0f0;
                font-family: monospace;
                font-weight: bold;
                font-size: 11px;
                padding: 0px;
                border-radius: 2px;
                color: #1e1e1e;
            }
            QPushButton:hover {
                background: #e0e0e0;
                border: 1px solid #808080;
            }
        """)
        self.toggle_btn.clicked.connect(self.toggle_callback)
        
        self.title_label = GridElementLabel(node, grid_panel)
        self.title_label.setStyleSheet("QLabel { font-weight: bold; color: #1e1e1e; font-size: 12px; background: transparent; }")
        
        layout.addWidget(self.toggle_btn)
        layout.addWidget(self.title_label)
        layout.addStretch()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_callback()
            event.accept()

class ExpandableFrame(QFrame):
    """A generic frame that can be expanded/collapsed with Altova XMLSpy-style border and header."""

    def __init__(self, node: _GridNode, on_rebuild, grid_panel=None, parent=None):
        super().__init__(parent)
        self.node = node
        self.on_rebuild = on_rebuild
        self.grid_panel = grid_panel
        
        self.setObjectName("expandableFrame")
        self.setStyleSheet("""
            QFrame#expandableFrame {
                border: 1px solid #d4d8dc;
                border-radius: 3px;
                background-color: #ffffff;
            }
        """)
        
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        
        # Header
        self.header_widget = HeaderWidget(node, self.toggle, grid_panel)
        self.header_widget.setObjectName("expandableHeader")
        if node.is_expanded:
            self.header_widget.setStyleSheet("""
                QWidget#expandableHeader {
                    background-color: #f1f4f8;
                    border-bottom: 1px solid #d4d8dc;
                    border-top-left-radius: 3px;
                    border-top-right-radius: 3px;
                }
            """)
        else:
            self.header_widget.setStyleSheet("""
                QWidget#expandableHeader {
                    background-color: #f8fafc;
                    border-radius: 3px;
                }
            """)
        self.main_layout.addWidget(self.header_widget)

        # Content is ONLY created if expanded (data-driven view)
        if node.is_expanded:
            self.content_widget = QWidget()
            self.content_widget.setObjectName("expandableContent")
            self.content_widget.setStyleSheet("QWidget#expandableContent { background-color: #ffffff; border-bottom-left-radius: 3px; border-bottom-right-radius: 3px; }")
            self.content_layout = QVBoxLayout(self.content_widget)
            self.content_layout.setContentsMargins(10, 6, 10, 6)
            self.content_layout.setSpacing(6)
            self.main_layout.addWidget(self.content_widget)
        else:
            self.content_layout = None
        
    def toggle(self):
        self.node.is_expanded = not self.node.is_expanded
        self.on_rebuild()
        
    def add_widget(self, widget: QWidget):
        if self.content_layout is not None:
            self.content_layout.addWidget(widget)


class TableDisplayWidget(QWidget):
    """Renders repeating XML elements as a nested table."""

    def __init__(self, element_name: str, items: list[_GridNode], on_rebuild, grid_panel=None, parent=None):
        super().__init__(parent)
        self.on_rebuild = on_rebuild
        self.grid_panel = grid_panel
        self.items = items
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 4, 0, 4)
        main_layout.setSpacing(4)
        
        # Header for the table display with toggle button
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        self.toggle_btn = QPushButton("-" if items[0].is_expanded else "+")
        self.toggle_btn.setFixedSize(16, 16)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setStyleSheet("""
            QPushButton {
                border: 1px solid #a0a0a0;
                background: #f0f0f0;
                font-family: monospace;
                font-weight: bold;
                font-size: 11px;
                padding: 0px;
                border-radius: 2px;
                color: #1e1e1e;
            }
            QPushButton:hover {
                background: #e0e0e0;
                border: 1px solid #808080;
            }
        """)
        self.toggle_btn.clicked.connect(self.toggle)
        header_layout.addWidget(self.toggle_btn)
        
        lbl = QLabel(f"<b>{element_name} ({len(items)})</b>")
        lbl.setStyleSheet("color: #b8960c; font-size: 12px;")
        lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        lbl.mousePressEvent = lambda e: self.toggle() if e.button() == Qt.MouseButton.LeftButton else None
        
        header_layout.addWidget(lbl)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)
        
        # Görev 1: Eğer tablo collapsed ise içeriği (grid_frame) hiç oluşturma
        if not items[0].is_expanded:
            return
            
        # Görev 2: Tablo sütunlarını ve Namespace varlığını tespit et
        has_namespaces = False
        ns_header_title = "xmlns"
        
        columns = []
        for item in items:
            for child in item.children:
                if getattr(child, "node_type", "element") == "comment":
                    continue
                if child.element == "@xmlns":
                    has_namespaces = True
                    # Başlık için ilk bulduğumuz namespace'in prefix'ini örnek alabiliriz
                    if child.children and ns_header_title == "xmlns":
                        ns_header_title = child.children[0].element
                    continue
                    
                if child.element not in columns:
                    columns.append(child.element)
                    
        if not columns:
            columns = ["Value"]
            
        # Grid frame oluşturma
        grid_frame = QFrame()
        grid_frame.setObjectName("gridTableFrame")
        grid_frame.setStyleSheet("QFrame#gridTableFrame { background: #d8d8d8; border: 1px solid #c8c8c8; }")
        grid_layout = QGridLayout(grid_frame)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setSpacing(1)
        
        # Row number column header
        hdr = QLabel("")
        hdr.setStyleSheet("background: #e8e8e8; padding: 4px; border: none;")
        grid_layout.addWidget(hdr, 0, 0)
        
        col_offset = 1
        
        # Eğer namespace varsa ilk sütunu ekle
        if has_namespaces:
            ns_hdr = QLabel(f"<b>{ns_header_title}</b>")
            ns_hdr.setStyleSheet("background: #e8e8e8; padding: 4px; border: none;")
            grid_layout.addWidget(ns_hdr, 0, col_offset)
            col_offset += 1
        
        target_col_index = -1
        target_row_index = -1
        is_target_elem_in_table = False

        def _node_contains_element(node: _GridNode, el) -> bool:
            if node.lxml_element is el:
                return True
            for ch in node.children:
                if _node_contains_element(ch, el):
                    return True
            return False

        if self.grid_panel and self.grid_panel._target_search_result:
            target_el = self.grid_panel._target_search_result.element
            match_type = self.grid_panel._target_search_result.match_type
            if match_type == "Element":
                for r, item in enumerate(items):
                    for c, col_name in enumerate(columns):
                        for child in item.children:
                            if child.element == col_name and _node_contains_element(child, target_el):
                                target_col_index = c
                                target_row_index = r
                                is_target_elem_in_table = True
                                break
                        if is_target_elem_in_table:
                            break
                    if is_target_elem_in_table:
                        break

        # Normal çocuk kolonların başlıkları
        for c, col_name in enumerate(columns):
            hdr = QLabel(f"<b>{col_name}</b>")
            if is_target_elem_in_table and c == target_col_index:
                hdr.setStyleSheet("background-color: #fffacd; color: #1e1e1e; padding: 4px; border: 2px solid #e6b800; border-radius: 2px; font-weight: bold;")
                if self.grid_panel._target_widget is None:
                    self.grid_panel._target_widget = hdr
            else:
                hdr.setStyleSheet("background: #e8e8e8; padding: 4px; border: none;")
            grid_layout.addWidget(hdr, 0, col_offset + c)
            
        # Satırları oluşturma
        for r, item in enumerate(items):
            is_target_row = False
            is_target_val = False
            if self.grid_panel and self.grid_panel._target_search_result and item.lxml_element is self.grid_panel._target_search_result.element:
                if self.grid_panel._target_search_result.match_type == "Element":
                    is_target_row = True
                else:
                    is_target_val = True

            is_child_target_row = (is_target_elem_in_table and r == target_row_index)

            # Row number
            is_out_of_filter = bool(self.grid_panel and hasattr(self.grid_panel, "_out_of_filter_elements")
                                    and item.lxml_element in self.grid_panel._out_of_filter_elements)
            row_text = f"{r + 1} ⚠️" if is_out_of_filter else str(r + 1)
            row_lbl = QLabel(row_text)
            row_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignTop)
            if is_out_of_filter:
                row_lbl.setStyleSheet("background: #ffe0b2; padding: 6px; font-size: 11px; font-weight: bold; color: #b78103; border: 1px solid #ffb74d;")
                row_lbl.setToolTip("Bu kayıt düzenleme sonrasında aktif filtre kriterini karşılamamaktadır.")
            elif is_target_row or is_child_target_row:
                row_lbl.setStyleSheet("background: #fff8dc; padding: 6px; font-size: 11px; font-weight: bold; color: #1e1e1e; border: 2px solid #e6b800; border-right: none;")
                if self.grid_panel._target_widget is None:
                    self.grid_panel._target_widget = row_lbl
            else:
                row_lbl.setStyleSheet("background: #f0f0f0; padding: 6px; font-size: 11px; color: #888; border: none;")
            grid_layout.addWidget(row_lbl, r + 1, 0)
            
            current_col = 1
            
            # Namespace sütunu verisi (sadece lokal declaration varsa dolar)
            if has_namespaces:
                ns_node = next((ch for ch in item.children if ch.element == "@xmlns"), None)
                ns_val = ""
                if ns_node and ns_node.children:
                    lines = []
                    for nc in ns_node.children:
                        # Eğer birden fazla namespace varsa prefix=URI formatında göster,
                        # tekse sadece URI'yi göster ki hücre gereksiz kalabalık olmasın.
                        if len(ns_node.children) > 1:
                            lines.append(f"{nc.element}={nc.value}")
                        else:
                            lines.append(nc.value)
                    ns_val = "\n".join(lines)
                
                ns_lbl = QLabel(ns_val)
                ns_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                if is_target_row:
                    ns_lbl.setStyleSheet("background: #fffdf0; padding: 6px; border-top: 2px solid #e6b800; border-bottom: 2px solid #e6b800; color: #333;")
                else:
                    ns_lbl.setStyleSheet("background: #ffffff; padding: 6px; border: none; color: #333;")
                ns_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                grid_layout.addWidget(ns_lbl, r + 1, current_col)
                current_col += 1
            
            # Eğer elemanın ne normal çocukları ne de text'i yoksa veya
            # sadece @xmlns namespace'i ya da yorumu varsa (text'i olan/olmayan leaf):
            non_meta_children = [
                ch for ch in item.children
                if ch.element != "@xmlns" and getattr(ch, "node_type", "element") != "comment"
            ]
            if not non_meta_children:
                val_lbl = GridCellLabel(item, self.grid_panel) if self.grid_panel else QLabel(item.value)
                val_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                if is_out_of_filter:
                    val_lbl.setStyleSheet("background: #fff8e1; padding: 6px; border: 1px dashed #ffb74d; color: #333;")
                elif is_target_row:
                    val_lbl.setStyleSheet("background: #fffdf0; padding: 6px; border: 2px solid #e6b800;")
                elif is_target_val:
                    val_lbl.setStyleSheet("background: #fffacd; padding: 6px; border: none;")
                    if self.grid_panel:
                        self.grid_panel._target_widget = val_lbl
                else:
                    val_lbl.setStyleSheet("background: #ffffff; padding: 6px; border: none;")
                grid_layout.addWidget(val_lbl, r + 1, current_col)
            else:
                for c, col_name in enumerate(columns):
                    col_children = [ch for ch in item.children if ch.element == col_name]
                    
                    cell_widget = QWidget()
                    cell_widget.setObjectName("tableCellWidget")
                    is_specific_target_cell = (is_child_target_row and c == target_col_index)

                    if is_specific_target_cell:
                        cell_widget.setStyleSheet("QWidget#tableCellWidget { background: #fffdf0; border: 2px solid #e6b800; border-radius: 2px; }")
                        # If the cell's direct single child element is itself the target, target_widget points to cell_widget
                        if len(col_children) == 1 and col_children[0].lxml_element is target_el:
                            self.grid_panel._target_widget = cell_widget
                    elif is_target_row:
                        border_right = "border-right: 2px solid #e6b800;" if c == len(columns) - 1 else ""
                        cell_widget.setStyleSheet(f"QWidget#tableCellWidget {{ background: #fffdf0; border-top: 2px solid #e6b800; border-bottom: 2px solid #e6b800; {border_right} }}")
                    else:
                        cell_widget.setStyleSheet("QWidget#tableCellWidget { background: #ffffff; border: none; }")
                    cell_layout = QVBoxLayout(cell_widget)
                    cell_layout.setContentsMargins(6, 6, 6, 6)
                    cell_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
                    
                    if col_children:
                        if len(col_children) == 1:
                            child_node = col_children[0]
                            is_child_target_val = False
                            if self.grid_panel and self.grid_panel._target_search_result and child_node.lxml_element is self.grid_panel._target_search_result.element:
                                if self.grid_panel._target_search_result.match_type == "Value":
                                    is_child_target_val = True

                            if not child_node.children:
                                lbl = GridCellLabel(child_node, self.grid_panel) if self.grid_panel else QLabel(child_node.value)
                                if is_child_target_val:
                                    lbl.setStyleSheet("background-color: #fffacd; color: #1e1e1e; padding: 2px 4px; border-radius: 2px;")
                                    self.grid_panel._target_widget = lbl
                                elif is_out_of_filter:
                                    lbl.setStyleSheet("background-color: #fff8e1; color: #1e1e1e; padding: 2px 4px; border: 1px dashed #ffb74d; border-radius: 2px;")
                                else:
                                    lbl.setStyleSheet("QLabel { background: transparent; border: none; color: #1e1e1e; }")
                                cell_layout.addWidget(lbl)
                            else:
                                nested = render_node(child_node, self.on_rebuild, self.grid_panel)
                                cell_layout.addWidget(nested)
                        else:
                            nested = TableDisplayWidget(col_name, col_children, self.on_rebuild, self.grid_panel)
                            cell_layout.addWidget(nested)
                    
                    grid_layout.addWidget(cell_widget, r + 1, current_col + c)
                    
        main_layout.addWidget(grid_frame)

    def toggle(self):
        self.items[0].is_expanded = not self.items[0].is_expanded
        self.on_rebuild()


def render_node(node: _GridNode, on_rebuild, grid_panel=None) -> QWidget:
    """Recursively render a node as a widget."""
    if getattr(node, "node_type", "element") == "comment":
        return CommentWidget(node.value)

    if not node.children:
        w = QWidget()
        l = QHBoxLayout(w)
        l.setContentsMargins(4, 2, 4, 2)
        l.setSpacing(8)
        lbl_elem = GridElementLabel(node, grid_panel)
        lbl_elem.setStyleSheet("QLabel { background: transparent; border: none; color: #1e1e1e; }")
        lbl_val = GridCellLabel(node, grid_panel) if grid_panel else QLabel(node.value)
        if not isinstance(lbl_val, GridCellLabel):
            lbl_val.setStyleSheet("QLabel { background: transparent; border: none; color: #1e1e1e; }")
        l.addWidget(lbl_elem)
        l.addWidget(lbl_val)

        is_out = bool(grid_panel and hasattr(grid_panel, "_out_of_filter_elements")
                      and node.lxml_element in grid_panel._out_of_filter_elements)
        if is_out:
            badge = QLabel("⚠️ [Filtre Dışı]")
            badge.setStyleSheet("color: #b78103; font-size: 11px; font-weight: bold; background: transparent;")
            badge.setToolTip("Bu kayıt düzenleme sonrasında aktif filtre kriterini karşılamamaktadır.")
            l.addWidget(badge)

        l.addStretch()

        if grid_panel and grid_panel._target_search_result and node.lxml_element is grid_panel._target_search_result.element:
            res = grid_panel._target_search_result
            if res.match_type == "Value":
                lbl_val.setStyleSheet("background-color: #fffacd; color: #1e1e1e; padding: 1px 4px; border-radius: 2px;")
                grid_panel._target_widget = lbl_val
            else:
                lbl_elem.setStyleSheet("background-color: #fffacd; color: #1e1e1e; padding: 1px 4px; border-radius: 2px;")
                grid_panel._target_widget = lbl_elem

        return w
        
    frame = ExpandableFrame(node, on_rebuild, grid_panel)

    if grid_panel and grid_panel._target_search_result and node.lxml_element is grid_panel._target_search_result.element:
        if grid_panel._target_search_result.match_type == "Element":
            if node.is_expanded:
                frame.header_widget.setStyleSheet("""
                    QWidget#expandableHeader {
                        background-color: #fffacd;
                        border-bottom: 2px solid #e6b800;
                        border-top-left-radius: 3px;
                        border-top-right-radius: 3px;
                    }
                """)
                frame.setStyleSheet("""
                    QFrame#expandableFrame {
                        border: 2px solid #e6b800;
                        border-radius: 3px;
                        background-color: #ffffff;
                    }
                """)
            else:
                frame.header_widget.setStyleSheet("""
                    QWidget#expandableHeader {
                        background-color: #fffacd;
                        border: 2px solid #e6b800;
                        border-radius: 3px;
                    }
                """)
            grid_panel._target_widget = frame.header_widget
    
    if node.is_expanded:
        xmlns_node = next((c for c in node.children if c.element == "@xmlns"), None)
        if xmlns_node and xmlns_node.children:
            ns_widget = QWidget()
            ns_layout = QGridLayout(ns_widget)
            ns_layout.setContentsMargins(0, 0, 0, 4)
            ns_layout.setSpacing(0)
            
            for row, ns_child in enumerate(xmlns_node.children):
                lbl_pfx = QLabel(f"<b>{ns_child.element}</b>")
                lbl_pfx.setStyleSheet("background: #f0f0f0; border: 1px solid #d0d0d0; padding: 4px; color: #555;")
                lbl_uri = QLabel(ns_child.value)
                lbl_uri.setStyleSheet("background: #ffffff; border: 1px solid #d0d0d0; border-left: none; padding: 4px; color: #333;")
                lbl_uri.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                
                ns_layout.addWidget(lbl_pfx, row, 0)
                ns_layout.addWidget(lbl_uri, row, 1)
                
            frame.add_widget(ns_widget)

        chunks = _get_consecutive_chunks(node.children)
        for chunk in chunks:
            if not chunk:
                continue
            if getattr(chunk[0], "node_type", "element") == "comment":
                for c_node in chunk:
                    frame.add_widget(CommentWidget(c_node.value))
            elif len(chunk) == 1:
                frame.add_widget(render_node(chunk[0], on_rebuild, grid_panel))
            else:
                frame.add_widget(TableDisplayWidget(chunk[0].element, chunk, on_rebuild, grid_panel))
            
    return frame




# =====================================================================
#  XML Parser
# =====================================================================
def _parse_xml_to_grid(filepath: str, xml_root=None) -> _GridNode:
    root = _GridNode("")
    root.is_expanded = True
    
    if xml_root is None:
        tree = etree.parse(filepath)
        xml_root = tree.getroot()
    
    def _recursive_parse(xml_el, parent_nsmap=None):
        if not isinstance(xml_el.tag, str):
            return None

        if parent_nsmap is None:
            parent_nsmap = {}
            
        local_ns = {}
        for pfx, uri in xml_el.nsmap.items():
            if parent_nsmap.get(pfx) != uri:
                local_ns[pfx] = uri
                
        tag = xml_el.tag
        if tag.startswith("{"):
            local_name = tag.split("}", 1)[1]
        else:
            local_name = tag
            
        if xml_el.prefix:
            display_tag = f"{xml_el.prefix}:{local_name}"
        else:
            display_tag = local_name
            
        text_val = (xml_el.text or "").strip()
        node = _GridNode(display_tag, text_val)
        node.lxml_element = xml_el
        
        if local_ns:
            ns_node = _GridNode("@xmlns")
            ns_node.lxml_element = xml_el
            for pfx, uri in local_ns.items():
                pfx_str = f"xmlns:{pfx}" if pfx else "xmlns"
                ns_node.append_child(_GridNode(pfx_str, uri))
            node.append_child(ns_node)
            
            # If element is a leaf (no XML child elements), create editable Text node
            if len(xml_el) == 0:
                text_child = _GridNode("Text", text_val, node_type="element_text")
                text_child.lxml_element = xml_el
                node.append_child(text_child)
                node.value = ""
            elif text_val:
                node.append_child(_GridNode("Text", text_val))
                node.value = ""
                
        for child in xml_el:
            if not isinstance(child.tag, str):
                if child.tag == etree.Comment or callable(child.tag):
                    comment_text = (child.text or "").strip()
                    comment_node = _GridNode("Comment", value=comment_text, node_type="comment")
                    comment_node.lxml_element = child
                    node.append_child(comment_node)
                continue
            child_node = _recursive_parse(child, xml_el.nsmap)
            if child_node is not None:
                node.append_child(child_node)
        return node
        
    if xml_root.getparent() is None:
        prev_comments = []
        try:
            prev = xml_root.getprevious()
            while prev is not None:
                if prev.tag == etree.Comment or callable(prev.tag):
                    c_node = _GridNode("Comment", value=(prev.text or "").strip(), node_type="comment")
                    c_node.lxml_element = prev
                    prev_comments.append(c_node)
                prev = prev.getprevious()
        except Exception:
            pass
        for c_node in reversed(prev_comments):
            root.append_child(c_node)

    parsed_root = _recursive_parse(xml_root)
    if parsed_root is not None:
        root.append_child(parsed_root)

    if xml_root.getparent() is None:
        try:
            nxt = xml_root.getnext()
            while nxt is not None:
                if nxt.tag == etree.Comment or callable(nxt.tag):
                    c_node = _GridNode("Comment", value=(nxt.text or "").strip(), node_type="comment")
                    c_node.lxml_element = nxt
                    root.append_child(c_node)
                nxt = nxt.getnext()
        except Exception:
            pass
        
    return root

def _is_complex_node(node: _GridNode) -> bool:
    """Returns True if the node has structural children (not just xmlns, Text, or comments)."""
    for child in node.children:
        if getattr(child, "node_type", "element") == "comment":
            continue
        if child.element not in ("@xmlns", "Text"):
            return True
    return False

def _local_name(node: _GridNode) -> str:
    if getattr(node, "node_type", "element") == "comment":
        return ""
    if node.element in ("@xmlns", "Text"):
        return node.element
    loc = node.element.split(":", 1)[-1]
    if node.lxml_element is not None and isinstance(node.lxml_element.tag, str):
        t = node.lxml_element.tag
        loc = t.split("}", 1)[1] if "}" in t else t
    return loc

def _get_node_text(node: _GridNode) -> str:
    """
    Central helper to extract text value from a grid node for filtering.
    Prioritizes node.value, falling back to node.lxml_element.text or child Text nodes
    when local xmlns declarations cause node.value to be empty in the grid model.
    """
    if node is None or getattr(node, "node_type", "element") == "comment":
        return ""
    if node.value is not None and node.value.strip():
        return node.value.strip()
    if node.lxml_element is not None and node.lxml_element.text and node.lxml_element.text.strip():
        return node.lxml_element.text.strip()
    for child in node.children:
        if getattr(child, "node_type", "element") == "comment":
            continue
        if child.element == "Text" and child.value is not None and child.value.strip():
            return child.value.strip()
    if node.value is not None:
        return node.value.strip()
    return ""

def _check_condition(text: str, condition: str, value: str) -> bool:
    """Evaluates filter condition against text."""
    if condition == "Equals":
        return text == value
    elif condition == "Not Equals":
        return text != value
    elif condition == "Contains":
        return value in text
    elif condition == "Starts With":
        return text.startswith(value)
    elif condition == "Ends With":
        return text.endswith(value)
    return False


def _check_node_matches_subpath(node: _GridNode, subpath: list[str], condition: str, value: str) -> bool:
    """Checks whether the node or its descendants match the condition along the subpath."""
    if not subpath:
        return True

    level_name = subpath[0]
    has_condition = bool(condition and condition not in ("", "(Tümü / Filtresiz)", "(All / None)", "None") and value is not None and str(value).strip() != "")

    if len(subpath) == 1:
        for child in node.children:
            if _local_name(child) == level_name:
                if not has_condition:
                    return True
                text = _get_node_text(child)
                if _check_condition(text, condition, value):
                    return True
        return False

    for child in node.children:
        if _local_name(child) == level_name:
            if _check_node_matches_subpath(child, subpath[1:], condition, value):
                return True
    return False

def _expand_path_nodes(node: _GridNode, subpath: list[str]):
    """
    Recursively ensures that all nodes matching the subpath have is_expanded = True,
    including inside nested table rows or complex frames.
    """
    if not subpath:
        return

    # If the current node itself matches the start of subpath, expand it and advance
    if _local_name(node).strip() == subpath[0].strip():
        node.is_expanded = True
        remaining = subpath[1:]
    else:
        remaining = subpath

    if not remaining:
        return

    level_name = remaining[0].strip()
    next_remaining = remaining[1:]

    for child in node.children:
        if _local_name(child).strip() == level_name:
            child.is_expanded = True
            if next_remaining:
                _expand_path_nodes(child, next_remaining)

def _prune_grid_node(node: _GridNode, subpath: list[str], condition: str, value: str, inside_table_row: bool = False) -> bool:
    """
    Recursively prunes children of _GridNode along the subpath.
    In repeating table rows (lists), preserves all sibling columns of matching rows.
    In single non-repeating XML paths, prunes non-path sibling branches to focus the view.
    Inside a table row (record), preserves all sibling fields/properties of complex objects.
    Returns True if this node (or its valid descendants) matched and should be kept.
    """
    if not subpath:
        return True

    level_name = subpath[0]
    has_condition = bool(condition and condition not in ("", "(Tümü / Filtresiz)", "(All / None)", "None") and value is not None and str(value).strip() != "")

    matching_children = [c for c in node.children if _local_name(c) == level_name]
    is_table_rows = len(matching_children) > 1

    if is_table_rows:
        # Repeating table rows found at this level (e.g. ACTIVATED_ORDERS or PRODUCT_CHAR_VALUE)
        children_to_remove = []
        any_kept = False
        remaining_subpath = subpath[1:]

        for child in node.children:
            if _local_name(child) == level_name:
                # Check if this table row matches the condition on the remaining subpath
                matches = _check_node_matches_subpath(child, remaining_subpath, condition, value)
                if matches:
                    any_kept = True
                    child.is_expanded = True
                    # If this table row contains deeper subpaths, prune inner levels with inside_table_row=True
                    if remaining_subpath:
                        _prune_grid_node(child, remaining_subpath, condition, value, inside_table_row=True)
                else:
                    children_to_remove.append(child)
            else:
                if not inside_table_row:
                    if child.element not in ("@xmlns", "Text"):
                        children_to_remove.append(child)

        for child in children_to_remove:
            node.children.remove(child)

        if any_kept:
            node.is_expanded = True
        return any_kept

    # Non-repeating level (is_table_rows is False)
    if inside_table_row:
        # Inside a table row (record): preserve all sibling fields/properties of this object.
        # Only traverse down along the path to evaluate any deeper subpaths or leaf conditions.
        any_kept = False
        for child in node.children:
            if _local_name(child) == level_name:
                if len(subpath) > 1:
                    keep = _prune_grid_node(child, subpath[1:], condition, value, inside_table_row=True)
                    if keep:
                        any_kept = True
                        child.is_expanded = True
                else:
                    if has_condition:
                        text = _get_node_text(child)
                        if _check_condition(text, condition, value):
                            any_kept = True
                            child.is_expanded = True
                    else:
                        any_kept = True
                        child.is_expanded = True
        return any_kept

    # Outside table rows: single non-repeating XML path container (e.g. root -> ProcessChangeOrder -> ChangeOrder)
    if len(subpath) == 1:
        any_kept = False
        children_to_remove = []
        for child in node.children:
            if _local_name(child) == level_name:
                if has_condition:
                    text = _get_node_text(child)
                    if _check_condition(text, condition, value):
                        any_kept = True
                        child.is_expanded = True
                    else:
                        children_to_remove.append(child)
                else:
                    any_kept = True
                    child.is_expanded = True
            else:
                if child.element not in ("@xmlns", "Text"):
                    children_to_remove.append(child)

        for child in children_to_remove:
            node.children.remove(child)

        if any_kept:
            node.is_expanded = True
        return any_kept

    children_to_remove = []
    any_kept = False

    for child in node.children:
        if _local_name(child) == level_name:
            keep = _prune_grid_node(child, subpath[1:], condition, value, inside_table_row=False)
            if not keep:
                children_to_remove.append(child)
            else:
                any_kept = True
                child.is_expanded = True
        else:
            if child.element not in ("@xmlns", "Text"):
                children_to_remove.append(child)

    for child in children_to_remove:
        node.children.remove(child)

    if any_kept:
        node.is_expanded = True
    return any_kept

# =====================================================================
#  Main Panel Widget
# =====================================================================
class XmlGridPanel(QWidget):
    """Altova XMLSpy-style nested Grid View prototype."""

    node_value_changed = pyqtSignal(object, str, str)  # (node, old_value, new_value)
    document_modified = pyqtSignal()
    undo_available_changed = pyqtSignal(bool)
    redo_available_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc_session = None
        self.grid_undo_stack = QUndoStack(self)
        self.grid_undo_stack.canUndoChanged.connect(self._on_stack_can_undo_changed)
        self.grid_undo_stack.canRedoChanged.connect(self._on_stack_can_redo_changed)
        self._undo_redo_blocked: bool = False
        self._history_error_message: str = ""
        self._out_of_filter_elements: set[etree._Element] = set()
        self._active_filter_text: str = ""
        self._active_filter_path: list[str] | None = None
        self._active_filter_condition: str | None = None
        self._active_filter_value: str | None = None
        self._active_filtered_elements: list[etree._Element] | None = None

        self._target_search_result = None
        self._target_widget = None
        self._setup_ui()

    def set_document_session(self, session):
        self.doc_session = session

    def _on_stack_can_undo_changed(self, available: bool):
        if not self._undo_redo_blocked:
            self.undo_available_changed.emit(available)
        else:
            self.undo_available_changed.emit(False)

    def _on_stack_can_redo_changed(self, available: bool):
        if not self._undo_redo_blocked:
            self.redo_available_changed.emit(available)
        else:
            self.redo_available_changed.emit(False)

    def lock_undo_redo(self, message: str, critical: bool = False):
        """Locks Grid Undo/Redo when stack index and DOM state diverge or on rollback failure."""
        self._undo_redo_blocked = True
        self._history_error_message = message
        self.undo_available_changed.emit(False)
        self.redo_available_changed.emit(False)
        if critical:
            QMessageBox.critical(self, "Kritik Belge Hatası", message)
        else:
            QMessageBox.warning(self, "İşlem Uyarısı", message)

    def is_undo_redo_blocked(self) -> bool:
        return getattr(self, "_undo_redo_blocked", False)

    def can_undo(self) -> bool:
        if self._undo_redo_blocked:
            return False
        return self.grid_undo_stack.canUndo()

    def can_redo(self) -> bool:
        if self._undo_redo_blocked:
            return False
        return self.grid_undo_stack.canRedo()

    def undo(self):
        if self._undo_redo_blocked:
            QMessageBox.warning(
                self,
                "İşlem Engellendi",
                f"Grid Undo/Redo devre dışı bırakılmıştır:\n{self._history_error_message}"
            )
            return
        self.grid_undo_stack.undo()

    def redo(self):
        if self._undo_redo_blocked:
            QMessageBox.warning(
                self,
                "İşlem Engellendi",
                f"Grid Undo/Redo devre dışı bırakılmıştır:\n{self._history_error_message}"
            )
            return
        self.grid_undo_stack.redo()

    def _prepare_for_command_push(self) -> bool:
        """
        Prepares the undo stack before pushing a new command.
        If undo/redo is currently locked:
          - Verifies DOM and DocumentSession consistency (is_draft_valid).
          - Clears the old history from grid_undo_stack before the new command is pushed.
          - Clears the lock flag.
        Returns True if safe to proceed, False otherwise.
        """
        if self._undo_redo_blocked:
            session = getattr(self, "doc_session", None)
            if session is not None:
                if hasattr(session, "is_draft_valid") and not session.is_draft_valid:
                    QMessageBox.warning(
                        self,
                        "Kilitli Geçmiş",
                        "Belge oturumu geçersiz bir durumda olduğu için işlem gerçekleştirilemiyor."
                    )
                    return False
            # Clear old history prior to pushing new command
            self.grid_undo_stack.clear()
            self._undo_redo_blocked = False
            self._history_error_message = ""
        return True

    def _sync_demo_root_from_dom(self):
        """Synchronizes grid model tree from canonical DOM root while preserving expansion states."""
        root = self._current_root
        if getattr(self, "doc_session", None) is not None and self.doc_session.dom_root is not None:
            root = self.doc_session.dom_root
            self._current_root = root
        if root is not None:
            expanded_elements = set()
            def collect_expanded(node: _GridNode):
                if node.is_expanded and node.lxml_element is not None:
                    expanded_elements.add(node.lxml_element)
                for ch in node.children:
                    collect_expanded(ch)
            if self._demo_root:
                collect_expanded(self._demo_root)

            self._demo_root = _parse_xml_to_grid("", root)

            def restore_expanded(node: _GridNode):
                if node.lxml_element in expanded_elements:
                    node.is_expanded = True
                for ch in node.children:
                    restore_expanded(ch)
            restore_expanded(self._demo_root)
        self._rebuild()

    def commit_delete_value(self, node: _GridNode, label: Optional[QLabel] = None, editor: Optional[QLineEdit] = None):
        """Clears/empties the value of an editable leaf node, keeping the element itself."""
        self.commit_cell_edit(node, "", label, editor)

    def start_cell_edit(self, node: _GridNode, label: QLabel):
        """Initiates in-place editing of a leaf node using QLineEdit."""
        session = getattr(self, "doc_session", None)
        if not _is_node_editable(node, session):
            return

        parent_widget = label.parentWidget()
        if not parent_widget:
            return

        layout = parent_widget.layout()
        if not layout:
            return

        editor = GridCellEditor(node, self, label, parent_widget)
        if isinstance(layout, QGridLayout):
            idx = layout.indexOf(label)
            if idx != -1:
                row, col, row_span, col_span = layout.getItemPosition(idx)
                label.hide()
                layout.addWidget(editor, row, col, row_span, col_span)
                editor.setFocus()
                editor.selectAll()
        else:
            idx = layout.indexOf(label)
            if idx != -1:
                label.hide()
                layout.insertWidget(idx, editor)
                editor.setFocus()
                editor.selectAll()

    def cancel_cell_edit(self, label: Optional[QLabel], editor: Optional[QLineEdit]):
        """Cancels cell editing, restores the original label, and cleans up editor."""
        if editor is not None:
            editor.hide()
            editor.setParent(None)
            editor.deleteLater()
        if label is not None:
            label.show()
            label.setFocus()

    def commit_cell_edit(self, node: _GridNode, new_text: str, label: Optional[QLabel] = None, editor: Optional[QLineEdit] = None):
        """Validates and commits inline cell edit."""
        current_val = node.value or ""
        if new_text == current_val:
            self.cancel_cell_edit(label, editor)
            return

        session = getattr(self, "doc_session", None)
        if session is not None and node.lxml_element is not None:
            valid, err_msg = session.dry_run_text_update(node.lxml_element, new_text)
            if not valid:
                QMessageBox.warning(
                    self,
                    "Geçersiz Değer",
                    f"Girilen değer XML formatına uygun değil:\n{err_msg}"
                )
                self.cancel_cell_edit(label, editor)
                return

        if not self._prepare_for_command_push():
            self.cancel_cell_edit(label, editor)
            return

        cmd = SetNodeTextCommand(
            node=node,
            element=node.lxml_element,
            old_text=node.lxml_element.text if node.lxml_element is not None else None,
            new_text=new_text,
            session=session,
            grid_panel=self,
            old_node_val=current_val,
        )
        if editor is not None:
            editor.hide()
            editor.setParent(None)
            editor.deleteLater()

        prev_count = self.grid_undo_stack.count()
        self.grid_undo_stack.push(cmd)

        if cmd._failed:
            if getattr(cmd, "_critical_failure", False):
                self.grid_undo_stack.clear()
            elif prev_count == 0:
                self.grid_undo_stack.clear()
                self._undo_redo_blocked = False
                self._history_error_message = ""
            else:
                self.lock_undo_redo(
                    "Düğüm değeri güncellenirken serileştirme hatası oluştu ve değişiklik geri alındı. "
                    "Geçmiş bütünlüğünü korumak için Grid Undo/Redo kilitlendi.",
                    critical=False
                )

    def _check_element_matches_filter(self, elem: etree._Element, path: list[str], condition: str, value: str) -> bool:
        """Helper to re-evaluate if an lxml element still matches the active filter criteria."""
        if not path or not condition:
            return True
        try:
            grid_res = _parse_xml_to_grid("", elem)
            child_node = next((c for c in grid_res.children if getattr(c, "node_type", "element") != "comment"), None)
            if child_node is None:
                return True
            if len(path) == 1 and _local_name(child_node) == path[0]:
                return _check_condition(_get_node_text(child_node), condition, value)
            subpath = path[1:] if len(path) > 1 and _local_name(child_node) == path[0] else path
            return _check_node_matches_subpath(child_node, subpath, condition, value)
        except Exception:
            return True

    def _on_node_text_updated(self, node: _GridNode, old_val: str, new_val: str):
        """Handles state synchronization, filter re-evaluation, and UI refresh after a node edit."""
        if self._active_filter_condition and self._active_filter_path and self._active_filtered_elements is not None:
            for elem in self._active_filtered_elements:
                matches = self._check_element_matches_filter(
                    elem, self._active_filter_path, self._active_filter_condition, self._active_filter_value or ""
                )
                if not matches:
                    self._out_of_filter_elements.add(elem)
                else:
                    self._out_of_filter_elements.discard(elem)

            out_count = len(self._out_of_filter_elements)
            if out_count > 0:
                self.filter_status_label.setText(f"Filter: {self._active_filter_text} ({out_count} kayıt filtre kriteri dışına çıktı)")
            else:
                self.filter_status_label.setText(f"Filter: {self._active_filter_text}")

        self._rebuild()
        self.node_value_changed.emit(node, old_val, new_val)
        self.document_modified.emit()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Main scroll area to contain the arbitrarily tall/wide grid
        # Add a filter status area at the top
        self.filter_status_widget = QWidget()
        self.filter_status_widget.setStyleSheet("background-color: #fff3cd; border-bottom: 1px solid #ffe69c; padding: 4px;")
        filter_layout = QHBoxLayout(self.filter_status_widget)
        filter_layout.setContentsMargins(6, 2, 6, 2)
        
        self.filter_status_label = QLabel("Filter: ")
        self.filter_status_label.setStyleSheet("color: #856404; font-weight: bold; border: none;")
        
        self.filter_clear_btn = QPushButton("Clear")
        self.filter_clear_btn.setStyleSheet("color: #856404; font-weight: bold; padding: 2px 6px;")
        self.filter_clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.filter_clear_btn.clicked.connect(self.clear_filter)
        
        filter_layout.addWidget(self.filter_status_label)
        filter_layout.addStretch()
        filter_layout.addWidget(self.filter_clear_btn)
        
        self.filter_status_widget.hide()
        
        layout.addWidget(self.filter_status_widget)
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; background: #ffffff; }")

        self.content_widget = QWidget()
        self.content_widget.setStyleSheet("background: #ffffff;")
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(8, 8, 8, 8)
        self.content_layout.setSpacing(8)
        self.content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        self.scroll_area.setWidget(self.content_widget)
        layout.addWidget(self.scroll_area)

        # Initial empty root
        self._demo_root = _GridNode("")
        self._rebuild()
        
    def load_xml(self, filepath: str, xml_root=None):
        """Load XML from filepath and update the grid view."""
        self._current_root = xml_root
        if self._current_root is None:
            import lxml.etree as etree
            self._current_root = etree.parse(filepath).getroot()
        self._demo_root = _parse_xml_to_grid(filepath, self._current_root)
        self._target_search_result = None
        self._target_widget = None
        self.grid_undo_stack.clear()
        self._undo_redo_blocked = False
        self._history_error_message = ""
        self.undo_available_changed.emit(False)
        self.redo_available_changed.emit(False)
        self._out_of_filter_elements.clear()
        self._active_filter_text = ""
        self._active_filter_path = None
        self._active_filter_condition = None
        self._active_filter_value = None
        self._active_filtered_elements = None
        self.filter_status_widget.hide()
        self._rebuild()

    def navigate_to_element(self, search_result):
        if not self._demo_root: return
        
        # 1. Find path to element
        def find_path(node, target_el):
            if node.lxml_element is target_el:
                return [node]
            for child in node.children:
                p = find_path(child, target_el)
                if p:
                    return [node] + p
            return []
            
        path = find_path(self._demo_root, search_result.element)
        if not path:
            return

        # Collapse all nodes first, so only the path to the target is open
        def _collapse_nodes(node: _GridNode):
            node.is_expanded = False
            for ch in node.children:
                _collapse_nodes(ch)

        for child in self._demo_root.children:
            _collapse_nodes(child)

        self._demo_root.is_expanded = True
        root_child = next((c for c in self._demo_root.children if getattr(c, "node_type", "element") != "comment"), None)
        if root_child is not None:
            root_child.is_expanded = True
            
        # 2. Expand all parents AND the representative (first sibling) for TableDisplayWidgets
        for i in range(1, len(path)):
            current_node = path[i]
            parent_node = path[i-1]
            
            # Ensure the parent is expanded so it renders its children
            parent_node.is_expanded = True
            
            # If current_node is part of a repeated group (TableDisplayWidget), 
            # its visibility is controlled by the first item in its consecutive chunk.
            chunks = _get_consecutive_chunks(parent_node.children)
            for chunk in chunks:
                if current_node in chunk:
                    chunk[0].is_expanded = True
                    break
            
        self._target_search_result = search_result
        self._target_widget = None
        
        # 3. Rebuild (will highlight and find widget)
        self._rebuild()
        
        # 4. Scroll after UI update
        if self._target_widget:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(10, lambda: self.scroll_area.ensureWidgetVisible(self._target_widget))

    def collapse_all(self):
        """
        Collapses all nodes in the grid view, leaving only the root expanded,
        and clears any active search highlights without modifying the XML data,
        active filters, or other view modes.
        """
        if not self._demo_root:
            return

        def _collapse_nodes(node: _GridNode):
            node.is_expanded = False
            for ch in node.children:
                _collapse_nodes(ch)

        for child in self._demo_root.children:
            _collapse_nodes(child)

        self._demo_root.is_expanded = True
        root_child = next((c for c in self._demo_root.children if getattr(c, "node_type", "element") != "comment"), None)
        if root_child is not None:
            root_child.is_expanded = True

        self._target_search_result = None
        self._target_widget = None
        self._rebuild()

    def clear_highlight(self) -> bool:
        """Clears active search highlight state and collapses the grid view to root state only if a highlight is active."""
        if self._target_search_result is not None or self._target_widget is not None:
            self.collapse_all()
            return True
        return False

    def _rebuild(self):
        """Rebuilds the entire UI from the data model state."""
        # Clear existing
        while self.content_layout.count():
            child = self.content_layout.takeAt(0)
            if child.widget():
                w = child.widget()
                w.setParent(None)
                w.deleteLater()
                
        # If the root is a hidden root with children, render children
        if self._demo_root.element == "" and self._demo_root.children:
            for child in self._demo_root.children:
                self.content_layout.addWidget(render_node(child, self._rebuild, self))
        else:
            self.content_layout.addWidget(render_node(self._demo_root, self._rebuild, self))

    def apply_filter_results(self, matched_elements, filter_text: str, path: list[str] = None, condition: str = None, value: str = None):
        """Displays only the filtered elements and prunes non-matching sub-entities."""
        self._active_filter_text = filter_text
        self._active_filter_path = path
        self._active_filter_condition = condition
        self._active_filter_value = value
        self._active_filtered_elements = list(matched_elements) if matched_elements else []
        self._out_of_filter_elements.clear()

        self.filter_status_label.setText(f"Filter: {filter_text}")
        self.filter_status_widget.show()
        
        if getattr(self, '_current_root', None) is None:
            return
            
        # Create a dummy root node for display
        dummy_root = _GridNode(self._current_root.tag, "")
        dummy_root.is_expanded = True
        dummy_root.lxml_element = self._current_root

        # If zero matches, rebuild with empty dummy root so previous results are not left on screen
        if not matched_elements:
            final_root = _GridNode("")
            final_root.is_expanded = True
            final_root.append_child(dummy_root)
            self._demo_root = final_root
            self._rebuild()
            return

        # Attach matching elements as children using the parser's logic
        for elem in matched_elements:
            grid_res = _parse_xml_to_grid("", elem)
            child_node = next((c for c in grid_res.children if getattr(c, "node_type", "element") != "comment"), grid_res.children[0])
            child_node.is_expanded = True
            
            # If elem is already a table row (its parent in XML has multiple siblings with the same tag),
            # preserve all columns of child_node!
            is_direct_table_row = False
            if elem.getparent() is not None and isinstance(elem.tag, str):
                p = elem.getparent()
                same_tag_count = sum(1 for c in p if c.tag == elem.tag)
                if same_tag_count > 1:
                    is_direct_table_row = True

            # If path indicates we filtered a sub-entity and this is not already a direct table row
            if path and len(path) > 1 and not is_direct_table_row:
                kept = _prune_grid_node(child_node, path[1:], condition, value)
                if kept:
                    child_node.is_expanded = True
                
            dummy_root.append_child(child_node)
            
        # Dynamically expand all nodes along the full selected path across dummy_root children
        if path:
            for ch in dummy_root.children:
                _expand_path_nodes(ch, path)

        # Wrap it similarly to how _parse_xml_to_grid does for the root
        final_root = _GridNode("")
        final_root.is_expanded = True
        final_root.append_child(dummy_root)
        
        self._demo_root = final_root
        self._rebuild()


    def clear_filter(self):
        """Removes the filter and restores original view."""
        self._out_of_filter_elements.clear()
        self._active_filter_text = ""
        self._active_filter_path = None
        self._active_filter_condition = None
        self._active_filter_value = None
        self._active_filtered_elements = None
        self.filter_status_widget.hide()
        if getattr(self, '_current_root', None) is not None:
            self._demo_root = _parse_xml_to_grid("", self._current_root)
            self._rebuild()

    def expand_to_target(self, target_name: str):
        """
        Restores the full XML view and expands the root and target node,
        leaving target children collapsed.
        """
        self.filter_status_widget.hide()
        
        if getattr(self, '_current_root', None) is not None:
            self._demo_root = _parse_xml_to_grid("", self._current_root)
            
        if not self._demo_root or not self._demo_root.children:
            return
            
        root_node = next((c for c in self._demo_root.children if getattr(c, "node_type", "element") != "comment"), None)
        if root_node is None:
            return
        root_node.is_expanded = True
        
        for child in root_node.children:
            if getattr(child, "node_type", "element") == "comment":
                continue
            if _local_name(child).strip() == target_name.strip():
                child.is_expanded = True
                for subchild in child.children:
                    if getattr(subchild, "node_type", "element") == "comment":
                        continue
                    if subchild.element not in ("@xmlns", "Text"):
                        subchild.is_expanded = False
            else:
                if child.element not in ("@xmlns", "Text"):
                    child.is_expanded = False
                    
        self._rebuild()
