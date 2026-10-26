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
    QSizePolicy, QApplication,
)
from PyQt6.QtGui import QAction, QIcon, QKeySequence, QCloseEvent
from PyQt6.QtCore import Qt, QSize
from lxml import etree
from XML.document_session import XmlDocumentSession
from GUI.center_panel import CenterPanel
from SEARCH.engine import search_xml
from GUI.json_preview import JsonPreviewPanel
from GUI.search_panel import SearchPanel
from SEARCH.result_list import SearchResultsPanel
from GUI.filter_view import FilterPanel
from FILTER.engine import apply_filter
from CONVERTER.xml_to_json import ConversionConfig, xml_to_json
from CONVERTER.heuristics import build_smart_array_detector
from CONVERTER.soap import is_soap_envelope, is_service_payload, soap_to_json, build_soap_array_detector


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
        self.doc_session = XmlDocumentSession()
        self.current_xml_path = None
        self.current_xml_root = None
        self._last_valid_xml_text = ""
        self.soap_numeric_fields = None
        self.update_status(state="No XML file opened")
        self._update_window_title()
        self._setup_undo_redo_sync()

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
        _action("file_new",     "New",         "Ctrl+N",  "Create a new XML document",
                slot=self._on_new_file)
        _action("file_open",    "Open XML…",   "Ctrl+O",  "Open an XML file",
                slot=self._open_file_dialog)
        _action("file_save",    "Save",         "Ctrl+S",  "Save current file",
                slot=self._on_save_file)
        _action("file_save_as", "Save As…",     "Ctrl+Shift+S", "Save file as…",
                slot=self._on_save_file_as)
        _action("file_exit",    "Exit",         "Ctrl+Q",  "Quit application",
                slot=self._on_exit)

        # ── Edit ─────────────────────────────────────────────────────
        _action("edit_undo",       "Undo",       "Ctrl+Z",  "Undo last action",
                slot=self._on_undo)
        _action("edit_redo",       "Redo",       "Ctrl+Y",  "Redo last action",
                slot=self._on_redo)
        _action("edit_copy",       "Copy",       "Ctrl+C",  "Copy selection",
                slot=self._on_copy)
        _action("edit_paste",      "Paste",      "Ctrl+V",  "Paste from clipboard",
                slot=self._on_paste)
        _action("edit_select_all", "Select All", "Ctrl+A",  "Select all",
                slot=self._on_select_all)

        # ── View ─────────────────────────────────────────────────────
        _action("view_tree",  "Tree View",    None, "Switch to tree view")
        _action("view_grid",  "Grid View",    None, "Switch to grid view")
        json_action = _action("view_json",  "JSON Preview", None, "Toggle JSON preview", slot=self._toggle_json_preview)
        json_action.setCheckable(True)
        json_action.setChecked(False)

        # ── Search ───────────────────────────────────────────────────
        _action("search_find",  "Search XML…",    "Ctrl+F",       "Search within XML")
        _action("search_next",  "Next Result",    "F3",           "Jump to next result")
        _action("search_prev",  "Previous Result","Shift+F3",     "Jump to previous result")

        # ── Filter ───────────────────────────────────────────────────
        _action("filter_apply", "Filter Explorer",     None, "Toggle Filter Explorer", slot=self._toggle_filter_panel)
        _action("filter_clear", "Clear Filter",   None, "Clear active filter", slot=self._on_filter_cleared)

        # ── Convert ──────────────────────────────────────────────────
        _action("convert_to_json",   "XML → JSON",  None, "Convert XML to JSON",
                slot=self._on_convert_to_json)
        _action("convert_save_json", "Save JSON…",  None, "Save JSON to file",
                slot=self._on_save_json)

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
        file_menu.addAction(self._actions["file_new"])
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
        edit_menu.addAction(self._actions["edit_paste"])
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
        self.search_panel.search_requested.connect(self._perform_search)
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
        """Set up the two-panel layout with QSplitter."""

        # Create panels
        self.center_panel = CenterPanel()
        self.center_panel.xml_content_changed.connect(self._on_text_view_content_changed)
        self.json_panel = JsonPreviewPanel()

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
        self.filter_panel.close_requested.connect(self.filter_panel.hide)
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
            return True
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
            return True
        except Exception as e:
            QMessageBox.critical(
                self,
                "Kaydetme Hatası",
                f"Dosya kaydedilirken bir hata oluştu:\n{e}"
            )
            return False

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
        return True

    def _open_file_dialog(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "Open XML File",
            "",
            "XML & Text Files (*.xml *.txt);;XML Files (*.xml);;Text Files (*.txt);;All Files (*)"
        )
        if not filepath:
            return
        if not self._maybe_save_changes():
            return
        self.load_xml_file(filepath)

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
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

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

    # ==================================================================
    #  FILTER HANDLING
    # ==================================================================
    def _toggle_filter_panel(self):
        if self.filter_panel.isVisible():
            self.filter_panel.hide()
        else:
            self.filter_panel.show()

    def _show_filter_panel(self):
        self.filter_panel.show()

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
        self.center_panel.grid_view.clear_filter()

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

    def _update_undo_redo_actions(self, *args):
        """Synchronizes Undo and Redo action states based on current tab and editor availability."""
        undo_action = self._actions.get("edit_undo")
        redo_action = self._actions.get("edit_redo")
        if not undo_action or not redo_action:
            return

        if hasattr(self, "center_panel") and self.center_panel.tabs.currentWidget() == self.center_panel.text_view:
            doc = self.center_panel.text_view.text_edit.document()
            undo_action.setEnabled(doc.isUndoAvailable())
            redo_action.setEnabled(doc.isRedoAvailable())
        else:
            undo_action.setEnabled(False)
            redo_action.setEnabled(False)

    def _on_undo(self):
        """Triggers undo in Text View if it is active."""
        if hasattr(self, "center_panel") and self.center_panel.tabs.currentWidget() == self.center_panel.text_view:
            self.center_panel.text_view.text_edit.undo()

    def _on_redo(self):
        """Triggers redo in Text View if it is active."""
        if hasattr(self, "center_panel") and self.center_panel.tabs.currentWidget() == self.center_panel.text_view:
            self.center_panel.text_view.text_edit.redo()

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
