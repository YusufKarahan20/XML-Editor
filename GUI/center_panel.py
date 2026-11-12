"""
GUI/center_panel.py — Center Tabbed Area
"""
from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTabWidget, QLabel, QStackedWidget,
    QPushButton, QFrame, QHBoxLayout
)
from PyQt6.QtCore import pyqtSignal, Qt, QSize

from GUI.grid_view import XmlGridPanel
from GUI.text_editor import XmlTextEditor
from GUI.tree_view import XmlTreePanel
from GUI.validation_bar import ValidationBar
from GUI.icon_provider import IconProvider

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

        self.validation_bar = ValidationBar(self)
        self.validation_bar.navigate_requested.connect(self.text_edit.navigate_to_line_col)
        layout.addWidget(self.validation_bar)

    def load_xml(self, filepath: str):
        """Load the XML file text into the editor."""
        with open(filepath, "r", encoding="utf-8-sig") as f:
            self.text_edit.setPlainText(f.read())
        self.clear_validation_error()

    def set_xml_text(self, text: str):
        """Set in-memory XML text into the editor."""
        self.text_edit.setPlainText(text)

    def get_xml_text(self) -> str:
        """Get the current XML text from the editor."""
        return self.text_edit.toPlainText()

    def show_validation_error(self, line: int, col: int, message: str):
        """Show XML syntax error in the validation bar."""
        self.validation_bar.show_error(line, col, message)

    def show_validation_errors(self, errors: list):
        """Show multiple XML syntax errors in the validation bar."""
        self.validation_bar.show_errors(errors)

    def clear_validation_error(self):
        """Hide and clear the validation bar and error line highlights."""
        self.validation_bar.clear_error()
        self.text_edit.clear_error_highlight()

    def navigate_to_element(self, search_result):
        """Navigate to element or value in Text View."""
        self.text_edit.navigate_to_element(search_result)

    def clear_search_highlight(self, reset_cursor: bool = False) -> bool:
        """Clear active search highlight in Text View."""
        return self.text_edit.clear_search_highlight(reset_cursor=reset_cursor)

    def refresh_theme(self, theme_name: str):
        """Refreshes text editor and validation bar for the specified theme."""
        if hasattr(self, "text_edit") and hasattr(self.text_edit, "refresh_theme"):
            self.text_edit.refresh_theme(theme_name)
        if hasattr(self, "validation_bar") and hasattr(self.validation_bar, "refresh_theme"):
            self.validation_bar.refresh_theme(theme_name)

class WelcomeWidget(QFrame):
    """Modern empty state / welcome screen shown when no XML document is loaded."""
    open_requested = pyqtSignal()
    recent_file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("welcomeWidget")
        self.setStyleSheet("""
            QFrame#welcomeWidget {
                background-color: #f7f7f7;
                border: 1px solid #dcdcdc;
                border-radius: 8px;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.setContentsMargins(40, 40, 40, 40)
        main_layout.setSpacing(16)

        # Centered container card
        card = QFrame()
        card.setObjectName("welcomeCard")
        card.setMaximumWidth(540)
        card.setStyleSheet("""
            QFrame#welcomeCard {
                background-color: #ffffff;
                border: 1px solid #d0d0d0;
                border-radius: 8px;
                padding: 24px;
            }
            QFrame#welcomeCard QLabel {
                background-color: transparent;
                background: transparent;
            }
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(14)
        card_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Title
        title_label = QLabel("XML Analyzer")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-size: 22px; font-weight: bold; color: #1e1e1e; background: transparent;")
        card_layout.addWidget(title_label)

        # Subtitle / Hint
        subtitle = QLabel("Bir XML dosyasını buraya sürükleyip bırakın\nveya dosya açarak başlayın.")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("font-size: 13px; color: #000000; line-height: 1.4; background: transparent;")
        card_layout.addWidget(subtitle)

        self.card = card
        self.title_label = title_label
        self.subtitle = subtitle
        self._current_theme = "light"
        self._recent_files_cache: list[str] = []

        # Primary action button: Open XML File
        self.btn_open = QPushButton("Open XML File")
        self.btn_open.setIcon(IconProvider.get_icon("folder-open", color="#ffffff"))
        self.btn_open.setIconSize(QSize(16, 16))
        self.btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open.setFixedHeight(36)
        self.btn_open.setStyleSheet("""
            QPushButton {
                background-color: #0066cc;
                color: #ffffff;
                font-size: 13px;
                font-weight: 600;
                border: none;
                border-radius: 4px;
                padding: 6px 20px;
            }
            QPushButton:hover {
                background-color: #0052a3;
            }
            QPushButton:pressed {
                background-color: #003d7a;
            }
        """)
        self.btn_open.clicked.connect(self.open_requested.emit)
        card_layout.addWidget(self.btn_open)

        # Recent Files Section (dynamically populated)
        self.recent_container = QWidget()
        self.recent_container.setStyleSheet("background: transparent; border: none;")
        self.recent_layout = QVBoxLayout(self.recent_container)
        self.recent_layout.setContentsMargins(0, 10, 0, 0)
        self.recent_layout.setSpacing(6)

        self.recent_header = QLabel("Recent Files")
        self.recent_header.setStyleSheet("font-size: 12px; font-weight: bold; color: #5a5a5a; text-transform: uppercase; background: transparent;")
        self.recent_layout.addWidget(self.recent_header)

        self.recent_list_layout = QVBoxLayout()
        self.recent_list_layout.setSpacing(4)
        self.recent_layout.addLayout(self.recent_list_layout)

        card_layout.addWidget(self.recent_container)
        self.recent_container.setVisible(False)

        main_layout.addWidget(card)

    def set_recent_files(self, files: list[str]):
        """Populate recent files in the welcome card."""
        self._recent_files_cache = list(files)
        while self.recent_list_layout.count():
            item = self.recent_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not files:
            self.recent_container.setVisible(False)
            return

        from GUI.theme_manager import PALETTE
        pal = PALETTE.get(self._current_theme, PALETTE["light"])

        self.recent_container.setVisible(True)
        for filepath in files[:5]:
            filename = Path(filepath).name
            btn = QPushButton(f"  {filename}")
            btn.setIcon(IconProvider.get_icon("file-text", color=pal["icon_muted"]))
            btn.setIconSize(QSize(16, 16))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {pal["welcome_recent_bg"]};
                    color: {pal["text_primary"]};
                    border: 1px solid {pal["welcome_recent_border"]};
                    border-radius: 4px;
                    padding: 6px 12px;
                    text-align: left;
                    font-size: 12px;
                }}
                QPushButton:hover {{
                    background-color: {pal["welcome_recent_hover"]};
                    border-color: {pal["border_default"]};
                }}
            """)
            btn.clicked.connect(lambda checked, p=filepath: self.recent_file_selected.emit(p))
            self.recent_list_layout.addWidget(btn)

    def refresh_theme(self, theme_name: str):
        """Updates welcome screen styling for the specified theme."""
        from GUI.theme_manager import PALETTE
        self._current_theme = theme_name.lower()
        pal = PALETTE.get(self._current_theme, PALETTE["light"])

        self.setStyleSheet(f"""
            QFrame#welcomeWidget {{
                background-color: {pal["welcome_outer_bg"]};
                border: 1px solid {pal["border_muted"]};
                border-radius: 8px;
            }}
        """)
        self.card.setStyleSheet(f"""
            QFrame#welcomeCard {{
                background-color: {pal["welcome_card_bg"]};
                border: 1px solid {pal["welcome_card_border"]};
                border-radius: 8px;
                padding: 24px;
            }}
            QFrame#welcomeCard QLabel {{
                background-color: transparent;
                background: transparent;
            }}
        """)
        self.title_label.setStyleSheet(f"font-size: 22px; font-weight: bold; color: {pal['text_primary']}; background: transparent;")
        self.subtitle.setStyleSheet(f"font-size: 13px; color: {pal['text_muted']}; line-height: 1.4; background: transparent;")
        self.recent_header.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {pal['text_secondary']}; text-transform: uppercase; background: transparent;")

        # Re-populate recent files with active theme styles
        if self._recent_files_cache:
            self.set_recent_files(self._recent_files_cache)


class CenterPanel(QWidget):
    """Center panel holding Text View, Grid View, and Tree View tabs."""
    xml_content_changed = pyqtSignal(str)
    grid_node_value_changed = pyqtSignal(object, str, str)
    grid_document_modified = pyqtSignal()
    open_requested = pyqtSignal()
    filter_cleared = pyqtSignal()
    recent_file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        
        self.header = QLabel("XML VIEWER")
        self.header.setObjectName("panelHeader")
        layout.addWidget(self.header)

        self.sync_warning_banner = QLabel(self)
        self.sync_warning_banner.setObjectName("syncWarningBanner")
        self.sync_warning_banner.setMinimumHeight(28)
        self.sync_warning_banner.setStyleSheet(
            "background-color: #fff3cd; color: #856404; border: 1px solid #ffeeba; "
            "border-radius: 4px; font-size: 12px; font-weight: 500;"
        )
        banner_layout = QHBoxLayout(self.sync_warning_banner)
        banner_layout.setContentsMargins(10, 4, 10, 4)
        banner_layout.setSpacing(8)

        self.sync_warning_icon_label = QLabel(self.sync_warning_banner)
        self.sync_warning_icon_label.setPixmap(
            IconProvider.get_pixmap("alert-triangle", size=16, color="#856404", widget=self.sync_warning_banner)
        )
        self.sync_warning_icon_label.setStyleSheet("background: transparent; border: none;")
        banner_layout.addWidget(self.sync_warning_icon_label)

        self.sync_warning_text_label = QLabel(
            "Taslak Senkronize Değil: Metin görünümündeki sözdizimi hatası giderilene kadar son geçerli XML gösterilmektedir.",
            self.sync_warning_banner
        )
        self.sync_warning_text_label.setStyleSheet("background: transparent; border: none; color: #856404;")
        banner_layout.addWidget(self.sync_warning_text_label)
        banner_layout.addStretch()

        self.sync_warning_banner.setVisible(False)
        layout.addWidget(self.sync_warning_banner)
        
        self.tabs = QTabWidget()
        self.tabs.setIconSize(QSize(16, 16))
        
        self.text_view = TextViewPanel()
        self.text_view.xml_content_changed.connect(self.xml_content_changed.emit)
        self.grid_view = XmlGridPanel()
        self.grid_view.node_value_changed.connect(self.grid_node_value_changed.emit)
        self.grid_view.document_modified.connect(self._on_grid_document_modified)
        self.grid_view.filter_cleared.connect(self.filter_cleared.emit)
        self.tree_view = XmlTreePanel()
        
        self.tabs.addTab(self.text_view, IconProvider.get_icon("file-code"), "Text View")
        self.tabs.addTab(self.grid_view, IconProvider.get_icon("table"), "Grid View")
        self.tabs.addTab(self.tree_view, IconProvider.get_icon("folder-tree"), "Tree View")
        self.tabs.setCurrentWidget(self.grid_view)
        
        self.welcome_widget = WelcomeWidget(self)
        self.welcome_widget.open_requested.connect(self.open_requested.emit)
        self.welcome_widget.recent_file_selected.connect(self.recent_file_selected.emit)

        self.content_stack = QStackedWidget(self)
        self.content_stack.addWidget(self.welcome_widget)  # Index 0: Welcome Screen
        self.content_stack.addWidget(self.tabs)            # Index 1: Editor Tabs

        self.doc_session = None
        self._active_search_result = None
        self._is_syncing_tab = False
        self._is_draft_valid = True
        self.tabs.currentChanged.connect(self._on_tab_changed)

        layout.addWidget(self.content_stack)
        from GUI.theme_manager import ThemeManager
        self.refresh_theme(ThemeManager().get_saved_theme())

    def show_welcome(self):
        """Displays the Welcome Screen and hides editor header and sync banner."""
        self.content_stack.setCurrentWidget(self.welcome_widget)
        self.header.setVisible(False)
        self.sync_warning_banner.setVisible(False)

    def show_editor(self):
        """Displays the editor tabs and shows editor header and sync banner if applicable."""
        self.content_stack.setCurrentWidget(self.tabs)
        self.header.setVisible(True)
        self._update_sync_banner()

    def is_showing_welcome(self) -> bool:
        """Returns True if Welcome Screen is currently active."""
        return self.content_stack.currentWidget() == self.welcome_widget

    def set_recent_files(self, files: list[str]):
        """Passes recent files to the welcome screen widget."""
        self.welcome_widget.set_recent_files(files)

    def _on_grid_document_modified(self):
        """Eagerly updates Tree View model from live canonical dom_root on Grid modifications."""
        if self.doc_session and self.doc_session.dom_root is not None:
            self.tree_view.load_xml("", self.doc_session.dom_root)
        self.grid_document_modified.emit()

    def set_document_session(self, session):
        """Pass canonical document session to CenterPanel and GridView."""
        self.doc_session = session
        self.grid_view.set_document_session(session)

    def set_draft_valid(self, is_valid: bool):
        """Update draft validity and refresh the sync warning banner."""
        self._is_draft_valid = is_valid
        self._update_sync_banner()

    def _update_sync_banner(self):
        """Shows out-of-sync banner only when draft is invalid and active tab is Grid or Tree."""
        if not self._is_draft_valid and self.tabs.currentWidget() in (self.grid_view, self.tree_view):
            self.sync_warning_banner.setVisible(True)
        else:
            self.sync_warning_banner.setVisible(False)

    def load_xml(self, filepath: str, xml_root=None, xml_text: str = None):
        """Distribute the filepath, root, and optional pre-read text to child panels."""
        self.show_editor()
        if xml_root is not None:
            self.grid_view.load_xml(filepath, xml_root)
            self.tree_view.load_xml(filepath, xml_root)
        else:
            self.grid_view.load_xml(filepath)
            self.tree_view.load_xml(filepath)
        if xml_text is not None:
            self.text_view.set_xml_text(xml_text)
            self.text_view.clear_validation_error()
        else:
            self.text_view.load_xml(filepath)
        self.set_draft_valid(True)

    def load_xml_from_text(self, xml_text: str, xml_root=None, source_name: str = ""):
        """Distribute in-memory XML text and root to child panels."""
        self.show_editor()
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
        self._update_sync_banner()
        if self._is_syncing_tab or self._active_search_result is None:
            return
        self._is_syncing_tab = True
        try:
            self._focus_current_view_on_active_result()
        finally:
            self._is_syncing_tab = False

    def clear_search_highlight(self, reset_cursor: bool = False) -> bool:
        """Clear active search highlight in both Grid View and Text View."""
        self._active_search_result = None
        grid_cleared = self.grid_view.clear_highlight()
        text_cleared = self.text_view.clear_search_highlight(reset_cursor=reset_cursor)
        return grid_cleared or text_cleared

    def refresh_theme(self, theme_name: str):
        """Refreshes child panels, tab icons, and banners on theme change."""
        from GUI.theme_manager import PALETTE
        pal = PALETTE.get(theme_name.lower(), PALETTE["light"])

        # 1. Child panels
        if hasattr(self, "welcome_widget") and hasattr(self.welcome_widget, "refresh_theme"):
            self.welcome_widget.refresh_theme(theme_name)
        if hasattr(self, "text_view") and hasattr(self.text_view, "refresh_theme"):
            self.text_view.refresh_theme(theme_name)
        if hasattr(self, "grid_view") and hasattr(self.grid_view, "refresh_theme"):
            self.grid_view.refresh_theme(theme_name)

        # 2. Tab icons
        if hasattr(self, "tabs"):
            self.tabs.setTabIcon(0, IconProvider.get_icon("file-code"))
            self.tabs.setTabIcon(1, IconProvider.get_icon("table"))
            self.tabs.setTabIcon(2, IconProvider.get_icon("folder-tree"))

        # 3. Sync warning banner
        if hasattr(self, "sync_warning_banner") and hasattr(self, "sync_warning_icon_label"):
            self.sync_warning_banner.setStyleSheet(f"""
                QFrame#syncWarningBanner {{
                    background-color: {pal["sync_banner_bg"]};
                    border: 1px solid {pal["sync_banner_border"]};
                    border-radius: 4px;
                }}
            """)
            self.sync_warning_icon_label.setPixmap(
                IconProvider.get_pixmap("alert-triangle", size=16, color=pal["sync_banner_text"], widget=self.sync_warning_banner)
            )
            self.sync_warning_text_label.setStyleSheet(f"background: transparent; border: none; color: {pal['sync_banner_text']};")
