"""
GUI/grid_view.py — Advanced XML Grid Panel Prototype

Implements Altova XMLSpy-style Grid View:
1. Normal Nested Grid: Container widgets for non-repeating elements.
2. Table Display: Grid/Table layout for repeating elements.
"""

from __future__ import annotations

from pathlib import Path
from lxml import etree

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame,
    QGridLayout, QPushButton, QSizePolicy, QMessageBox
)
from PyQt6.QtCore import Qt


class _GridNode:
    """Lightweight node holding element name, optional value, and children."""
    __slots__ = ("element", "value", "children", "is_expanded", "lxml_element")

    def __init__(self, element: str, value: str = "", children: list[_GridNode] | None = None):
        self.element = element
        self.value = value
        self.children = children or []
        self.is_expanded = False
        self.lxml_element = None

    def append_child(self, child: _GridNode) -> _GridNode:
        self.children.append(child)
        return child


# =====================================================================
#  Widget Components
# =====================================================================
class HeaderWidget(QWidget):
    """Clickable header for the ExpandableFrame."""
    def __init__(self, node: _GridNode, toggle_callback, parent=None):
        super().__init__(parent)
        self.toggle_callback = toggle_callback
        
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
        
        self.title_label = QLabel(node.element)
        self.title_label.setStyleSheet("font-weight: bold; color: #1e1e1e; font-size: 12px; background: transparent;")
        self.title_label.setCursor(Qt.CursorShape.PointingHandCursor)
        
        layout.addWidget(self.toggle_btn)
        layout.addWidget(self.title_label)
        layout.addStretch()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_callback()
            event.accept()

class ExpandableFrame(QFrame):
    """A generic frame that can be expanded/collapsed with Altova XMLSpy-style border and header."""

    def __init__(self, node: _GridNode, on_rebuild, parent=None):
        super().__init__(parent)
        self.node = node
        self.on_rebuild = on_rebuild
        
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
        self.header_widget = HeaderWidget(node, self.toggle)
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
        grid_frame.setStyleSheet("QFrame { background: #d8d8d8; border: 1px solid #c8c8c8; }")
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
        
        # Normal çocuk kolonların başlıkları
        for c, col_name in enumerate(columns):
            hdr = QLabel(f"<b>{col_name}</b>")
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

            # Row number
            row_lbl = QLabel(str(r + 1))
            row_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignTop)
            if is_target_row:
                row_lbl.setStyleSheet("background: #fffacd; padding: 6px; font-size: 11px; color: #888; border: none;")
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
                ns_lbl.setStyleSheet("background: #ffffff; padding: 6px; border: none; color: #333;")
                ns_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                grid_layout.addWidget(ns_lbl, r + 1, current_col)
                current_col += 1
            
            # Eğer elemanın ne normal çocukları ne de text'i yoksa veya
            # sadece @xmlns namespace'i varsa (text'i boş olan leaf):
            if not item.children or (len(item.children) == 1 and item.children[0].element == "@xmlns"):
                val_lbl = QLabel(item.value)
                val_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                if is_target_val:
                    val_lbl.setStyleSheet("background: #fffacd; padding: 6px; border: none;")
                    self.grid_panel._target_widget = val_lbl
                else:
                    val_lbl.setStyleSheet("background: #ffffff; padding: 6px; border: none;")
                grid_layout.addWidget(val_lbl, r + 1, current_col)
            else:
                for c, col_name in enumerate(columns):
                    col_children = [ch for ch in item.children if ch.element == col_name]
                    
                    cell_widget = QWidget()
                    cell_widget.setStyleSheet("background: #ffffff; border: none;")
                    cell_layout = QVBoxLayout(cell_widget)
                    cell_layout.setContentsMargins(6, 6, 6, 6)
                    cell_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
                    
                    if col_children:
                        if len(col_children) == 1:
                            child_node = col_children[0]
                            is_child_target_elem = False
                            is_child_target_val = False
                            if self.grid_panel and self.grid_panel._target_search_result and child_node.lxml_element is self.grid_panel._target_search_result.element:
                                if self.grid_panel._target_search_result.match_type == "Element":
                                    is_child_target_elem = True
                                    cell_widget.setStyleSheet("background: #fffacd; border: none;")
                                    self.grid_panel._target_widget = cell_widget
                                else:
                                    is_child_target_val = True

                            if not child_node.children:
                                lbl = QLabel(child_node.value)
                                if is_child_target_val:
                                    lbl.setStyleSheet("background: #fffacd; border: none;")
                                    self.grid_panel._target_widget = lbl
                                else:
                                    lbl.setStyleSheet("background: transparent; border: none;") 
                                lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
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
    if not node.children:
        w = QWidget()
        l = QHBoxLayout(w)
        l.setContentsMargins(4, 2, 4, 2)
        l.setSpacing(8)
        lbl_elem = QLabel(f"<b>{node.element}</b>")
        lbl_elem.setStyleSheet("color: #1e1e1e;")
        lbl_val = QLabel(node.value)
        l.addWidget(lbl_elem)
        l.addWidget(lbl_val)
        l.addStretch()

        if grid_panel and grid_panel._target_search_result and node.lxml_element is grid_panel._target_search_result.element:
            res = grid_panel._target_search_result
            if res.match_type == "Value":
                lbl_val.setStyleSheet("background-color: #fffacd; color: #1e1e1e;")
                grid_panel._target_widget = lbl_val
            else:
                w.setStyleSheet("background-color: #fffacd; border-radius: 2px;")
                grid_panel._target_widget = w

        return w
        
    # Group children to detect repeats
    groups = {}
    for c in node.children:
        groups.setdefault(c.element, []).append(c)
        
    frame = ExpandableFrame(node, on_rebuild)

    if grid_panel and grid_panel._target_search_result and node.lxml_element is grid_panel._target_search_result.element:
        if grid_panel._target_search_result.match_type == "Element":
            if node.is_expanded:
                frame.header_widget.setStyleSheet("""
                    QWidget#expandableHeader {
                        background-color: #fffacd;
                        border-bottom: 1px solid #d4d8dc;
                        border-top-left-radius: 3px;
                        border-top-right-radius: 3px;
                    }
                """)
            else:
                frame.header_widget.setStyleSheet("""
                    QWidget#expandableHeader {
                        background-color: #fffacd;
                        border-radius: 3px;
                    }
                """)
            grid_panel._target_widget = frame.header_widget
    
    if node.is_expanded:
        if "@xmlns" in groups:
            xmlns_nodes = groups.pop("@xmlns")[0].children
            ns_widget = QWidget()
            ns_layout = QGridLayout(ns_widget)
            ns_layout.setContentsMargins(0, 0, 0, 4)
            ns_layout.setSpacing(0)
            
            for row, ns_child in enumerate(xmlns_nodes):
                lbl_pfx = QLabel(f"<b>{ns_child.element}</b>")
                lbl_pfx.setStyleSheet("background: #f0f0f0; border: 1px solid #d0d0d0; padding: 4px; color: #555;")
                lbl_uri = QLabel(ns_child.value)
                lbl_uri.setStyleSheet("background: #ffffff; border: 1px solid #d0d0d0; border-left: none; padding: 4px; color: #333;")
                lbl_uri.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                
                ns_layout.addWidget(lbl_pfx, row, 0)
                ns_layout.addWidget(lbl_uri, row, 1)
                
            frame.add_widget(ns_widget)

        for element_name, items in groups.items():
            if len(items) == 1:
                frame.add_widget(render_node(items[0], on_rebuild, grid_panel))
            else:
                frame.add_widget(TableDisplayWidget(element_name, items, on_rebuild, grid_panel))
            
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
            
            if text_val:
                node.append_child(_GridNode("Text", text_val))
                node.value = ""
                
        for child in xml_el:
            child_node = _recursive_parse(child, xml_el.nsmap)
            node.append_child(child_node)
        return node
        
    parsed_root = _recursive_parse(xml_root)
    # parsed_root.is_expanded is False by default, keeping it collapsed
    root.append_child(parsed_root)
        
    return root

def _is_complex_node(node: _GridNode) -> bool:
    """Returns True if the node has structural children (not just xmlns or Text)."""
    for child in node.children:
        if child.element not in ("@xmlns", "Text"):
            return True
    return False

def _local_name(node: _GridNode) -> str:
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
    if node is None:
        return ""
    if node.value is not None and node.value.strip():
        return node.value.strip()
    if node.lxml_element is not None and node.lxml_element.text and node.lxml_element.text.strip():
        return node.lxml_element.text.strip()
    for child in node.children:
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

def _prune_grid_node(node: _GridNode, subpath: list[str], condition: str, value: str) -> bool:
    """
    Recursively prunes children of _GridNode along the subpath.
    In repeating table rows (lists), preserves all sibling columns of matching rows.
    In single non-repeating XML paths, prunes non-path sibling branches to focus the view.
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
                    # If this table row contains deeper subpaths, prune inner levels as well
                    if len(remaining_subpath) > 1:
                        _prune_grid_node(child, remaining_subpath, condition, value)
                    elif remaining_subpath:
                        # Direct leaf of this table row: preserve all columns of the row and expand to leaf
                        _expand_path_nodes(child, remaining_subpath)
                else:
                    children_to_remove.append(child)
            else:
                if child.element not in ("@xmlns", "Text"):
                    children_to_remove.append(child)

        for child in children_to_remove:
            node.children.remove(child)

        if any_kept:
            node.is_expanded = True
        return any_kept

    # Single XML path (non-repeating)
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
            keep = _prune_grid_node(child, subpath[1:], condition, value)
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._target_search_result = None
        self._target_widget = None
        self._setup_ui()

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
            
        # 2. Expand all parents AND the representative (first sibling) for TableDisplayWidgets
        for i in range(1, len(path)):
            current_node = path[i]
            parent_node = path[i-1]
            
            # Ensure the parent is expanded so it renders its children
            parent_node.is_expanded = True
            
            # If current_node is part of a repeated group (TableDisplayWidget), 
            # its visibility is controlled by the first item in that group.
            first_sibling = next((c for c in parent_node.children if c.element == current_node.element), None)
            if first_sibling:
                first_sibling.is_expanded = True
            
        self._target_search_result = search_result
        self._target_widget = None
        
        # 3. Rebuild (will highlight and find widget)
        self._rebuild()
        
        # 4. Scroll after UI update
        if self._target_widget:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(10, lambda: self.scroll_area.ensureWidgetVisible(self._target_widget))

    def _rebuild(self):
        """Rebuilds the entire UI from the data model state."""
        # Clear existing
        while self.content_layout.count():
            child = self.content_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
                
        # If the root is a hidden root with children, render children
        if self._demo_root.element == "" and self._demo_root.children:
            for child in self._demo_root.children:
                self.content_layout.addWidget(render_node(child, self._rebuild, self))
        else:
            self.content_layout.addWidget(render_node(self._demo_root, self._rebuild, self))

    def apply_filter_results(self, matched_elements, filter_text: str, path: list[str] = None, condition: str = None, value: str = None):
        """Displays only the filtered elements and prunes non-matching sub-entities."""
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
            child_node = _parse_xml_to_grid("", elem).children[0]
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
            
        root_node = self._demo_root.children[0]
        root_node.is_expanded = True
        
        for child in root_node.children:
            if _local_name(child).strip() == target_name.strip():
                child.is_expanded = True
                for subchild in child.children:
                    if subchild.element not in ("@xmlns", "Text"):
                        subchild.is_expanded = False
            else:
                if child.element not in ("@xmlns", "Text"):
                    child.is_expanded = False
                    
        self._rebuild()
