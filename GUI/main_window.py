"""
GUI/main_window.py — Main Application Window

Assembles the full XML Analyzer interface:
  • Menu bar with File / Edit / View / Search / Filter / Convert / Help
  • Toolbar with key actions + embedded search panel
  • Three resizable panels (XML Tree | XML Grid | JSON Preview)
  • Status bar

All QActions are wired as stubs so functionality can be connected later.
"""

import os
from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QToolBar, QStatusBar, QLabel, QFileDialog, QMessageBox,
    QSizePolicy,
)
from PyQt6.QtGui import QAction, QIcon, QKeySequence
from PyQt6.QtCore import Qt, QSize

from GUI.tree_view import XmlTreePanel
from GUI.center_panel import CenterPanel
from GUI.json_preview import JsonPreviewPanel
from GUI.search_panel import SearchPanel


class MainWindow(QMainWindow):
    """Top-level application window for XML Analyzer."""

    APP_TITLE = "XML Analyzer"
    DEFAULT_SIZE = (1400, 900)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(self.APP_TITLE)
        self.resize(*self.DEFAULT_SIZE)
        self.setMinimumSize(800, 500)

        # ── Load stylesheet ──────────────────────────────────────────
        self._load_stylesheet()

        # ── Actions (must be created before menus & toolbars) ────────
        self._actions: dict[str, QAction] = {}
        self._create_actions()

        # ── UI assembly ──────────────────────────────────────────────
        self._create_menu_bar()
        self._create_toolbar()
        self._create_central_area()
        self._create_status_bar()

    # ==================================================================
    #  STYLESHEET
    # ==================================================================
    def _load_stylesheet(self):
        """Load the QSS light theme from resources/styles/."""
        qss_path = Path(__file__).resolve().parent.parent / "resources" / "styles" / "light_theme.qss"
        if qss_path.exists():
            with open(qss_path, "r", encoding="utf-8") as f:
                self.setStyleSheet(f.read())

    # ==================================================================
    #  ACTIONS
    # ==================================================================
    def _create_actions(self):
        """Create all QActions with shortcuts.  Actual logic is not
        connected yet — each action calls a placeholder slot."""

        def _action(name, text, shortcut=None, tip=None, slot=None):
            action = QAction(text, self)
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
        _action("file_open",    "Open XML…",   "Ctrl+O",  "Open an XML file")
        _action("file_save",    "Save",         "Ctrl+S",  "Save current file")
        _action("file_save_as", "Save As…",     "Ctrl+Shift+S", "Save file as…")
        _action("file_exit",    "Exit",         "Ctrl+Q",  "Quit application",
                slot=self.close)

        # ── Edit ─────────────────────────────────────────────────────
        _action("edit_undo",       "Undo",       "Ctrl+Z",  "Undo last action")
        _action("edit_redo",       "Redo",       "Ctrl+Y",  "Redo last action")
        _action("edit_copy",       "Copy",       "Ctrl+C",  "Copy selection")
        _action("edit_select_all", "Select All", "Ctrl+A",  "Select all")

        # ── View ─────────────────────────────────────────────────────
        _action("view_tree",  "Tree View",    None, "Switch to tree view")
        _action("view_grid",  "Grid View",    None, "Switch to grid view")
        _action("view_json",  "JSON Preview", None, "Toggle JSON preview")

        # ── Search ───────────────────────────────────────────────────
        _action("search_find",  "Search XML…",    "Ctrl+F",       "Search within XML")
        _action("search_next",  "Next Result",    "F3",           "Jump to next result")
        _action("search_prev",  "Previous Result","Shift+F3",     "Jump to previous result")

        # ── Filter ───────────────────────────────────────────────────
        _action("filter_apply", "Apply Filter",   None, "Apply current filter")
        _action("filter_clear", "Clear Filter",   None, "Clear active filter")

        # ── Convert ──────────────────────────────────────────────────
        _action("convert_to_json",   "XML → JSON",  None, "Convert XML to JSON")
        _action("convert_save_json", "Save JSON…",  None, "Save JSON to file")

        # ── Help ─────────────────────────────────────────────────────
        _action("help_about", "About", None, "About XML Analyzer",
                slot=self._show_about)

    # ==================================================================
    #  MENU BAR
    # ==================================================================
    def _create_menu_bar(self):
        mb = self.menuBar()

        # ── File ─────────────────────────────────────────────────────
        file_menu = mb.addMenu("&File")
        file_menu.addAction(self._actions["file_open"])
        file_menu.addAction(self._actions["file_save"])
        file_menu.addAction(self._actions["file_save_as"])
        file_menu.addSeparator()
        file_menu.addAction(self._actions["file_exit"])

        # ── Edit ─────────────────────────────────────────────────────
        edit_menu = mb.addMenu("&Edit")
        edit_menu.addAction(self._actions["edit_undo"])
        edit_menu.addAction(self._actions["edit_redo"])
        edit_menu.addSeparator()
        edit_menu.addAction(self._actions["edit_copy"])
        edit_menu.addAction(self._actions["edit_select_all"])

        # ── View ─────────────────────────────────────────────────────
        view_menu = mb.addMenu("&View")
        view_menu.addAction(self._actions["view_tree"])
        view_menu.addAction(self._actions["view_grid"])
        view_menu.addAction(self._actions["view_json"])

        # ── Search ───────────────────────────────────────────────────
        search_menu = mb.addMenu("&Search")
        search_menu.addAction(self._actions["search_find"])
        search_menu.addAction(self._actions["search_next"])
        search_menu.addAction(self._actions["search_prev"])

        # ── Filter ───────────────────────────────────────────────────
        filter_menu = mb.addMenu("F&ilter")
        filter_menu.addAction(self._actions["filter_apply"])
        filter_menu.addAction(self._actions["filter_clear"])

        # ── Convert ──────────────────────────────────────────────────
        convert_menu = mb.addMenu("&Convert")
        convert_menu.addAction(self._actions["convert_to_json"])
        convert_menu.addAction(self._actions["convert_save_json"])

        # ── Help ─────────────────────────────────────────────────────
        help_menu = mb.addMenu("&Help")
        help_menu.addAction(self._actions["help_about"])

    # ==================================================================
    #  TOOLBAR
    # ==================================================================
    def _create_toolbar(self):
        tb = QToolBar("Main Toolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(18, 18))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(tb)

        # Key actions
        tb.addAction(self._actions["file_open"])
        tb.addAction(self._actions["file_save"])
        tb.addSeparator()
        tb.addAction(self._actions["convert_to_json"])
        tb.addAction(self._actions["filter_apply"])
        tb.addSeparator()

        # Embed the search panel
        self.search_panel = SearchPanel()
        tb.addWidget(self.search_panel)

        # Spacer to push view toggles to the right
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)

        tb.addAction(self._actions["view_tree"])
        tb.addAction(self._actions["view_grid"])

    # ==================================================================
    #  CENTRAL AREA — three resizable panels
    # ==================================================================
    def _create_central_area(self):
        """Set up the three-panel layout with QSplitter."""

        # Create panels
        self.tree_panel = XmlTreePanel()
        self.center_panel = CenterPanel()
        self.json_panel = JsonPreviewPanel()

        # Horizontal splitter: Tree | Center | JSON
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setHandleWidth(3)
        self.main_splitter.addWidget(self.tree_panel)
        self.main_splitter.addWidget(self.center_panel)
        self.main_splitter.addWidget(self.json_panel)

        # Default proportions: 25% / 45% / 30%
        self.main_splitter.setStretchFactor(0, 25)
        self.main_splitter.setStretchFactor(1, 45)
        self.main_splitter.setStretchFactor(2, 30)

        # Wrap in a central widget
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(6, 6, 6, 6)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.main_splitter)
        self.setCentralWidget(central)

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
    #  PLACEHOLDER SLOTS
    # ==================================================================
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
            "<p style='color:#6c7086;'>Version 0.1.0-dev</p>",
        )
