"""
GUI/main_window.py — Main Application Window

Assembles the full XML Analyzer interface:
  • Menu bar with File / Edit / View / Search / Filter / Convert / Help
  • Toolbar with key actions + embedded search panel
  • Three resizable panels (XML Tree | XML Grid | JSON Preview)
  • Status bar

All QActions are wired as stubs so functionality can be connected later.
"""

import logging
import os
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QToolBar, QStatusBar, QLabel, QFileDialog, QMessageBox,
    QSizePolicy, QApplication, QToolButton, QMenu,
)
from PyQt6.QtGui import QAction, QIcon, QKeySequence, QCloseEvent
from PyQt6.QtCore import Qt, QSize, QSettings, QEvent, QObject
from lxml import etree
from XML.document_session import XmlDocumentSession, HistoryIntegrityError
from XML.history_manager import HistoryLockError
from GUI.center_panel import CenterPanel
from GUI.history_viewer import HistoryViewerDialog
from GUI.icon_provider import IconProvider
from GUI.theme_manager import ThemeManager, PALETTE
from SEARCH.engine import search_xml
from GUI.json_preview import JsonPreviewPanel
from GUI.search_panel import SearchPanel
from SEARCH.result_list import SearchResultsPanel
from GUI.filter_view import FilterPanel
from FILTER.engine import apply_filter
from CONVERTER.xml_to_json import ConversionConfig, xml_to_json
from CONVERTER.heuristics import build_smart_array_detector
from CONVERTER.soap import is_soap_envelope, is_service_payload, soap_to_json, build_soap_array_detector

logger = logging.getLogger(__name__)


def _normalize_path(filepath: str) -> str:
    """Standardizes path on Windows for case-insensitive and slash-agnostic comparison."""
    if not filepath or not str(filepath).strip():
        return ""
    return os.path.normcase(os.path.abspath(os.path.normpath(str(filepath).strip())))


class _FileDropEventFilter(QObject):
    """Application-level event filter for secure, global XML drag & drop handling."""
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window

    def _get_valid_local_xml_files(self, mime_data) -> list[str]:
        valid_files = []
        if not mime_data or not mime_data.hasUrls():
            return valid_files

        for url in mime_data.urls():
            if not url.isLocalFile():
                continue
            local_path = url.toLocalFile()
            if not local_path or not local_path.strip():
                continue
            clean_path = os.path.abspath(os.path.normpath(local_path.strip()))
            if not os.path.isfile(clean_path):
                continue
            if not clean_path.lower().endswith((".xml", ".txt")):
                continue
            valid_files.append(clean_path)
        return valid_files

    def eventFilter(self, watched, event):
        if isinstance(watched, QWidget) and watched.window() == self.main_window:
            if event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
                if event.mimeData() and event.mimeData().hasUrls():
                    valid_xmls = self._get_valid_local_xml_files(event.mimeData())
                    if valid_xmls:
                        event.acceptProposedAction()
                    else:
                        event.ignore()
                    return True
            elif event.type() == QEvent.Type.Drop:
                if event.mimeData() and event.mimeData().hasUrls():
                    valid_xmls = self._get_valid_local_xml_files(event.mimeData())
                    if valid_xmls:
                        event.acceptProposedAction()
                        self.main_window.open_document(valid_xmls[0])
                    else:
                        event.ignore()
                    return True
        return super().eventFilter(watched, event)


class MainWindow(QMainWindow):
    """Top-level application window for XML Analyzer."""

    APP_TITLE = "XML Analyzer"
    DEFAULT_SIZE = (1400, 900)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(self.APP_TITLE)
        self.resize(*self.DEFAULT_SIZE)
        self.setMinimumSize(800, 500)

        # ── Initialize ThemeManager and apply saved theme ─────────────
        self.theme_manager = ThemeManager(self)
        initial_theme = self.theme_manager.get_saved_theme()
        self.theme_manager.apply_theme(initial_theme, save_preference=False, force=True)
        self.theme_manager.theme_changed.connect(self._on_theme_changed)

        # ── Actions & Settings (must be created before menus & toolbars) ──
        self._actions: dict[str, QAction] = {}
        self._recent_files: list[str] = []
        self._load_recent_files()
        self._create_actions()

        # ── UI assembly ──────────────────────────────────────────────
        self._create_menu_bar()
        self._create_toolbar()
        self._create_central_area()
        self._create_status_bar()
        self.doc_session = XmlDocumentSession()
        self.center_panel.set_document_session(self.doc_session)
        self.center_panel.grid_node_value_changed.connect(self._on_grid_node_value_changed)
        self.center_panel.open_requested.connect(self._open_file_dialog)
        self.center_panel.recent_file_selected.connect(self.open_document)
        self.center_panel.set_recent_files(self._recent_files)
        self.current_xml_path = None
        self.current_xml_root = None
        self._last_valid_xml_text = ""
        self.soap_numeric_fields = None
        self.update_status(state="No XML file opened")
        self._update_window_title()
        self._setup_undo_redo_sync()

        # ── Initial View State & Global Drag-and-Drop Filter ──────────
        if self.current_xml_root is None:
            self.center_panel.show_welcome()

        # Synchronize all child panels and widgets with the initial theme
        self._on_theme_changed(initial_theme)

        self._drop_filter = _FileDropEventFilter(self)
        app_inst = QApplication.instance()
        if app_inst:
            app_inst.installEventFilter(self._drop_filter)

    def _update_window_title(self):
        """Updates the window title reflecting active file name and dirty status."""
        file_path = self.doc_session.file_path
        if file_path:
            filename = file_path.name
        elif self.doc_session.dom_root is not None:
            filename = "Untitled.xml"
        else:
            filename = ""

        dirty_marker = " *" if self.doc_session.is_dirty() else ""
        if filename:
            self.setWindowTitle(f"{self.APP_TITLE} — {filename}{dirty_marker}")
        else:
            self.setWindowTitle(self.APP_TITLE)

    # ==================================================================
    #  STYLESHEET & THEME HELPERS
    # ==================================================================
    def _load_stylesheet(self):
        """Stylesheets are managed centrally via ThemeManager at QApplication level."""
        pass

    def _get_menu_style(self, theme: str) -> str:
        """Returns the dynamic QMenu stylesheet using theme palette tokens."""
        pal = PALETTE.get(theme, PALETTE["light"])
        return f"""
            QMenu {{
                background-color: {pal["menu_bg"]};
                color: {pal["text_primary"]};
                border: 1px solid {pal["border_default"]};
                border-radius: 4px;
                padding: 4px 0;
            }}
            QMenu::item {{
                background-color: transparent;
                color: {pal["text_primary"]};
                padding: 6px 28px 6px 20px;
            }}
            QMenu::item:selected {{
                background-color: {pal["menu_item_hover"]};
                color: {pal["text_primary"]};
            }}
            QMenu::separator {{
                height: 1px;
                background-color: {pal["border_muted"]};
                margin: 4px 10px;
            }}
        """

    def _get_menu_btn_style(self, theme: str) -> str:
        """Returns the dynamic header QToolButton stylesheet using theme palette tokens."""
        pal = PALETTE.get(theme, PALETTE["light"])
        return f"""
            QToolButton {{
                background-color: transparent;
                color: {pal["text_primary"]};
                border: 1px solid transparent;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 13px;
                font-weight: 500;
            }}
            QToolButton:hover {{
                background-color: {pal["toolbar_btn_hover"]};
                border-color: {pal["border_muted"]};
                color: {pal["text_primary"]};
            }}
            QToolButton:pressed, QToolButton:checked, QToolButton:open {{
                background-color: {pal["toolbar_btn_pressed"]};
                border-color: {pal["border_default"]};
                color: {pal["text_primary"]};
            }}
            QToolButton:focus {{
                outline: none;
            }}
            QToolButton::menu-indicator {{
                image: none;
                width: 0px;
            }}
        """

    # ==================================================================
    #  ACTIONS
    # ==================================================================
    def _create_actions(self):
        """Create all QActions with shortcuts and vector SVG icons."""

        def _action(name, text, shortcut=None, tip=None, slot=None, icon_name=None):
            action = QAction(text, self)
            if icon_name:
                action.setIcon(IconProvider.get_icon(icon_name))
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            if tip:
                action.setStatusTip(tip)
                action.setToolTip(tip)
            if slot:
                action.triggered.connect(slot)
            else:
                action.triggered.connect(lambda checked, n=name: self._placeholder(n))
            self._actions[name] = action
            return action

        # ── File ─────────────────────────────────────────────────────
        _action("file_new",     "New",         "Ctrl+N",  "Create a new XML document",
                slot=self._on_new_file, icon_name="file-plus")
        _action("file_open",    "Open XML…",   "Ctrl+O",  "Open an XML file",
                slot=self._open_file_dialog, icon_name="folder-open")
        _action("file_save",    "Save",         "Ctrl+S",  "Save current file",
                slot=self._on_save_file, icon_name="save")
        _action("file_save_as", "Save As…",     "Ctrl+Shift+S", "Save file as…",
                slot=self._on_save_file_as, icon_name="file-down")
        _action("file_history", "History Viewer…", "Ctrl+H", "View document change history and audit trail",
                slot=self._show_history_viewer, icon_name="history")
        _action("file_exit",    "Exit",         "Ctrl+Q",  "Quit application",
                slot=self._on_exit, icon_name="log-out")

        # ── Edit ─────────────────────────────────────────────────────
        _action("edit_undo",       "Undo",       "Ctrl+Z",  "Undo last action",
                slot=self._on_undo, icon_name="undo-2")
        _action("edit_redo",       "Redo",       "Ctrl+Y",  "Redo last action",
                slot=self._on_redo, icon_name="redo-2")
        _action("edit_copy",       "Copy",       "Ctrl+C",  "Copy selection",
                slot=self._on_copy, icon_name="copy")
        _action("edit_paste",      "Paste",      "Ctrl+V",  "Paste from clipboard",
                slot=self._on_paste, icon_name="clipboard-paste")
        _action("edit_select_all", "Select All", "Ctrl+A",  "Select all",
                slot=self._on_select_all, icon_name="check-square")

        # ── View ─────────────────────────────────────────────────────
        _action("view_tree",  "Tree View",    None, "Switch to tree view")
        _action("view_grid",  "Grid View",    None, "Switch to grid view")
        json_action = _action("view_json",  "JSON Preview", None, "Toggle JSON preview",
                              slot=self._toggle_json_preview, icon_name="panel-right")
        json_action.setCheckable(True)
        json_action.setChecked(False)

        # ── Search ───────────────────────────────────────────────────
        _action("search_find",  "Search XML…",    "Ctrl+F",       "Search within XML",
                slot=self._focus_search)
        _action("search_next",  "Next Result",    "F3",           "Jump to next result",
                slot=self._on_search_next)
        _action("search_prev",  "Previous Result","Shift+F3",     "Jump to previous result",
                slot=self._on_search_prev)

        # ── Filter ───────────────────────────────────────────────────
        filter_action = _action("filter_apply", "Filter Explorer", None, "Toggle Filter Explorer",
                                slot=self._toggle_filter_panel, icon_name="filter")
        filter_action.setCheckable(True)
        filter_action.setChecked(False)
        _action("filter_clear", "Clear Filter",   None, "Clear active filter",
                slot=self._on_filter_cleared, icon_name="filter-x")

        # ── Convert ──────────────────────────────────────────────────
        _action("convert_to_json",   "XML → JSON",  None, "Convert XML to JSON",
                slot=self._on_convert_to_json, icon_name="arrow-right-left")
        _action("convert_save_json", "Save JSON…",  None, "Save JSON to file",
                slot=self._on_save_json, icon_name="file-down")

        # ── Help ─────────────────────────────────────────────────────
        _action("help_about", "About", None, "About XML Analyzer",
                slot=self._show_about, icon_name="help-circle")

        # ── Theme ────────────────────────────────────────────────────
        is_dark = self.theme_manager.is_dark()
        theme_icon = "sun" if is_dark else "moon"
        self.theme_action = _action("toggle_theme", "Dark Mode", "Ctrl+T", "Toggle between Light and Dark mode",
                                    slot=self._toggle_theme, icon_name=theme_icon)
        self.theme_action.setCheckable(True)
        self.theme_action.setChecked(is_dark)

        # Window-level shortcuts for all actions
        for act in self._actions.values():
            self.addAction(act)

    # ==================================================================
    #  MENU BAR
    # ==================================================================
    def _create_menu_bar(self):
        mb = self.menuBar()

        # ── File ─────────────────────────────────────────────────────
        self.file_menu = mb.addMenu("&File")
        self.file_menu.addAction(self._actions["file_new"])
        self.file_menu.addAction(self._actions["file_open"])
        self.recent_menu = self.file_menu.addMenu("Recent Files")
        self.recent_menu.setIcon(IconProvider.get_icon("history"))
        self._rebuild_recent_menu()
        self.file_menu.addAction(self._actions["file_save"])
        self.file_menu.addAction(self._actions["file_save_as"])
        self.addAction(self._actions["file_history"])
        self.file_menu.addSeparator()

        # Theme submenu inside File menu
        is_dark = self.theme_manager.is_dark()
        self.theme_menu = self.file_menu.addMenu("Theme")
        self.theme_menu.setIcon(IconProvider.get_icon("sun" if is_dark else "moon"))
        self.theme_light_act = self.theme_menu.addAction("Light Mode")
        self.theme_light_act.setCheckable(True)
        self.theme_light_act.setChecked(not is_dark)
        self.theme_light_act.triggered.connect(lambda: self.theme_manager.apply_theme("light"))

        self.theme_dark_act = self.theme_menu.addAction("Dark Mode")
        self.theme_dark_act.setCheckable(True)
        self.theme_dark_act.setChecked(is_dark)
        self.theme_dark_act.triggered.connect(lambda: self.theme_manager.apply_theme("dark"))

        self.file_menu.addSeparator()
        self.file_menu.addAction(self._actions["file_exit"])

        # ── Edit ─────────────────────────────────────────────────────
        self.edit_menu = mb.addMenu("&Edit")
        self.edit_menu.addAction(self._actions["edit_undo"])
        self.edit_menu.addAction(self._actions["edit_redo"])
        self.edit_menu.addSeparator()
        self.edit_menu.addAction(self._actions["edit_copy"])
        self.edit_menu.addAction(self._actions["edit_paste"])
        self.edit_menu.addAction(self._actions["edit_select_all"])

        # ── View ─────────────────────────────────────────────────────
        self.view_menu = mb.addMenu("&View")
        self.view_menu.addAction(self._actions["view_tree"])
        self.view_menu.addAction(self._actions["view_grid"])
        self.view_menu.addAction(self._actions["view_json"])
        self.view_menu.addAction(self._actions["file_history"])
        self.view_menu.addSeparator()
        self.view_menu.addMenu(self.theme_menu)

        # ── Filter ───────────────────────────────────────────────────
        self.filter_menu = mb.addMenu("F&ilter")
        self.filter_menu.addAction(self._actions["filter_apply"])
        self.filter_menu.addAction(self._actions["filter_clear"])

        # ── Convert ──────────────────────────────────────────────────
        self.convert_menu = mb.addMenu("&Convert")
        self.convert_menu.addAction(self._actions["convert_to_json"])
        self.convert_menu.addAction(self._actions["convert_save_json"])
        self.convert_menu.addAction(self._actions["view_json"])

        # ── Help ─────────────────────────────────────────────────────
        self.help_menu = mb.addMenu("&Help")
        self.help_menu.addAction(self._actions["help_about"])

        # Dynamic styling for dropdown menus
        menu_qss = self._get_menu_style(self.theme_manager.get_current_theme())
        for m in (self.file_menu, self.recent_menu, self.theme_menu, self.edit_menu,
                  self.view_menu, self.filter_menu, self.convert_menu, self.help_menu):
            m.setStyleSheet(menu_qss)

        # Hide native menubar; toolbar header serves as the unified single-row control bar
        mb.hide()

    # ==================================================================
    #  TOOLBAR
    # ==================================================================
    def _create_toolbar(self):
        """Unified single-row header containing menus, search panel, and help."""
        self.header_toolbar = self.addToolBar("Main Toolbar")
        self.header_toolbar.setMovable(False)
        self.header_toolbar.setFloatable(False)
        self.header_toolbar.setObjectName("header_toolbar")

        btn_style = self._get_menu_btn_style(self.theme_manager.get_current_theme())

        def _make_menu_btn(text: str, menu: QMenu, tooltip: str = "", icon_name: str = "") -> QToolButton:
            btn = QToolButton(self.header_toolbar)
            btn.setText(text)
            if icon_name:
                btn.setIcon(IconProvider.get_icon(icon_name))
                btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                btn.setIconSize(QSize(16, 16))
            btn.setToolTip(tooltip or text)
            btn.setStyleSheet(btn_style)
            btn.setMenu(menu)
            btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            return btn

        # ── Menus on the left ─────────────────────────────────────────
        self.file_btn = _make_menu_btn("File", self.file_menu, "File operations", icon_name="folder")
        self.header_toolbar.addWidget(self.file_btn)

        self.edit_btn = _make_menu_btn("Edit", self.edit_menu, "Edit operations", icon_name="edit-3")
        self.header_toolbar.addWidget(self.edit_btn)

        self.filter_btn = _make_menu_btn("Filter", self.filter_menu, "Filter operations", icon_name="filter")
        self.header_toolbar.addWidget(self.filter_btn)

        self.convert_btn = _make_menu_btn("Convert", self.convert_menu, "Conversion operations", icon_name="arrow-right-left")
        self.header_toolbar.addWidget(self.convert_btn)

        # ── History direct button ────────────────────────────────────
        self.history_btn = QToolButton(self.header_toolbar)
        self.history_btn.setText("History")
        self.history_btn.setIcon(IconProvider.get_icon("history"))
        self.history_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.history_btn.setIconSize(QSize(16, 16))
        self.history_btn.setToolTip("View document change history and audit trail (Ctrl+H)")
        self.history_btn.setStyleSheet(btn_style)
        self.history_btn.clicked.connect(self._show_history_viewer)
        self.history_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header_toolbar.addWidget(self.history_btn)

        # ── Separator right after Convert ─────────────────────────────
        self.header_toolbar.addSeparator()

        # ── Search Panel directly next to Convert ─────────────────────
        self.search_panel = SearchPanel(self.header_toolbar)
        self.search_panel.search_requested.connect(self._perform_search)
        self.header_toolbar.addWidget(self.search_panel)

        # ── Expanding Spacer to push content to the left ──────────────
        spacer = QWidget(self.header_toolbar)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        spacer.setStyleSheet("background: transparent;")
        self.header_toolbar.addWidget(spacer)
        self.help_btn = None

    # ==================================================================
    #  CENTRAL AREA — three resizable panels
    # ==================================================================
    def _create_central_area(self):
        """Set up the two-panel layout with QSplitter."""

        # Create panels
        self.center_panel = CenterPanel()
        self.center_panel.xml_content_changed.connect(self._on_text_view_content_changed)
        self.center_panel.filter_cleared.connect(self._on_filter_cleared)
        self.json_panel = JsonPreviewPanel()
        self.json_panel.close_requested.connect(self._on_json_preview_closed)

        # Horizontal splitter: Center | JSON
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setHandleWidth(3)
        self.main_splitter.addWidget(self.center_panel)
        self.main_splitter.addWidget(self.json_panel)

        # Default proportions: 70% / 30%
        self.main_splitter.setStretchFactor(0, 70)
        self.main_splitter.setStretchFactor(1, 30)

        # Search Results Panel (remains at the bottom of the work area)
        self.search_results_panel = SearchResultsPanel()
        self.search_results_panel.hide()
        self.search_results_panel.close_requested.connect(self.search_results_panel.hide)
        self.search_panel.previous_requested.connect(self.search_results_panel.select_previous)
        self.search_panel.next_requested.connect(self.search_results_panel.select_next)
        self.search_results_panel.result_selected.connect(self.center_panel.navigate_to_element)
        self.search_results_panel.match_index_changed.connect(self.search_panel.set_active_match)

        # Filter Panel (Filter Explorer on the left)
        self.filter_panel = FilterPanel()
        self.filter_panel.hide()
        self.filter_panel.close_requested.connect(self._on_filter_panel_closed)
        self.filter_panel.filter_changed.connect(self._on_filter_changed)
        self.filter_panel.target_selected.connect(self._on_target_selected)
        self.filter_panel.filter_cleared.connect(self._on_filter_cleared)

        # Vertical splitter for work area: (Center | JSON) on top, Search Results at bottom
        self.work_v_splitter = QSplitter(Qt.Orientation.Vertical)
        self.work_v_splitter.addWidget(self.main_splitter)
        self.work_v_splitter.addWidget(self.search_results_panel)
        self.work_v_splitter.setStretchFactor(0, 85)
        self.work_v_splitter.setStretchFactor(1, 15)
        self.work_v_splitter.setChildrenCollapsible(False)

        # Horizontal outer splitter: Filter Explorer on Left | Work Area on Right
        self.h_main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.h_main_splitter.addWidget(self.filter_panel)
        self.h_main_splitter.addWidget(self.work_v_splitter)
        self.h_main_splitter.setStretchFactor(0, 25)
        self.h_main_splitter.setStretchFactor(1, 75)

        # Wrap in a central widget
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(6, 6, 6, 6)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.h_main_splitter)
        self.setCentralWidget(central)
        
        # Hide JSON preview by default
        self.json_panel.hide()

    # ==================================================================
    #  STATUS BAR
    # ==================================================================
    def _create_status_bar(self):
        sb = QStatusBar()
        self.setStatusBar(sb)

        self.status_state = QLabel("Ready")
        self.status_nodes = QLabel("Nodes: 0")
        self.status_search = QLabel("Search: 0 matches")

        # Subtle separator style
        sep_style = "color: #b0b0b0; padding: 0 4px; background: transparent;"
        sep1 = QLabel("|")
        sep1.setStyleSheet(sep_style)
        sep2 = QLabel("|")
        sep2.setStyleSheet(sep_style)

        sb.addPermanentWidget(self.status_state)
        sb.addPermanentWidget(sep1)
        sb.addPermanentWidget(self.status_nodes)
        sb.addPermanentWidget(sep2)
        sb.addPermanentWidget(self.status_search)

    # ------------------------------------------------------------------
    # Public helpers for future state updates
    # ------------------------------------------------------------------
    def update_status(self, state: str = None, nodes: int = None, matches: int = None):
        """Convenience method to update one or more status bar segments."""
        if state is not None:
            self.status_state.setText(state)
        if nodes is not None:
            self.status_nodes.setText(f"Nodes: {nodes}")
        if matches is not None:
            self.status_search.setText(f"Search: {matches} match{'es' if matches != 1 else ''}")

    def get_action(self, name: str) -> QAction | None:
        """Retrieve a named action for external signal connections."""
        return self._actions.get(name)

    # ==================================================================
    #  FILE HANDLING
    # ==================================================================
    def _sync_text_view_draft(self):
        """Flushes any pending debounced edits in Text View to doc_session immediately."""
        editor = self.center_panel.text_view.text_edit
        if editor._debounce_timer.isActive():
            editor._debounce_timer.stop()
            self._on_text_view_content_changed(editor.toPlainText())

    def _on_save_file(self) -> bool:
        """Handles File -> Save action (Ctrl+S)."""
        # Check if there is an active document or text in Text View
        has_content = (self.doc_session.dom_root is not None) or bool(self.center_panel.text_view.get_xml_text().strip())
        if not has_content:
            QMessageBox.information(
                self,
                "Kaydedilecek Belge Yok",
                "Kaydedilecek açık veya düzenlenmiş bir XML belgesi bulunamadı."
            )
            return False

        # Flush any pending typing before save
        self._sync_text_view_draft()

        # Disallow saving invalid XML drafts
        if not self.doc_session.is_draft_valid:
            QMessageBox.warning(
                self,
                "Kaydetme Başarısız",
                "Sözdizimi hatası içeren XML taslağı kaydedilemez.\n"
                "Lütfen önce sözdizimi hatalarını giderin."
            )
            return False

        # If no active file path exists (e.g. pasted XML or new document), redirect to Save As
        if not self.doc_session.file_path:
            return self._on_save_file_as()

        # Atomically save to existing active path
        try:
            saved_path = self.doc_session.save_file()
            self.current_xml_path = str(saved_path)
            self._last_valid_xml_text = self.doc_session.raw_text_draft
            self._update_window_title()
            self.update_status(state=f"Kaydedildi: {saved_path.name}")
            if hasattr(self, "_history_dialog") and self._history_dialog and self._history_dialog.isVisible():
                try:
                    self._history_dialog.reload()
                except Exception:
                    pass
            return True
        except HistoryIntegrityError as e:
            logger.warning("History integrity error during save: %s", e)
            QMessageBox.warning(
                self,
                "Tarihçe Bütünlüğü Uyarısı",
                f"Belgenin geçmiş kütüğünde tutarsızlık tespit edildi:\n{e}\n\n"
                "Geçmiş zincirini korumak için kayıt durduruldu.\n"
                "XML taslağınız korunmaktadır. Belgenizi 'Farklı Kaydet' (Save As) seçeneğiyle "
                "yeni bir dosyaya kaydedebilirsiniz."
            )
            return False
        except HistoryLockError as e:
            logger.warning("History lock error during save: %s", e)
            QMessageBox.warning(
                self,
                "Dosya Kilitli",
                f"Belge geçmişi başka bir süreç tarafından kilitlenmiş:\n{e}\n\n"
                "Lütfen diğer işlemin tamamlanmasını bekleyip tekrar deneyin."
            )
            return False
        except Exception as e:
            QMessageBox.critical(
                self,
                "Kaydetme Hatası",
                f"Dosya kaydedilirken bir hata oluştu:\n{e}"
            )
            return False

    def _on_save_file_as(self) -> bool:
        """Handles File -> Save As action (Ctrl+Shift+S)."""
        # Check if there is an active document or text in Text View
        has_content = (self.doc_session.dom_root is not None) or bool(self.center_panel.text_view.get_xml_text().strip())
        if not has_content:
            QMessageBox.information(
                self,
                "Kaydedilecek Belge Yok",
                "Kaydedilecek açık veya düzenlenmiş bir XML belgesi bulunamadı."
            )
            return False

        # Flush any pending typing before save
        self._sync_text_view_draft()

        # Disallow saving invalid XML drafts
        if not self.doc_session.is_draft_valid:
            QMessageBox.warning(
                self,
                "Kaydetme Başarısız",
                "Sözdizimi hatası içeren XML taslağı kaydedilemez.\n"
                "Lütfen önce sözdizimi hatalarını giderin."
            )
            return False

        # Prompt destination path
        initial_dir = str(self.doc_session.file_path) if self.doc_session.file_path else "document.xml"
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Save XML As",
            initial_dir,
            "XML Files (*.xml);;Text Files (*.txt);;All Files (*)"
        )

        if not filepath:
            # User cancelled save dialog; preserve session and path unchanged
            return False

        # Ensure .xml extension if none provided
        target = Path(filepath)
        if not target.suffix:
            filepath = str(target.with_suffix(".xml"))

        # Atomically save to selected target path
        try:
            saved_path = self.doc_session.save_file(filepath)
            self.current_xml_path = str(saved_path)
            self._last_valid_xml_text = self.doc_session.raw_text_draft
            self._update_window_title()
            self.update_status(state=f"Farklı Kaydedildi: {saved_path.name}")
            if hasattr(self, "_history_dialog") and self._history_dialog and self._history_dialog.isVisible():
                try:
                    self._history_dialog.refresh_document(self.doc_session)
                except Exception:
                    pass
            return True
        except HistoryIntegrityError as e:
            logger.warning("History integrity error during Save As: %s", e)
            QMessageBox.warning(
                self,
                "Tarihçe Bütünlüğü Uyarısı",
                f"Hedef belgenin geçmiş kütüğünde tutarsızlık tespit edildi:\n{e}\n\n"
                "Geçmiş zincirini korumak için farklı kaydetme durduruldu.\n"
                "Mevcut XML taslağınız korunmaktadır. Başka bir dosya adı seçerek tekrar deneyebilirsiniz."
            )
            return False
        except HistoryLockError as e:
            logger.warning("History lock error during Save As: %s", e)
            QMessageBox.warning(
                self,
                "Dosya Kilitli",
                f"Hedef belge geçmişi başka bir süreç tarafından kilitlenmiş:\n{e}\n\n"
                "Lütfen diğer işlemin tamamlanmasını bekleyip tekrar deneyin."
            )
            return False
        except Exception as e:
            QMessageBox.critical(
                self,
                "Kaydetme Hatası",
                f"Dosya kaydedilirken bir hata oluştu:\n{e}"
            )
            return False

    def _show_history_viewer(self):
        """Opens or activates the read-only History Viewer dialog for the active document."""
        if not self.doc_session.file_path:
            QMessageBox.information(
                self,
                "Geçmiş Bulunamadı",
                "Geçmiş kayıtlarını görüntülemek için önce diske kaydedilmiş bir XML belgesi açmalısınız.\n\n"
                "Henüz kaydedilmemiş veya yeni oluşturulmuş belgelerin geçmiş kaydı bulunmamaktadır."
            )
            return

        if hasattr(self, "_history_dialog") and self._history_dialog and self._history_dialog.isVisible():
            self._history_dialog.reload()
            self._history_dialog.raise_()
            self._history_dialog.activateWindow()
        else:
            self._history_dialog = HistoryViewerDialog(self.doc_session, theme_manager=self.theme_manager, parent=self)
            self._history_dialog.show()

    DEFAULT_NEW_DOCUMENT_XML = '<?xml version="1.0" encoding="UTF-8"?>\n<root>\n</root>\n'

    def _maybe_save_changes(self) -> bool:
        """
        Flushes pending debounced edits and checks if the document has unsaved modifications.
        Prompts user to Save, Discard, or Cancel if dirty.
        Returns:
            True: if document is clean, or user saved successfully, or user chose Discard.
            False: if user cancelled, or chose Save but saving failed/was cancelled.
        """
        self._sync_text_view_draft()
        if not self.doc_session.is_dirty():
            return True

        filename = self.doc_session.file_path.name if self.doc_session.file_path else (
            "Untitled.xml" if self.doc_session.dom_root is not None else "Belge"
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Kaydedilmemiş Değişiklikler")
        box.setText(f"'{filename}' belgesinde kaydedilmemiş değişiklikler var.\nDeğişiklikleri kaydetmek istiyor musunuz?")
        box.setStandardButtons(
            QMessageBox.StandardButton.Save |
            QMessageBox.StandardButton.Discard |
            QMessageBox.StandardButton.Cancel
        )
        box.setDefaultButton(QMessageBox.StandardButton.Save)

        # Set Turkish button text
        save_btn = box.button(QMessageBox.StandardButton.Save)
        if save_btn:
            save_btn.setText("Kaydet")
        discard_btn = box.button(QMessageBox.StandardButton.Discard)
        if discard_btn:
            discard_btn.setText("Kaydetmeden Çık")
        cancel_btn = box.button(QMessageBox.StandardButton.Cancel)
        if cancel_btn:
            cancel_btn.setText("İptal")

        ret = box.exec()
        if ret == QMessageBox.StandardButton.Save:
            return self._on_save_file()
        elif ret == QMessageBox.StandardButton.Discard:
            return True
        else:
            return False

    def _on_new_file(self) -> bool:
        """Handles File -> New action (Ctrl+N). Prompts to save dirty document, then creates empty template."""
        if not self._maybe_save_changes():
            return False

        template = self.DEFAULT_NEW_DOCUMENT_XML
        try:
            new_root = self.doc_session.load_text(template, source_path=None)
        except Exception as e:
            QMessageBox.warning(
                self,
                "Yeni Belge Hatası",
                f"Yeni belge şablonu oluşturulamadı:\n{e}"
            )
            return False

        self._last_valid_xml_text = template
        self.current_xml_root = new_root
        self.current_xml_path = None

        self.center_panel.load_xml("Untitled.xml", new_root, xml_text=template)
        self.center_panel.set_draft_valid(True)
        self.center_panel.text_view.clear_validation_error()
        self.filter_panel.load_xml_root(new_root)
        self.json_panel.clear()

        # Reset previous search results and search panel
        self.search_panel.search_input.blockSignals(True)
        self.search_panel.clear()
        self.search_panel.search_input.blockSignals(False)
        self.search_results_panel.clear()
        self.search_results_panel.hide()
        self.center_panel.clear_search_highlight(reset_cursor=True)

        node_count = sum(1 for _ in new_root.iter("*"))
        self.update_status(state="Yeni Belge Oluşturuldu", nodes=node_count, matches=0)
        self._update_window_title()
        self._update_undo_redo_actions()

        if hasattr(self, "_history_dialog") and self._history_dialog and self._history_dialog.isVisible():
            try:
                self._history_dialog.refresh_document(self.doc_session)
            except Exception:
                pass

        return True

    def _load_recent_files(self):
        """Loads recent files list from persistent QSettings."""
        settings = QSettings("XMLAnalyzer", "XMLAnalyzer")
        raw_list = settings.value("recent_files", [])
        if isinstance(raw_list, list):
            self._recent_files = [str(p) for p in raw_list if str(p).strip()]
        else:
            self._recent_files = []

    def _save_recent_files(self):
        """Saves recent files list to persistent QSettings."""
        settings = QSettings("XMLAnalyzer", "XMLAnalyzer")
        settings.setValue("recent_files", self._recent_files)

    def _add_recent_file(self, filepath: str):
        """Adds or moves a validated XML file to the top of Recent Files."""
        norm_key = _normalize_path(filepath)
        if not norm_key:
            return
        clean_path = os.path.abspath(os.path.normpath(str(filepath).strip()))
        self._recent_files = [p for p in self._recent_files if _normalize_path(p) != norm_key]
        self._recent_files.insert(0, clean_path)
        self._recent_files = self._recent_files[:10]
        self._save_recent_files()
        self._rebuild_recent_menu()
        self.center_panel.set_recent_files(self._recent_files)

    def _remove_recent_file(self, filepath: str):
        """Removes a missing or invalid file from Recent Files."""
        norm_key = _normalize_path(filepath)
        self._recent_files = [p for p in self._recent_files if _normalize_path(p) != norm_key]
        self._save_recent_files()
        self._rebuild_recent_menu()
        self.center_panel.set_recent_files(self._recent_files)

    def _clear_recent_files(self):
        """Clears all entries from Recent Files."""
        self._recent_files.clear()
        self._save_recent_files()
        self._rebuild_recent_menu()
        self.center_panel.set_recent_files(self._recent_files)

    def _rebuild_recent_menu(self):
        """Reconstructs the File -> Recent Files submenu."""
        if not hasattr(self, "recent_menu") or self.recent_menu is None:
            return
        self.recent_menu.clear()
        if not self._recent_files:
            disabled_act = self.recent_menu.addAction("(No Recent Files)")
            disabled_act.setEnabled(False)
        else:
            for idx, path_str in enumerate(self._recent_files, start=1):
                fname = Path(path_str).name
                act = self.recent_menu.addAction(f"{idx}. {fname}")
                act.setToolTip(path_str)
                act.setStatusTip(path_str)
                act.triggered.connect(lambda checked, p=path_str: self.open_document(p))
            self.recent_menu.addSeparator()
            clear_act = self.recent_menu.addAction("Clear Recent Files")
            clear_act.triggered.connect(self._clear_recent_files)

    def open_document(self, filepath: str) -> bool:
        """Unified user-facing entry point for opening an XML document from any source.
        Performs path verification, single dirty-document check, and recent files management.
        """
        if not filepath or not str(filepath).strip():
            return False

        clean_path = os.path.abspath(os.path.normpath(str(filepath).strip()))
        norm_key = _normalize_path(clean_path)

        # 1. Verify file exists on disk and is a regular file
        if not os.path.isfile(clean_path):
            is_in_recent = any(_normalize_path(p) == norm_key for p in getattr(self, "_recent_files", []))
            if is_in_recent:
                ret = QMessageBox.warning(
                    self,
                    "Dosya Bulunamadı",
                    f"Seçilen dosya diskte bulunamadı:\n{clean_path}\n\nBu dosya son açılanlar listesinden kaldırılsın mı?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes
                )
                if ret == QMessageBox.StandardButton.Yes:
                    self._remove_recent_file(clean_path)
            else:
                QMessageBox.warning(
                    self,
                    "Dosya Bulunamadı",
                    f"Belirtilen XML dosyası bulunamadı:\n{clean_path}"
                )
            return False

        # 2. Check if the same file is already open
        if self.current_xml_path and _normalize_path(self.current_xml_path) == norm_key:
            if not self.doc_session.is_dirty():
                self._add_recent_file(clean_path)
                self.center_panel.show_editor()
                return True
            else:
                # Same file open but dirty: prompt user
                if not self._maybe_save_changes():
                    return False
                success = self.load_xml_file(clean_path)
                if success:
                    self._add_recent_file(clean_path)
                return success

        # 3. Different file: single dirty-document check
        if not self._maybe_save_changes():
            return False

        # 4. Load via canonical programmatic loader
        success = self.load_xml_file(clean_path)
        if success:
            self._add_recent_file(clean_path)
        return success

    def _open_file_dialog(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "Open XML File",
            "",
            "XML & Text Files (*.xml *.txt);;XML Files (*.xml);;Text Files (*.txt);;All Files (*)"
        )
        if not filepath:
            return
        self.open_document(filepath)

    def _on_exit(self):
        """Handles File -> Exit action (Ctrl+Q)."""
        self._user_close_requested = True
        self.close()

    def closeEvent(self, event: QCloseEvent):
        """Intercepts window close events to protect unsaved changes."""
        is_user_close = event.spontaneous() or getattr(self, "_user_close_requested", False)
        self._user_close_requested = False

        if is_user_close:
            if self._maybe_save_changes():
                if hasattr(self, "_drop_filter") and self._drop_filter:
                    app_inst = QApplication.instance()
                    if app_inst:
                        app_inst.removeEventFilter(self._drop_filter)
                    self._drop_filter = None
                event.accept()
            else:
                event.ignore()
        else:
            if hasattr(self, "_drop_filter") and self._drop_filter:
                app_inst = QApplication.instance()
                if app_inst:
                    app_inst.removeEventFilter(self._drop_filter)
                self._drop_filter = None
            event.accept()

    def _handle_load_recovery_feedback(self, filepath: str) -> None:
        """
        Evaluates recovery results from the document session after opening a file.
        Provides user feedback via Status Bar for harmless recoveries (COMMITTED, ABORTED)
        or a single QMessageBox.warning summary dialog for conflicts, corruption, quarantines,
        errors, or unknown statuses.
        """
        results = list(self.doc_session.last_recovery_results or [])
        # Clear results immediately to prevent repeated notifications on view changes or focus
        self.doc_session.last_recovery_results = []

        if not results:
            return

        status_descriptions = {
            "COMMITTED": "Bekleyen geçmiş kaydı başarıyla işlendi.",
            "ABORTED": "Tamamlanmamış geçmiş işlemi güvenli şekilde iptal edildi.",
            "CONFLICT": "Çakışma tespit edildi; önceki tamamlanmamış işlem karantinaya alındı.",
            "QUARANTINED": "Bekleyen geçmiş kaydı disk içeriğiyle eşleşmediği için karantinaya alındı.",
            "QUARANTINED_CORRUPT": "Bozuk bekleyen geçmiş kütüğü tespit edildi ve karantinaya taşındı.",
            "QUARANTINED_ARCHIVED_EPOCH": "Arşivlenmiş bir döneme ait bekleyen kayıt karantinaya alındı.",
            "TRANSACTION_ACTIVE_LOCKED": "Belge geçmişi üzerinde kilitli veya devam eden başka bir işlem tespit edildi.",
            "ERROR": "Geçmiş kurtarma sırasında hata oluştu.",
        }

        info_statuses = {"COMMITTED", "ABORTED"}
        warning_statuses = {
            "CONFLICT",
            "QUARANTINED",
            "QUARANTINED_CORRUPT",
            "QUARANTINED_ARCHIVED_EPOCH",
            "TRANSACTION_ACTIVE_LOCKED",
            "ERROR",
        }

        warning_items: list[str] = []
        info_items: list[str] = []

        for item in results:
            if isinstance(item, dict):
                raw_status = item.get("status")
                err_detail = item.get("error") or item.get("summary") or ""
                if not err_detail and "save_id" in item:
                    err_detail = f"Kayıt No: {item['save_id']}"
            else:
                raw_status = str(item)
                err_detail = ""

            status_str = str(raw_status).strip().upper() if raw_status else "UNKNOWN"

            if status_str in info_statuses:
                desc = status_descriptions.get(status_str, status_str)
                info_items.append(f"{status_str}: {desc}")
            elif status_str in warning_statuses:
                desc = status_descriptions.get(status_str, status_str)
                detail_suffix = f" ({err_detail})" if err_detail else ""
                warning_items.append(f"• [{status_str}] {desc}{detail_suffix}")
            else:
                # Bilinmeyen durumlar sessizce başarılı kabul edilmemeli; güvenli şekilde ele alınmalı ve loglanmalı
                logger.warning("Bilinmeyen geçmiş kurtarma durumu tespit edildi: %s (Kayıt: %s)", status_str, item)
                detail_suffix = f" ({err_detail})" if err_detail else ""
                warning_items.append(f"• [BİLİNMEYEN DURUM: {status_str}] Güvenlik nedeniyle incelenmesi gerekebilir.{detail_suffix}")

        filename = Path(filepath).name

        if warning_items:
            warning_details = "\n".join(warning_items)
            msg_text = (
                f"'{filename}' belgesi başarıyla açıldı, ancak belge geçmişi (History) kontrolünde "
                f"aşağıdaki durumlar tespit edildi:\n\n"
                f"{warning_details}\n\n"
                "Mevcut XML içeriğiniz korunmuştur. Ancak geçmiş bütünlüğünü güvenceye almak için "
                "belgenizi 'Farklı Kaydet' (Save As) seçeneğiyle yeni bir dosyaya kaydetmeniz önerilir."
            )
            QMessageBox.warning(self, "Geçmiş Bütünlüğü Uyarısı", msg_text)
            self.statusBar().showMessage(f"Açıldı: {filename} (Geçmiş uyarısı mevcut)", 5000)
        elif info_items:
            status_text = ", ".join(sorted(set(item.split(":")[0] for item in info_items)))
            self.statusBar().showMessage(f"Açıldı: {filename} (Geçmiş kurtarıldı: {status_text})", 5000)

    def load_xml_file(self, filepath: str) -> bool:
        """Validates and loads the XML file into all views."""
        # 1. Validate and load via session atomically
        try:
            new_root = self.doc_session.load_file(filepath)
            file_text = self.doc_session.raw_text_draft
        except Exception as e:
            QMessageBox.warning(
                self,
                "XML Load Error",
                f"Could not parse the selected XML file.\nThe current view will not be changed.\n\nError: {e}"
            )
            return False

        # 2. XML is valid, update the views
        try:
            # Normalize line endings to \n to match QPlainTextEdit internal representation
            normalized_text = file_text.replace("\r\n", "\n").replace("\r", "\n")
            self.doc_session.raw_text_draft = normalized_text
            self.doc_session.last_saved_text = normalized_text
            self._last_valid_xml_text = normalized_text

            self.center_panel.load_xml(filepath, new_root, xml_text=normalized_text)
            self.center_panel.set_draft_valid(True)
            self.center_panel.text_view.clear_validation_error()
            self.filter_panel.load_xml_root(new_root)
            self.json_panel.clear()

            # Reset previous search results and search panel
            self.search_panel.clear()
            self.search_results_panel.clear()
            self.search_results_panel.hide()
            self.center_panel.clear_search_highlight()

            self.current_xml_root = new_root
            self.current_xml_path = str(self.doc_session.file_path)
            filename = Path(filepath).name

            # Calculate total element nodes (ignoring text, comments, attributes)
            node_count = sum(1 for _ in self.current_xml_root.iter("*"))

            self.update_status(state=f"Opened: {filename}", nodes=node_count, matches=0)
            self._update_window_title()

            # Handle History recovery feedback after views and status are updated
            try:
                self._handle_load_recovery_feedback(filepath)
            except Exception as rec_err:
                logger.warning("Error evaluating recovery feedback for %s: %s", filepath, rec_err)

            # If History Viewer is open, refresh it with the newly loaded document
            if hasattr(self, "_history_dialog") and self._history_dialog and self._history_dialog.isVisible():
                try:
                    self._history_dialog.refresh_document(self.doc_session)
                except Exception as h_err:
                    logger.warning("Error refreshing history dialog on file load: %s", h_err)

            return True
        except Exception as e:
            QMessageBox.warning(
                self,
                "XML Load Error",
                f"An error occurred while populating the views:\nThe current view will not be changed.\n\nError: {e}"
            )
            return False

    def _on_text_view_content_changed(self, new_text: str):
        """Validates and loads XML edited or pasted directly in Text View."""
        if new_text == self._last_valid_xml_text:
            # If draft was invalid or validation bar is visible, user fixed/reverted back to valid text
            if not self.doc_session.is_draft_valid or self.center_panel.text_view.validation_bar.isVisible():
                self.doc_session.update_text_draft(new_text)
                self.center_panel.text_view.clear_validation_error()
                self.center_panel.set_draft_valid(True)
                self._update_window_title()
                filename = Path(self.doc_session.file_path).name if self.doc_session.file_path else "<Pasted XML>"
                node_count = sum(1 for _ in self.current_xml_root.iter("*")) if self.current_xml_root is not None else 0
                self.update_status(state=f"Ready: {filename}", nodes=node_count, matches=0)
            return

        if not new_text.strip() and self.current_xml_root is None:
            return

        # Validate draft through XmlDocumentSession
        is_valid = self.doc_session.update_text_draft(new_text)

        if not is_valid:
            # Syntax error: preserve user draft, show validation bar, do NOT revert text
            errors = self.doc_session.syntax_errors
            if errors:
                first_err = errors[0]
                line = first_err.line
                col = first_err.column
                msg = first_err.message
            else:
                line, col, msg = 1, 1, "XML syntax error"

            self.center_panel.text_view.show_validation_errors(errors)
            self.center_panel.set_draft_valid(False)
            self._update_window_title()
            if len(errors) > 1:
                self.update_status(state=f"Sözdizimi Hatası ({len(errors)} hata, ilk: Satır {line})")
            else:
                self.update_status(state=f"Sözdizimi Hatası (Satır {line})")
            return

        # Valid XML: update canonical DOM and views
        try:
            new_root = self.doc_session.dom_root
            self._last_valid_xml_text = new_text

            # Clear validation error & sync banner
            self.center_panel.text_view.clear_validation_error()
            self.center_panel.set_draft_valid(True)

            source_name = str(self.doc_session.file_path) if self.doc_session.file_path else "<Pasted XML>"
            self.center_panel.load_xml_from_text(new_text, new_root, source_name=source_name)
            self.filter_panel.load_xml_root(new_root)
            self.json_panel.clear()

            # Reset previous search results and search panel without displacing user's editor cursor
            self.search_panel.search_input.blockSignals(True)
            self.search_panel.clear()
            self.search_panel.search_input.blockSignals(False)
            self.search_results_panel.clear()
            self.search_results_panel.hide()
            self.center_panel.clear_search_highlight(reset_cursor=False)

            self.current_xml_root = new_root
            if self.doc_session.file_path:
                self.current_xml_path = str(self.doc_session.file_path)

            node_count = sum(1 for _ in self.current_xml_root.iter("*"))
            self.update_status(state="Loaded from Text View", nodes=node_count, matches=0)
            self._update_window_title()
        except Exception as e:
            self.statusBar().showMessage(f"Görünüm güncelleme hatası: {e}", 5000)

    # ==================================================================
    #  SEARCH HANDLING
    # ==================================================================
    def _perform_search(self, query: str):
        if not query.strip():
            self.search_panel.set_result_count(0)
            self.search_results_panel.clear()
            self.search_results_panel.hide()
            self.update_status(matches=0)
            self.center_panel.clear_search_highlight(reset_cursor=True)
            return

        if self.current_xml_root is None:
            self.search_panel.show_error("Arama yapılacak XML dosyası bulunamadı.")
            self.search_results_panel.clear()
            self.search_results_panel.hide()
            self.center_panel.clear_search_highlight()
            return

        # Starting a new search clears any previous search's highlight
        self.center_panel.clear_search_highlight()

        filter_mode = self.search_panel.get_filter_mode()
        results = search_xml(self.current_xml_root, query, filter_mode=filter_mode)
        self.search_panel.show_results(len(results))
        self.update_status(matches=len(results))
        
        if not results:
            self.search_results_panel.clear()
            self.search_results_panel.hide()
        else:
            self.search_results_panel.update_results(results)
            self.search_results_panel.show()
            sizes = self.work_v_splitter.sizes()
            if len(sizes) == 2 and sizes[1] < 80:
                total_h = sum(sizes) if sum(sizes) > 0 else 700
                desired_panel_h = min(250, max(80, total_h // 4))
                self.work_v_splitter.setSizes([total_h - desired_panel_h, desired_panel_h])

    def _focus_search(self):
        """Focuses the search input field and selects all text for immediate typing."""
        if hasattr(self, "search_panel") and hasattr(self.search_panel, "search_input"):
            self.search_panel.search_input.setFocus()
            self.search_panel.search_input.selectAll()

    def _on_search_next(self):
        """Navigates to the next search match."""
        if hasattr(self, "search_panel"):
            self.search_panel._on_next_clicked()

    def _on_search_prev(self):
        """Navigates to the previous search match."""
        if hasattr(self, "search_panel"):
            self.search_panel._on_prev_clicked()

    # ==================================================================
    #  FILTER HANDLING
    # ==================================================================
    def _toggle_filter_panel(self, checked: bool = None):
        if checked is None:
            new_state = not self.filter_panel.isVisible()
        else:
            new_state = checked
        self.filter_panel.setVisible(new_state)
        action = self.get_action("filter_apply")
        if action and action.isChecked() != new_state:
            action.setChecked(new_state)

    def _show_filter_panel(self):
        self.filter_panel.show()
        action = self.get_action("filter_apply")
        if action and not action.isChecked():
            action.setChecked(True)

    def _on_filter_panel_closed(self):
        self.filter_panel.hide()
        action = self.get_action("filter_apply")
        if action and action.isChecked():
            action.setChecked(False)

    def _on_filter_changed(self, target: str, path: list[str], condition: str = "", value: str = ""):
        if self.current_xml_root is None:
            return

        try:
            matched, match_count = apply_filter(self.current_xml_root, target, path, condition, value)
        except Exception as e:
            self.statusBar().showMessage(f"Filtreleme hatası: {str(e)}", 5000)
            return

        has_condition = bool(condition and condition not in ("", "(Tümü / Filtresiz)", "(All / None)") and value)

        if path:
            path_str = " → ".join(path)
            if has_condition:
                if match_count == 0:
                    filter_text = f"{target} → {path_str} {condition} '{value}' (0 matches - Kayıt bulunamadı)"
                else:
                    filter_text = f"{target} → {path_str} {condition} '{value}' ({match_count} matches)"
            else:
                if match_count == 0:
                    filter_text = f"{target} → {path_str} (0 records - Kayıt bulunamadı)"
                else:
                    filter_text = f"{target} → {path_str} ({match_count} records)"
        else:
            if has_condition:
                if match_count == 0:
                    filter_text = f"{target} {condition} '{value}' (0 matches - Kayıt bulunamadı)"
                else:
                    filter_text = f"{target} {condition} '{value}' ({match_count} matches)"
            else:
                if match_count == 0:
                    filter_text = f"{target} (0 records - Kayıt bulunamadı)"
                else:
                    filter_text = f"{target} ({match_count} records)"

        if match_count == 0:
            self.statusBar().showMessage("Filtre kriterlerine uygun kayıt bulunamadı.", 3000)

        self.center_panel.tabs.setCurrentWidget(self.center_panel.grid_view)
        self.center_panel.grid_view.apply_filter_results(matched, filter_text, path, condition, value)

    def _on_filter_applied(self, target: str, path: list[str], condition: str = "", value: str = ""):
        self._on_filter_changed(target, path, condition, value)

    def _on_target_selected(self, target: str):
        if self.current_xml_root is None:
            return
        self.center_panel.tabs.setCurrentWidget(self.center_panel.grid_view)
        self.center_panel.grid_view.expand_to_target(target)

    def _on_filter_cleared(self):
        if hasattr(self, "filter_panel") and self.filter_panel:
            self.filter_panel.reset_filter()
        if hasattr(self, "center_panel") and self.center_panel and hasattr(self.center_panel, "grid_view"):
            self.center_panel.grid_view.clear_filter()

    def _on_json_preview_closed(self):
        """Handles close button clicked on JSON Preview panel."""
        self.json_panel.hide()
        if "view_json" in self._actions:
            self._actions["view_json"].setChecked(False)

    # ==================================================================
    #  PLACEHOLDER SLOTS
    # ==================================================================
    def _toggle_json_preview(self, checked: bool):
        """Toggle the visibility of the JSON preview panel."""
        self.json_panel.setVisible(checked)

    def _placeholder(self, action_name: str):
        """Default handler for not-yet-implemented actions."""
        self.statusBar().showMessage(f"Action '{action_name}' — not implemented yet", 3000)

    def _show_about(self):
        QMessageBox.about(
            self,
            "About XML Analyzer",
            "<h3>XML Analyzer</h3>"
            "<p>A professional desktop XML analysis tool.</p>"
            "<p>Built with Python 3.12, PyQt6 &amp; lxml.</p>"
            "<p style='color:#6c7086;'>Icons: Lucide Icons (ISC License - https://lucide.dev)</p>"
            "<p style='color:#6c7086;'>Version 0.1.0-dev</p>",
        )

    # ==================================================================
    #  CONVERT / JSON HANDLING
    # ==================================================================
    def _convert_current_xml_to_json(self) -> str:
        """
        Converts active XML tree to JSON.
        If the active XML is a SOAP envelope, uses specialized SOAP-to-JSON
        input conversion. Otherwise, uses standard XML-to-JSON with Smart
        Array Detection.
        """
        if self.current_xml_root is None:
            raise ValueError("No active XML root element to convert.")

        if is_soap_envelope(self.current_xml_root):
            numeric_fields = getattr(self, "soap_numeric_fields", None)
            detector = build_soap_array_detector(self.current_xml_root)
            return soap_to_json(
                self.current_xml_root,
                numeric_fields=numeric_fields,
                force_list=detector,
            )

        detector = build_smart_array_detector(
            self.current_xml_root,
            use_prescan=True,
            use_heuristics=False,
        )
        include_root = not is_service_payload(self.current_xml_root)
        config = ConversionConfig(
            strip_namespaces=True,
            force_list=detector,
            include_root=include_root,
        )
        return xml_to_json(self.current_xml_root, config=config)

    def _on_convert_to_json(self):
        """Converts the active in-memory XML tree to JSON and displays it in JsonPreviewPanel."""
        if self.current_xml_root is None:
            QMessageBox.information(
                self,
                "XML to JSON",
                "Dönüştürülecek aktif bir XML dosyası bulunamadı.\nLütfen önce bir XML dosyası açın."
            )
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.statusBar().showMessage("XML to JSON dönüştürülüyor...", 0)
        try:
            json_text = self._convert_current_xml_to_json()
            self.json_panel.set_json(json_text)

            # Ensure preview panel is visible and keep the View action checked
            self.json_panel.show()
            json_action = self.get_action("view_json")
            if json_action:
                json_action.setChecked(True)

            self.update_status(state="XML → JSON dönüştürüldü")
            self.statusBar().showMessage("XML başarıyla JSON'a dönüştürüldü.", 4000)
        except Exception as e:
            QMessageBox.critical(
                self,
                "Conversion Error",
                f"XML to JSON dönüşümü sırasında bir hata oluştu:\n\n{e}"
            )
            self.update_status(state="Dönüşüm başarısız")
            self.statusBar().showMessage(f"Dönüşüm hatası: {e}", 5000)
        finally:
            QApplication.restoreOverrideCursor()

    def _on_save_json(self):
        """Saves the JSON representation to a file chosen by the user."""
        if self.current_xml_root is None:
            QMessageBox.information(
                self,
                "Save JSON",
                "Kaydedilecek aktif bir XML dosyası bulunamadı.\nLütfen önce bir XML dosyası açın."
            )
            return

        # If preview is empty, perform conversion on the fly
        json_text = self.json_panel.get_text().strip()
        if not json_text:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            self.statusBar().showMessage("XML to JSON dönüştürülüyor...", 0)
            try:
                json_text = self._convert_current_xml_to_json()
                self.json_panel.set_json(json_text)
                self.json_panel.show()
                json_action = self.get_action("view_json")
                if json_action:
                    json_action.setChecked(True)
            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Conversion Error",
                    f"JSON oluşturulurken hata oluştu:\n\n{e}"
                )
                self.statusBar().showMessage(f"Dönüşüm hatası: {e}", 5000)
                return
            finally:
                QApplication.restoreOverrideCursor()

        default_filename = (
            str(Path(self.current_xml_path).with_suffix(".json"))
            if self.current_xml_path
            else "converted.json"
        )

        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Save JSON File",
            default_filename,
            "JSON Files (*.json);;All Files (*)"
        )

        if not filepath:
            return

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(json_text)
            filename = Path(filepath).name
            self.statusBar().showMessage(f"JSON başarıyla kaydedildi: {filename}", 5000)
        except Exception as e:
            QMessageBox.critical(
                self,
                "Save Error",
                f"JSON dosyası kaydedilirken bir hata oluştu:\n\n{e}"
            )
            self.statusBar().showMessage(f"Kayıt hatası: {e}", 5000)

    # ==================================================================
    #  EDIT HANDLING
    # ==================================================================
    def _setup_undo_redo_sync(self):
        """Connects signals for synchronizing Undo/Redo actions with the active editor and tab."""
        editor = self.center_panel.text_view.text_edit
        editor.undoAvailable.connect(self._on_undo_available_changed)
        editor.redoAvailable.connect(self._on_redo_available_changed)
        self.center_panel.grid_view.undo_available_changed.connect(self._on_grid_undo_available_changed)
        self.center_panel.grid_view.redo_available_changed.connect(self._on_grid_redo_available_changed)
        self.center_panel.grid_document_modified.connect(self._on_grid_document_modified)
        self.center_panel.tabs.currentChanged.connect(self._update_undo_redo_actions)
        self._update_undo_redo_actions()

    def _on_undo_available_changed(self, available: bool):
        """Updates Undo action enabled state when Text View is active."""
        if hasattr(self, "center_panel") and self.center_panel.tabs.currentWidget() == self.center_panel.text_view:
            action = self._actions.get("edit_undo")
            if action:
                action.setEnabled(available)

    def _on_redo_available_changed(self, available: bool):
        """Updates Redo action enabled state when Text View is active."""
        if hasattr(self, "center_panel") and self.center_panel.tabs.currentWidget() == self.center_panel.text_view:
            action = self._actions.get("edit_redo")
            if action:
                action.setEnabled(available)

    def _on_grid_undo_available_changed(self, available: bool):
        """Updates Undo action enabled state when Grid View is active."""
        if hasattr(self, "center_panel") and self.center_panel.tabs.currentWidget() == self.center_panel.grid_view:
            action = self._actions.get("edit_undo")
            if action:
                action.setEnabled(available)

    def _on_grid_redo_available_changed(self, available: bool):
        """Updates Redo action enabled state when Grid View is active."""
        if hasattr(self, "center_panel") and self.center_panel.tabs.currentWidget() == self.center_panel.grid_view:
            action = self._actions.get("edit_redo")
            if action:
                action.setEnabled(available)

    def _update_undo_redo_actions(self, *args):
        """Synchronizes Undo and Redo action states based on current tab and editor availability."""
        undo_action = self._actions.get("edit_undo")
        redo_action = self._actions.get("edit_redo")
        if not undo_action or not redo_action:
            return

        if not hasattr(self, "center_panel"):
            undo_action.setEnabled(False)
            redo_action.setEnabled(False)
            return

        current = self.center_panel.tabs.currentWidget()
        if current == self.center_panel.text_view:
            if getattr(self, "_needs_text_view_sync", False):
                self.center_panel.text_view.set_xml_text(self.doc_session.raw_text_draft)
                self._last_valid_xml_text = self.doc_session.raw_text_draft
                self._needs_text_view_sync = False
            doc = self.center_panel.text_view.text_edit.document()
            undo_action.setEnabled(doc.isUndoAvailable())
            redo_action.setEnabled(doc.isRedoAvailable())
        elif current == self.center_panel.grid_view:
            undo_action.setEnabled(self.center_panel.grid_view.can_undo())
            redo_action.setEnabled(self.center_panel.grid_view.can_redo())
        else:
            undo_action.setEnabled(False)
            redo_action.setEnabled(False)

    def _on_undo(self):
        """Triggers undo in the active view (Text View or Grid View)."""
        if not hasattr(self, "center_panel"):
            return
        current = self.center_panel.tabs.currentWidget()
        if current == self.center_panel.text_view:
            self.center_panel.text_view.text_edit.undo()
        elif current == self.center_panel.grid_view:
            self.center_panel.grid_view.undo()

    def _on_redo(self):
        """Triggers redo in the active view (Text View or Grid View)."""
        if not hasattr(self, "center_panel"):
            return
        current = self.center_panel.tabs.currentWidget()
        if current == self.center_panel.text_view:
            self.center_panel.text_view.text_edit.redo()
        elif current == self.center_panel.grid_view:
            self.center_panel.grid_view.redo()

    def _on_grid_document_modified(self):
        """Coordinates view sync, window dirty title, and search highlight clearing after grid mutations."""
        self._needs_text_view_sync = True
        self._last_valid_xml_text = self.doc_session.raw_text_draft
        self._update_window_title()
        self.center_panel.clear_search_highlight()
        self._update_undo_redo_actions()

    def _on_grid_node_value_changed(self, node, old_val: str, new_val: str):
        """Handles grid cell edit: updates dirty state, window title, and clears search highlights."""
        self._on_grid_document_modified()

    def _on_copy(self):
        """Copies selection from the focused widget or JSON preview."""
        focused = QApplication.focusWidget()
        if focused and hasattr(focused, "copy"):
            focused.copy()
        elif hasattr(self, "json_panel") and self.json_panel.text_edit.textCursor().hasSelection():
            self.json_panel.text_edit.copy()

    def _on_paste(self):
        """Pastes clipboard text into the focused widget or Text View."""
        focused = QApplication.focusWidget()
        if focused and hasattr(focused, "paste"):
            focused.paste()
        elif hasattr(self, "center_panel") and hasattr(self.center_panel, "text_view"):
            self.center_panel.text_view.text_edit.paste()

    def _on_select_all(self):
        """Selects all text in the focused widget or JSON preview."""
        focused = QApplication.focusWidget()
        if focused and hasattr(focused, "selectAll"):
            focused.selectAll()
        elif hasattr(self, "json_panel") and self.json_panel.isVisible():
            self.json_panel.text_edit.selectAll()

    # ==================================================================
    #  THEME MANAGEMENT
    # ==================================================================
    def _toggle_theme(self):
        """Toggles between Light and Dark themes."""
        self.theme_manager.toggle_theme()

    def _on_theme_changed(self, theme_name: str):
        """Central listener for theme change events."""
        is_dark = (theme_name == "dark")
        if hasattr(self, "theme_action"):
            self.theme_action.blockSignals(True)
            self.theme_action.setChecked(is_dark)
            self.theme_action.setIcon(IconProvider.get_icon("sun" if is_dark else "moon"))
            self.theme_action.blockSignals(False)

        if hasattr(self, "theme_light_act"):
            self.theme_light_act.setChecked(not is_dark)
        if hasattr(self, "theme_dark_act"):
            self.theme_dark_act.setChecked(is_dark)
        if hasattr(self, "theme_menu") and self.theme_menu:
            self.theme_menu.setIcon(IconProvider.get_icon("sun" if is_dark else "moon"))

        # Re-apply menu and toolbar styles
        menu_qss = self._get_menu_style(theme_name)
        for m in (getattr(self, "file_menu", None), getattr(self, "recent_menu", None),
                  getattr(self, "theme_menu", None), getattr(self, "edit_menu", None),
                  getattr(self, "view_menu", None), getattr(self, "filter_menu", None),
                  getattr(self, "convert_menu", None), getattr(self, "help_menu", None)):
            if m:
                m.setStyleSheet(menu_qss)

        btn_qss = self._get_menu_btn_style(theme_name)
        for b in (getattr(self, "file_btn", None), getattr(self, "edit_btn", None),
                  getattr(self, "filter_btn", None), getattr(self, "convert_btn", None),
                  getattr(self, "history_btn", None)):
            if b:
                b.setStyleSheet(btn_qss)

        # Re-apply all icons across actions, buttons, and child panels
        self._reapply_theme_icons(theme_name)

    def _reapply_theme_icons(self, theme_name: Optional[str] = None):
        """Re-assigns icons across all actions, menus, toolbar buttons, and child widgets."""
        t = theme_name or self.theme_manager.get_current_theme()
        is_dark = (t == "dark")

        # 1. Action icons
        action_icon_map = {
            "file_new": "file-plus",
            "file_open": "folder-open",
            "file_save": "save",
            "file_save_as": "file-down",
            "file_history": "history",
            "file_exit": "log-out",
            "edit_undo": "undo-2",
            "edit_redo": "redo-2",
            "edit_copy": "copy",
            "edit_paste": "clipboard-paste",
            "edit_select_all": "check-square",
            "view_json": "panel-right",
            "filter_apply": "filter",
            "filter_clear": "filter-x",
            "convert_to_json": "arrow-right-left",
            "convert_save_json": "file-code",
            "help_about": "help-circle",
            "toggle_theme": "sun" if is_dark else "moon",
        }
        for act_name, icon_name in action_icon_map.items():
            if act_name in self._actions:
                self._actions[act_name].setIcon(IconProvider.get_icon(icon_name))

        # 2. Menus
        if hasattr(self, "recent_menu") and self.recent_menu:
            self.recent_menu.setIcon(IconProvider.get_icon("history"))
        if hasattr(self, "theme_menu") and self.theme_menu:
            self.theme_menu.setIcon(IconProvider.get_icon("sun" if is_dark else "moon"))

        # 3. Toolbar buttons
        if hasattr(self, "file_btn") and self.file_btn:
            self.file_btn.setIcon(IconProvider.get_icon("folder"))
        if hasattr(self, "edit_btn") and self.edit_btn:
            self.edit_btn.setIcon(IconProvider.get_icon("edit-3"))
        if hasattr(self, "filter_btn") and self.filter_btn:
            self.filter_btn.setIcon(IconProvider.get_icon("filter"))
        if hasattr(self, "convert_btn") and self.convert_btn:
            self.convert_btn.setIcon(IconProvider.get_icon("arrow-right-left"))
        if hasattr(self, "history_btn") and self.history_btn:
            self.history_btn.setIcon(IconProvider.get_icon("history"))

        # 4. Child panels
        if hasattr(self, "search_panel") and hasattr(self.search_panel, "refresh_theme"):
            self.search_panel.refresh_theme(t)
        if hasattr(self, "search_results_panel") and hasattr(self.search_results_panel, "refresh_theme"):
            self.search_results_panel.refresh_theme(t)
        if hasattr(self, "center_panel") and hasattr(self.center_panel, "refresh_theme"):
            self.center_panel.refresh_theme(t)
        if hasattr(self, "json_panel") and hasattr(self.json_panel, "refresh_theme"):
            self.json_panel.refresh_theme(t)
        if hasattr(self, "filter_panel") and hasattr(self.filter_panel, "refresh_theme"):
            self.filter_panel.refresh_theme(t)
