"""
GUI/text_editor.py — Advanced XML Text Editor Component
Provides ReadOnly XML viewing with Syntax Highlighting, Line Numbers, and Code Folding.
"""

import re
from PyQt6.QtWidgets import (
    QWidget, QPlainTextEdit, QTextEdit, QHBoxLayout
)
from PyQt6.QtGui import (
    QPainter, QColor, QTextFormat, QFont, QSyntaxHighlighter, QTextCharFormat
)
from PyQt6.QtCore import Qt, QRect, QSize

class FoldNode:
    """Represents a foldable block in the raw XML text."""
    __slots__ = ("start_line", "end_line", "level", "is_folded", "children")
    
    def __init__(self, start_line, level):
        self.start_line = start_line
        self.end_line = -1
        self.level = level
        self.is_folded = False
        self.children = []

class XmlStructureAnalyzer:
    """Lightweight analyzer to determine fold ranges from raw XML text."""
    @staticmethod
    def analyze(text):
        lines = text.split("\n")
        root_nodes = []
        node_stack = []
        
        tag_pattern = re.compile(r'<(/?)(\S+?)(?:[\s>].*?)?>')
        
        for i, line in enumerate(lines):
            pos = 0
            while True:
                start_idx = line.find('<', pos)
                if start_idx == -1: 
                    break
                
                # Ignore comments and XML declarations / doctypes
                if line.startswith('<!--', start_idx):
                    pos = line.find('-->', start_idx)
                    if pos == -1: break
                    pos += 3
                    continue
                if line.startswith('<?', start_idx) or line.startswith('<!', start_idx):
                    pos = line.find('>', start_idx)
                    if pos == -1: break
                    pos += 1
                    continue
                    
                match = tag_pattern.match(line[start_idx:])
                if match:
                    full_match = match.group(0)
                    if not full_match.endswith('/>'):
                        is_close = match.group(1) == '/'
                        tag_name = match.group(2)
                        
                        if not is_close:
                            node = FoldNode(i, len(node_stack))
                            if node_stack:
                                node_stack[-1][1].children.append(node)
                            else:
                                root_nodes.append(node)
                            node_stack.append((tag_name, node))
                        else:
                            # Pop matching tag
                            for j in range(len(node_stack)-1, -1, -1):
                                if node_stack[j][0] == tag_name:
                                    node = node_stack[j][1]
                                    node.end_line = i
                                    node_stack = node_stack[:j]
                                    break
                    pos = start_idx + len(full_match)
                else:
                    pos = start_idx + 1
                    
        # Close any unmatched nodes gracefully
        while node_stack:
            _, node = node_stack.pop()
            node.end_line = len(lines) - 1
            
        return root_nodes

class XmlHighlighter(QSyntaxHighlighter):
    """Syntax Highlighter for raw XML text."""
    def __init__(self, document):
        super().__init__(document)
        
        self.fmt_tag = QTextCharFormat()
        self.fmt_tag.setForeground(QColor("#0000FF")) # Blue tags
        
        self.fmt_attr_name = QTextCharFormat()
        self.fmt_attr_name.setForeground(QColor("#800080")) # Purple attribute name/key
        
        self.fmt_attr_val = QTextCharFormat()
        self.fmt_attr_val.setForeground(QColor("#FF0000")) # Red attribute value
        
        self.fmt_comment = QTextCharFormat()
        self.fmt_comment.setForeground(QColor("#008000")) # Green comments
        
        self.fmt_decl = QTextCharFormat()
        self.fmt_decl.setForeground(QColor("#808080")) # Gray decl

    def highlightBlock(self, text):
        # 1. Tags (<...>) and Attributes inside them
        tag_pattern = re.compile(r'<[^>]*>')
        attr_pattern = re.compile(r'\s+([A-Za-z0-9_:-]+\s*=)\s*("[^"]*"|\'[^\']*\')')
        
        for tag_match in tag_pattern.finditer(text):
            tag_text = tag_match.group(0)
            tag_start = tag_match.start()
            tag_end = tag_match.end()
            
            # Skip comments and declarations, they are handled separately
            if tag_text.startswith("<?") or tag_text.startswith("<!--"):
                continue
                
            # Base color for the entire tag is BLUE
            self.setFormat(tag_start, tag_end - tag_start, self.fmt_tag)
            
            # Attributes inside the tag
            for attr_match in attr_pattern.finditer(tag_text):
                # group(1) -> attribute name and '=' (PURPLE)
                self.setFormat(tag_start + attr_match.start(1), attr_match.end(1) - attr_match.start(1), self.fmt_attr_name)
                # group(2) -> attribute string value (RED)
                self.setFormat(tag_start + attr_match.start(2), attr_match.end(2) - attr_match.start(2), self.fmt_attr_val)
                
        # 2. XML Declarations (<?xml ... ?>)
        for match in re.finditer(r'<\?xml.*?\?>', text):
            self.setFormat(match.start(), match.end() - match.start(), self.fmt_decl)
            
        # 3. Multiline comment tracking (handles single line implicitly)
        self.setCurrentBlockState(0)
        start_idx = 0
        if self.previousBlockState() == 1:
            start_idx = 0
        else:
            start_idx = text.find("<!--")
            
        while start_idx >= 0:
            end_idx = text.find("-->", start_idx)
            if end_idx == -1:
                self.setCurrentBlockState(1)
                self.setFormat(start_idx, len(text) - start_idx, self.fmt_comment)
                break
            else:
                comment_len = end_idx - start_idx + 3
                self.setFormat(start_idx, comment_len, self.fmt_comment)
                start_idx = text.find("<!--", start_idx + comment_len)

class LineNumberArea(QWidget):
    """Custom margin widget for rendering line numbers and fold indicators."""
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        
    def sizeHint(self):
        return QSize(self.editor.line_number_area_width(), 0)
        
    def paintEvent(self, event):
        self.editor.line_number_area_paint_event(event)
        
    def mousePressEvent(self, event):
        self.editor.line_number_area_mouse_press_event(event)

class XmlTextEditor(QPlainTextEdit):
    """ReadOnly QPlainTextEdit with folding and line numbers."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        
        mono = QFont("Cascadia Code", 11)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(mono)
        
        self.line_number_area = LineNumberArea(self)
        
        self.blockCountChanged.connect(self.update_line_number_area_width)
        self.updateRequest.connect(self.update_line_number_area)
        
        self.update_line_number_area_width(0)
        
        self.highlighter = XmlHighlighter(self.document())
        
        self.fold_nodes = []
        self.line_to_node = {}
        
    def setPlainText(self, text):
        super().setPlainText(text)
        self.fold_nodes = XmlStructureAnalyzer.analyze(text)
        self.line_to_node = {}
        
        def populate_map(nodes):
            for n in nodes:
                if n.end_line > n.start_line:
                    self.line_to_node[n.start_line] = n
                populate_map(n.children)
                
        populate_map(self.fold_nodes)
        self.apply_folding() # ensure clean state
        
    def line_number_area_width(self):
        digits = 1
        m = max(1, self.blockCount())
        while m >= 10:
            m //= 10
            digits += 1
        
        # padding + numbers + gutter area for icons
        return 5 + self.fontMetrics().horizontalAdvance('9') * digits + 25
        
    def update_line_number_area_width(self, _):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)
        
    def update_line_number_area(self, rect, dy):
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
            
        if rect.contains(self.viewport().rect()):
            self.update_line_number_area_width(0)
            
    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height()))
        
    def line_number_area_paint_event(self, event):
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QColor("#f0f0f0"))
        
        block = self.firstVisibleBlock()
        blockNumber = block.blockNumber()
        
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        
        # Right border line
        painter.setPen(QColor("#d0d0d0"))
        painter.drawLine(self.line_number_area.width() - 1, 0, self.line_number_area.width() - 1, self.line_number_area.height())
        
        fm = self.fontMetrics()
        
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                
                # Draw Line Number
                number = str(blockNumber + 1)
                painter.setPen(QColor("#888888"))
                painter.drawText(0, top, self.line_number_area.width() - 25, fm.height(),
                                 Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, number)
                
                # Draw Fold Icons
                node = self.line_to_node.get(blockNumber)
                if node:
                    icon_size = 9
                    x = self.line_number_area.width() - 17
                    y = top + (fm.height() - icon_size) // 2
                    
                    painter.setPen(QColor("#666666"))
                    painter.setBrush(QColor("#ffffff"))
                    painter.drawRect(x, y, icon_size, icon_size)
                    
                    # Horizontal line for minus
                    painter.drawLine(x + 2, y + icon_size//2, x + icon_size - 2, y + icon_size//2)
                    
                    # Vertical line for plus
                    if node.is_folded:
                        painter.drawLine(x + icon_size//2, y + 2, x + icon_size//2, y + icon_size - 2)
                        
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            blockNumber += 1
            
    def line_number_area_mouse_press_event(self, event):
        y = event.position().y()
        block = self.firstVisibleBlock()
        blockNumber = block.blockNumber()
        
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        
        while block.isValid():
            if top <= y <= bottom and block.isVisible():
                x = event.position().x()
                # If click was in the gutter icon area
                if x >= self.line_number_area.width() - 22:
                    self.toggle_fold(blockNumber)
                    break
            
            block = block.next()
            if block.isVisible():
                top = bottom
                bottom = top + round(self.blockBoundingRect(block).height())
            blockNumber += 1

    def toggle_fold(self, line_number):
        if line_number in self.line_to_node:
            node = self.line_to_node[line_number]
            node.is_folded = not node.is_folded
            self.apply_folding()
            
    def apply_folding(self):
        line_count = self.document().blockCount()
        visible = [True] * line_count
        
        # Recursive state application respecting nested folds
        def apply_node(node, parent_hidden):
            if parent_hidden:
                for i in range(node.start_line, node.end_line + 1):
                    if i < line_count:
                        visible[i] = False
            else:
                if node.is_folded:
                    # Hide everything inside the node, but keep the start_line visible!
                    for i in range(node.start_line + 1, node.end_line + 1):
                        if i < line_count:
                            visible[i] = False
                            
            hidden_for_children = parent_hidden or node.is_folded
            for child in node.children:
                apply_node(child, hidden_for_children)
                
        for root in self.fold_nodes:
            apply_node(root, False)
            
        b = self.document().firstBlock()
        while b.isValid():
            v = visible[b.blockNumber()]
            if b.isVisible() != v:
                b.setVisible(v)
            b = b.next()
            
        self.viewport().update()
        self.line_number_area.update()
        self.document().markContentsDirty(0, self.document().characterCount())
