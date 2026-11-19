"""
GUI/history_viewer.py — XML Document History Viewer & Audit Trail

A read-only, professional audit viewer dialog for XML Analyzer:
  • Chronological list of ChangeSets and Origin Baseline.
  • Detailed side-by-side inspection of fine-grained ChangeRecords.
  • Color-coded badges for all ChangeTypes.
  • Read-only safety (never alters History files or transactions).
  • Full TXT and JSONL export functionality.
  • Real-time theme synchronization (Light / Dark mode).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QSize, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QFont, QIcon, QKeySequence
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLabel, QPushButton, QListWidget, QListWidgetItem,
    QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit,
    QFrame, QStackedWidget, QFileDialog, QMessageBox,
    QMenu, QTextEdit, QAbstractItemView, QSizePolicy,
    QApplication,
)

from XML.history_models import ChangeSet, ChangeRecord, ChangeType, DocumentMetadata
from XML.history_manager import get_document_id
from XML.document_session import XmlDocumentSession
from GUI.theme_manager import ThemeManager, PALETTE
from GUI.icon_provider import IconProvider

logger = logging.getLogger(__name__)


def format_local_timestamp(iso_str: Optional[str]) -> str:
    """
    Converts an ISO 8601 UTC timestamp to the user's local timezone
    and formats it as 'YYYY-MM-DD HH:MM:SS'.
    Gracefully handles naive strings, 'Z', or timezone offsets.
    """
    if not iso_str:
        return "Bilinmiyor"
    try:
        clean_str = iso_str.strip()
        if clean_str.endswith("Z"):
            clean_str = clean_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(clean_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso_str.replace("T", " ")[:19]


# ---------------------------------------------------------------------------
# ChangeType Visual Configuration & Friendly Labels
# ---------------------------------------------------------------------------

CHANGE_TYPE_CONFIG: dict[str, dict[str, str]] = {
    "ELEMENT_INSERT": {
        "label": "Element Ekleme",
        "bg_light": "#e6f4ea", "fg_light": "#137333",
        "bg_dark": "#133e24", "fg_dark": "#81c995",
    },
    "ELEMENT_DELETE": {
        "label": "Element Silme",
        "bg_light": "#fce8e6", "fg_light": "#c5221f",
        "bg_dark": "#4c1a1a", "fg_dark": "#f28b82",
    },
    "ELEMENT_RENAME": {
        "label": "Yeniden Adlandırma",
        "bg_light": "#feefe3", "fg_light": "#b06000",
        "bg_dark": "#3d2e16", "fg_dark": "#fdd663",
    },
    "VALUE_UPDATE": {
        "label": "Değer Değişimi",
        "bg_light": "#e8f0fe", "fg_light": "#1a73e8",
        "bg_dark": "#17345f", "fg_dark": "#8ab4f8",
    },
    "ATTR_UPDATE": {
        "label": "Attr Güncelleme",
        "bg_light": "#f3e8fd", "fg_light": "#8430ce",
        "bg_dark": "#361b58", "fg_dark": "#c58af9",
    },
    "ATTR_INSERT": {
        "label": "Attr Ekleme",
        "bg_light": "#e1f5fe", "fg_light": "#0277bd",
        "bg_dark": "#0f364d", "fg_dark": "#4fc3f7",
    },
    "ATTR_DELETE": {
        "label": "Attr Silme",
        "bg_light": "#fbe9e7", "fg_light": "#d84315",
        "bg_dark": "#421e14", "fg_dark": "#ff8a65",
    },
    "ORDER_CHANGED": {
        "label": "Sıralama Değişimi",
        "bg_light": "#f1f3f4", "fg_light": "#5f6368",
        "bg_dark": "#2d3135", "fg_dark": "#bdc1c6",
    },
    "ELEMENT_MOVE": {
        "label": "Element Taşıma",
        "bg_light": "#f1f3f4", "fg_light": "#5f6368",
        "bg_dark": "#2d3135", "fg_dark": "#bdc1c6",
    },
}


def _get_type_config(change_type: str | ChangeType) -> dict[str, str]:
    name = change_type.value if hasattr(change_type, "value") else str(change_type)
    return CHANGE_TYPE_CONFIG.get(name, {
        "label": name,
        "bg_light": "#f1f3f4", "fg_light": "#3c4043",
        "bg_dark": "#2a2b2e", "fg_dark": "#e8eaed",
    })


def _format_node_type(node_type: str | Any, change_type: str | Any) -> str:
    """Returns a friendly, context-aware display label for an XML node type."""
    c_name = change_type.value if hasattr(change_type, "value") else str(change_type)
    n_name = node_type.value if hasattr(node_type, "value") else str(node_type)

    if c_name == "VALUE_UPDATE":
        if n_name == "tail":
            return "Tail"
        return "Value"
    elif n_name == "attribute" or c_name.startswith("ATTR_"):
        return "Attribute"
    elif n_name == "text":
        return "Value"
    elif n_name == "tail":
        return "Tail"
    else:
        return "Element"


# ---------------------------------------------------------------------------
# History Viewer Dialog
# ---------------------------------------------------------------------------

class HistoryViewerDialog(QDialog):
    """
    Standalone read-only audit inspection window for XML change history.
    """

    def __init__(
        self,
        doc_session: XmlDocumentSession,
        theme_manager: Optional[ThemeManager] = None,
        parent: Optional[QWidget] = None
    ):
        super().__init__(parent)
        self.doc_session = doc_session

        if theme_manager is not None:
            self.theme_manager = theme_manager
        elif parent is not None and hasattr(parent, "theme_manager"):
            self.theme_manager = parent.theme_manager
        else:
            self.theme_manager = ThemeManager(self)

        self._audit_data: Optional[Dict[str, Any]] = None
        self._current_records: List[ChangeSet] = []
        self._selected_item_data: Optional[Dict[str, Any]] = None

        self._setup_window_properties()
        self._init_ui()
        self._apply_theme()

        # Connect live theme updates
        self.theme_manager.theme_changed.connect(self._apply_theme)

        # Initial data load
        self.reload()

    def _setup_window_properties(self):
        self.setWindowTitle("XML Document History Viewer — Audit Trail")
        self.resize(1180, 740)
        self.setMinimumSize(960, 600)
        self.setWindowIcon(IconProvider.get_icon("history"))

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 14, 16, 14)
        root_layout.setSpacing(10)

        # ── 1. Top Header Bar ──────────────────────────────────────────
        self.header_frame = QFrame(self)
        self.header_frame.setObjectName("headerFrame")
        self.header_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header_layout = QHBoxLayout(self.header_frame)
        header_layout.setContentsMargins(12, 10, 12, 10)
        header_layout.setSpacing(12)

        self.icon_lbl = QLabel(self.header_frame)
        self.icon_lbl.setObjectName("headerIcon")
        self.icon_lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.icon_lbl.setStyleSheet("background: transparent; border: none;")
        self.icon_lbl.setPixmap(IconProvider.get_pixmap("history", size=24, widget=self))
        header_layout.addWidget(self.icon_lbl)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self.title_lbl = QLabel("XML BELGE GEÇMİŞ DENETİMİ (AUDIT TRAIL)", self)
        self.title_lbl.setObjectName("viewerTitle")
        font_title = QFont()
        font_title.setPointSize(12)
        font_title.setBold(True)
        self.title_lbl.setFont(font_title)
        title_box.addWidget(self.title_lbl)

        self.subtitle_lbl = QLabel("Açık belge geçmişi inceleniyor...", self)
        self.subtitle_lbl.setObjectName("viewerSubtitle")
        title_box.addWidget(self.subtitle_lbl)
        header_layout.addLayout(title_box)

        header_layout.addStretch()

        # Integrity Status Badge
        self.status_badge = QLabel("Denetleniyor", self)
        self.status_badge.setObjectName("statusBadge")
        header_layout.addWidget(self.status_badge)

        # Refresh button
        self.btn_refresh = QPushButton("Yenile", self)
        self.btn_refresh.setIcon(IconProvider.get_icon("redo-2"))
        self.btn_refresh.setToolTip("Geçmiş verilerini diskten yeniden oku")
        self.btn_refresh.clicked.connect(self.reload)
        header_layout.addWidget(self.btn_refresh)

        root_layout.addWidget(self.header_frame)

        # ── 2. Central Splitter (List | Details) ────────────────────────
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setChildrenCollapsible(False)

        # ── Left Pane: History Records List ──
        left_widget = QWidget(self.splitter)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        left_header_box = QHBoxLayout()
        left_header = QLabel("GEÇMİŞ KAYITLARI", left_widget)
        left_header.setObjectName("panelHeaderLabel")
        font_h = QFont()
        font_h.setBold(True)
        left_header.setFont(font_h)
        left_header_box.addWidget(left_header)
        left_header_box.addStretch()
        left_layout.addLayout(left_header_box)

        self.txt_filter_list = QLineEdit(left_widget)
        self.txt_filter_list.setPlaceholderText("Kayıtlarda ara (ID, tarih)...")
        self.txt_filter_list.textChanged.connect(self._filter_history_list)
        left_layout.addWidget(self.txt_filter_list)

        self.list_history = QListWidget(left_widget)
        self.list_history.setObjectName("historyList")
        self.list_history.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_history.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_history.customContextMenuRequested.connect(self._show_history_context_menu)
        self.list_history.itemSelectionChanged.connect(self._on_history_item_selected)
        left_layout.addWidget(self.list_history)

        self.splitter.addWidget(left_widget)

        # ── Right Pane: ChangeSet / Baseline Details ──
        right_widget = QWidget(self.splitter)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        right_header = QLabel("DEĞİŞİKLİK AYRINTILARI", right_widget)
        right_header.setObjectName("panelHeaderLabel")
        right_header.setFont(font_h)
        right_layout.addWidget(right_header)

        # Stacked Widget for Details (Placeholder vs Origin Baseline vs ChangeSet)
        self.stack_details = QStackedWidget(right_widget)

        # Page 0: Placeholder
        self.page_empty = QWidget()
        empty_layout = QVBoxLayout(self.page_empty)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_empty_msg = QLabel("Ayrıntılarını incelemek istediğiniz bir kaydı soldaki listeden seçin.", self.page_empty)
        self.lbl_empty_msg.setObjectName("emptyStateLabel")
        empty_layout.addWidget(self.lbl_empty_msg)
        self.stack_details.addWidget(self.page_empty)

        # Page 1: Origin Baseline View
        self.page_origin = QWidget()
        origin_layout = QVBoxLayout(self.page_origin)
        origin_layout.setContentsMargins(12, 12, 12, 12)
        origin_layout.setSpacing(12)

        self.origin_card = QFrame(self.page_origin)
        self.origin_card.setObjectName("originCard")
        self.origin_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        origin_card_layout = QVBoxLayout(self.origin_card)
        origin_card_layout.setContentsMargins(16, 16, 16, 16)
        origin_card_layout.setSpacing(10)

        origin_title = QLabel("BAŞLANGIÇ TEMEL KAYDI (ORIGIN BASELINE)", self.origin_card)
        origin_title.setObjectName("cardTitle")
        origin_title.setFont(font_h)
        origin_card_layout.addWidget(origin_title)

        self.origin_info_lbl = QLabel(self.origin_card)
        self.origin_info_lbl.setObjectName("cardContent")
        self.origin_info_lbl.setTextFormat(Qt.TextFormat.PlainText)
        origin_card_layout.addWidget(self.origin_info_lbl)

        self.origin_desc_lbl = QLabel(
            "Bu kayıt, belgenin XML Analyzer içinde ilk kez kaydedildiği andaki temiz başlangıç durumunu (Origin Baseline) "
            "temsil eder. Bu aşamada henüz bir önceki sürüme kıyasla üretilmiş ChangeSet bulunmamaktadır. Sonraki tüm değişiklikler "
            "bu temel üzerinden takip edilir.",
            self.origin_card
        )
        self.origin_desc_lbl.setWordWrap(True)
        self.origin_desc_lbl.setObjectName("cardDescription")
        origin_card_layout.addWidget(self.origin_desc_lbl)

        origin_layout.addWidget(self.origin_card)
        origin_layout.addStretch()
        self.stack_details.addWidget(self.page_origin)

        # Page 2: ChangeSet Details View
        self.page_changeset = QWidget()
        cs_layout = QVBoxLayout(self.page_changeset)
        cs_layout.setContentsMargins(0, 0, 0, 0)
        cs_layout.setSpacing(8)

        # Top summary card
        self.cs_summary_card = QFrame(self.page_changeset)
        self.cs_summary_card.setObjectName("summaryCard")
        self.cs_summary_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        cs_summary_layout = QVBoxLayout(self.cs_summary_card)
        cs_summary_layout.setContentsMargins(12, 10, 12, 10)
        cs_summary_layout.setSpacing(4)

        self.cs_summary_title = QLabel(self.cs_summary_card)
        self.cs_summary_title.setObjectName("csSummaryTitle")
        self.cs_summary_title.setFont(font_h)
        cs_summary_layout.addWidget(self.cs_summary_title)

        self.cs_summary_details = QLabel(self.cs_summary_card)
        self.cs_summary_details.setObjectName("csSummaryDetails")
        self.cs_summary_details.setTextFormat(Qt.TextFormat.PlainText)
        self.cs_summary_details.setWordWrap(True)
        cs_summary_layout.addWidget(self.cs_summary_details)
        cs_layout.addWidget(self.cs_summary_card)

        # Changes filter & counter
        cs_tools_layout = QHBoxLayout()
        self.txt_filter_changes = QLineEdit(self.page_changeset)
        self.txt_filter_changes.setPlaceholderText("Değişikliklerde ara (tag, attribute, xpath, değer)...")
        self.txt_filter_changes.textChanged.connect(self._filter_changes_table)
        cs_tools_layout.addWidget(self.txt_filter_changes)

        self.lbl_changes_count = QLabel("0 değişiklik", self.page_changeset)
        cs_tools_layout.addWidget(self.lbl_changes_count)
        cs_layout.addLayout(cs_tools_layout)

        # Table of Changes
        self.tbl_changes = QTableWidget(self.page_changeset)
        self.tbl_changes.setObjectName("changesTable")
        self.tbl_changes.setColumnCount(7)
        self.tbl_changes.setHorizontalHeaderLabels([
            "#", "Değişiklik Türü", "Düğüm", "Tag", "Attribute", "XML Konumu (XPath)", "Özet"
        ])
        self.tbl_changes.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.tbl_changes.horizontalHeader().setStretchLastSection(True)
        self.tbl_changes.verticalHeader().setVisible(False)
        self.tbl_changes.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl_changes.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tbl_changes.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl_changes.itemSelectionChanged.connect(self._on_change_row_selected)
        self.tbl_changes.setColumnWidth(0, 40)
        self.tbl_changes.setColumnWidth(1, 140)
        self.tbl_changes.setColumnWidth(2, 100)
        self.tbl_changes.setColumnWidth(3, 160)
        self.tbl_changes.setColumnWidth(4, 100)
        self.tbl_changes.setColumnWidth(5, 230)
        cs_layout.addWidget(self.tbl_changes, stretch=2)

        # Value Comparison Sub-Pane (Old vs New Value Inspector)
        self.diff_inspector_card = QFrame(self.page_changeset)
        self.diff_inspector_card.setObjectName("inspectorCard")
        self.diff_inspector_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        inspector_layout = QVBoxLayout(self.diff_inspector_card)
        inspector_layout.setContentsMargins(10, 8, 10, 8)
        inspector_layout.setSpacing(6)

        self._current_xpath = ""

        # XPath Header with Copy Button
        xpath_header_layout = QHBoxLayout()
        xpath_header_layout.setContentsMargins(0, 0, 0, 0)
        xpath_header_layout.setSpacing(8)

        self.lbl_inspector_xpath = QLabel("Seçili Değişiklik Değerleri", self.diff_inspector_card)
        self.lbl_inspector_xpath.setObjectName("inspectorXPath")
        self.lbl_inspector_xpath.setFont(font_h)
        self.lbl_inspector_xpath.setWordWrap(True)
        self.lbl_inspector_xpath.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        xpath_header_layout.addWidget(self.lbl_inspector_xpath, stretch=1)

        self.btn_copy_xpath = QPushButton("XPath Kopyala", self.diff_inspector_card)
        self.btn_copy_xpath.setObjectName("btnCopyXPath")
        self.btn_copy_xpath.setIcon(IconProvider.get_icon("copy"))
        self.btn_copy_xpath.setToolTip("XML Konumunu (XPath) Panoya Kopyala")
        self.btn_copy_xpath.setFixedHeight(24)
        self.btn_copy_xpath.setEnabled(False)
        self.btn_copy_xpath.clicked.connect(self._copy_current_xpath)
        xpath_header_layout.addWidget(self.btn_copy_xpath)

        inspector_layout.addLayout(xpath_header_layout)

        val_cols_layout = QHBoxLayout()
        val_cols_layout.setSpacing(10)

        # Old Value Box
        old_val_box = QVBoxLayout()
        old_val_box.setSpacing(2)
        old_lbl = QLabel("Eski Değer (Before):", self.diff_inspector_card)
        old_lbl.setObjectName("valColHeader")
        old_val_box.addWidget(old_lbl)
        self.txt_old_val = QTextEdit(self.diff_inspector_card)
        self.txt_old_val.setReadOnly(True)
        self.txt_old_val.setObjectName("valBoxOld")
        old_val_box.addWidget(self.txt_old_val)
        val_cols_layout.addLayout(old_val_box)

        # New Value Box
        new_val_box = QVBoxLayout()
        new_val_box.setSpacing(2)
        new_lbl = QLabel("Yeni Değer (After):", self.diff_inspector_card)
        new_lbl.setObjectName("valColHeader")
        new_val_box.addWidget(new_lbl)
        self.txt_new_val = QTextEdit(self.diff_inspector_card)
        self.txt_new_val.setReadOnly(True)
        self.txt_new_val.setObjectName("valBoxNew")
        new_val_box.addWidget(self.txt_new_val)
        val_cols_layout.addLayout(new_val_box)

        inspector_layout.addLayout(val_cols_layout)
        cs_layout.addWidget(self.diff_inspector_card, stretch=1)

        self.stack_details.addWidget(self.page_changeset)

        right_layout.addWidget(self.stack_details)
        self.splitter.addWidget(right_widget)

        # Set splitter balance: left list ~340px, right details ~800px
        self.splitter.setSizes([340, 800])
        root_layout.addWidget(self.splitter, stretch=1)

        # ── 3. Bottom Action Bar ───────────────────────────────────────
        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(0, 4, 0, 0)
        bottom_layout.setSpacing(10)

        self.lbl_stats = QLabel("İstatistikler yükleniyor...", self)
        self.lbl_stats.setObjectName("statsLabel")
        bottom_layout.addWidget(self.lbl_stats)

        bottom_layout.addStretch()

        # Manage / Delete History Menu Button
        self.btn_delete_history = QPushButton("Geçmişi Yönet", self)
        self.btn_delete_history.setIcon(IconProvider.get_icon("trash-2"))
        self.menu_delete_history = QMenu(self)
        self.act_del_selected = self.menu_delete_history.addAction("Seçili Kaydı Sil (Del)")
        self.act_del_selected.setIcon(IconProvider.get_icon("trash-2"))
        self.act_del_selected.triggered.connect(self._delete_selected_history_item)
        self.act_clear_all = self.menu_delete_history.addAction("Tüm Geçmişi Temizle...")
        self.act_clear_all.setIcon(IconProvider.get_icon("alert-triangle"))
        self.act_clear_all.triggered.connect(self._clear_all_history)
        self.btn_delete_history.setMenu(self.menu_delete_history)
        bottom_layout.addWidget(self.btn_delete_history)

        # TXT Export Menu Button
        self.btn_export_txt = QPushButton("Dışa Aktar (TXT)", self)
        self.btn_export_txt.setIcon(IconProvider.get_icon("file-text"))
        self.menu_export_txt = QMenu(self)
        act_txt_all = self.menu_export_txt.addAction("Tüm Geçmişi Aktar (.txt)")
        act_txt_all.triggered.connect(lambda: self._export_txt(export_selected=False))
        self.act_txt_sel = self.menu_export_txt.addAction("Seçili Kaydı Aktar (.txt)")
        self.act_txt_sel.triggered.connect(lambda: self._export_txt(export_selected=True))
        self.btn_export_txt.setMenu(self.menu_export_txt)
        bottom_layout.addWidget(self.btn_export_txt)

        # JSONL Export Menu Button
        self.btn_export_jsonl = QPushButton("Dışa Aktar (JSONL)", self)
        self.btn_export_jsonl.setIcon(IconProvider.get_icon("file-code"))
        self.menu_export_jsonl = QMenu(self)
        act_jsonl_all = self.menu_export_jsonl.addAction("Tüm Geçmişi Aktar (.jsonl)")
        act_jsonl_all.triggered.connect(lambda: self._export_jsonl(export_selected=False))
        self.act_jsonl_sel = self.menu_export_jsonl.addAction("Seçili ChangeSet'i Aktar (.jsonl)")
        self.act_jsonl_sel.triggered.connect(lambda: self._export_jsonl(export_selected=True))
        self.btn_export_jsonl.setMenu(self.menu_export_jsonl)
        bottom_layout.addWidget(self.btn_export_jsonl)

        # Close button
        self.btn_close = QPushButton("Kapat", self)
        self.btn_close.clicked.connect(self.accept)
        bottom_layout.addWidget(self.btn_close)

        root_layout.addLayout(bottom_layout)

    # ==================================================================
    #  DATA LOADING & POPULATION
    # ==================================================================

    def refresh_document(self, doc_session: XmlDocumentSession):
        """Switches the active document session and reloads history view."""
        self.doc_session = doc_session
        self.reload()

    def reload(self):
        """Reads read-only audit data from doc_session and updates view."""
        if not self.doc_session or not self.doc_session.file_path:
            self._show_no_document_state()
            return

        file_name = Path(self.doc_session.file_path).name
        full_path = str(self.doc_session.file_path)
        self.subtitle_lbl.setText(f"Belge: {file_name} ({full_path})")
        self.subtitle_lbl.setToolTip(full_path)

        try:
            audit = self.doc_session.get_history_audit()
        except Exception as e:
            logger.warning("Error fetching history audit: %s", e)
            audit = None

        if not audit:
            self._show_empty_history_state(file_name)
            return

        self._audit_data = audit
        self._current_records = audit.get("records", [])

        # Update status badge
        self._update_status_badge()

        # Populate left list
        self._populate_history_list()

        # Update stats
        hist_size = audit.get("history_file_size", 0)
        size_kb = hist_size / 1024.0
        meta = audit.get("metadata")
        epoch_str = f"Epoch: {meta.active_epoch}" if meta else "Epoch: 1"
        self.lbl_stats.setText(f"Aktif Kütük: {size_kb:.1f} KB | Toplam Kayıt: {len(self._current_records)} | {epoch_str}")

    def _show_no_document_state(self):
        self.subtitle_lbl.setText("Açık ve kaydedilmiş bir XML belgesi bulunamadı.")
        self.status_badge.setText("Belge Yok")
        self.status_badge.setStyleSheet("background-color: #e5e7eb; color: #374151; border: 1px solid #d1d5db; border-radius: 4px; padding: 4px 8px;")
        self.list_history.clear()
        self.stack_details.setCurrentWidget(self.page_empty)
        self.lbl_empty_msg.setText("Geçmiş kayıtlarını görüntülemek için önce bir XML dosyası açmalısınız.")
        self.lbl_stats.setText("Belge açık değil.")

    def _show_empty_history_state(self, filename: str):
        self.status_badge.setText("Kayıt Yok")
        self.status_badge.setStyleSheet("background-color: #e5e7eb; color: #374151; border: 1px solid #d1d5db; border-radius: 4px; padding: 4px 8px;")
        self.list_history.clear()
        self.stack_details.setCurrentWidget(self.page_empty)
        self.lbl_empty_msg.setText(f"'{filename}' belgesine ait henüz geçmiş kaydı bulunmamaktadır.")
        self.lbl_stats.setText("0 kayıt.")

    def _populate_history_list(self):
        self.list_history.clear()
        meta: Optional[DocumentMetadata] = self._audit_data.get("metadata") if self._audit_data else None

        # 1. Origin Baseline Item (if metadata exists)
        if meta and meta.created_at:
            created_str = format_local_timestamp(meta.created_at)
            item = QListWidgetItem()
            item.setText(f"[Başlangıç] Origin Baseline\n{created_str} • İlk Temiz Kayıt")
            item.setIcon(IconProvider.get_icon("file-text"))
            item.setData(Qt.ItemDataRole.UserRole, {"type": "origin", "metadata": meta})
            self.list_history.addItem(item)

        # 2. ChangeSet Items (chronological order)
        for idx, cs in enumerate(self._current_records, start=1):
            ts = format_local_timestamp(cs.timestamp)
            changes_cnt = len(cs.changes)
            summary = cs.summary or f"{changes_cnt} değişiklik"
            item = QListWidgetItem()
            item.setText(f"[{idx}] Save #{cs.save_id}\n{ts} • {changes_cnt} değişiklik")
            item.setIcon(IconProvider.get_icon("save"))
            item.setData(Qt.ItemDataRole.UserRole, {
                "type": "changeset",
                "changeset": cs,
                "index": idx,
                "text_search": f"{cs.save_id} {ts} {summary}".lower(),
            })
            self.list_history.addItem(item)

        # 3. Corrupted Lines Item (if any)
        corrupted = self._audit_data.get("corrupted_lines", 0) if self._audit_data else 0
        if corrupted > 0:
            item = QListWidgetItem()
            item.setText(f"⚠️ {corrupted} Bozuk Satır Atlandı\nKütükte okunamayan satır tespit edildi.")
            item.setIcon(IconProvider.get_icon("alert-triangle"))
            item.setData(Qt.ItemDataRole.UserRole, {"type": "corrupted", "count": corrupted})
            self.list_history.addItem(item)

        # Select first item if available
        if self.list_history.count() > 0:
            self.list_history.setCurrentRow(0)

    # ==================================================================
    #  SELECTION & DETAILS PRESENTATION
    # ==================================================================

    def _on_history_item_selected(self):
        items = self.list_history.selectedItems()
        if not items:
            self.stack_details.setCurrentWidget(self.page_empty)
            self._selected_item_data = None
            self.act_txt_sel.setEnabled(False)
            self.act_jsonl_sel.setEnabled(False)
            self.act_del_selected.setEnabled(False)
            return

        data = items[0].data(Qt.ItemDataRole.UserRole)
        self._selected_item_data = data
        item_type = data.get("type") if isinstance(data, dict) else None

        if item_type == "origin":
            self._display_origin_details(data.get("metadata"))
            self.act_txt_sel.setEnabled(True)
            self.act_jsonl_sel.setEnabled(False)  # Origin baseline is metadata, not a ChangeSet
            self.act_del_selected.setEnabled(False)
        elif item_type == "changeset":
            self._display_changeset_details(data.get("changeset"), data.get("index", 1))
            self.act_txt_sel.setEnabled(True)
            self.act_jsonl_sel.setEnabled(True)
            self.act_del_selected.setEnabled(True)
        elif item_type == "corrupted":
            self._display_corrupted_details(data.get("count", 0))
            self.act_txt_sel.setEnabled(False)
            self.act_jsonl_sel.setEnabled(False)
            self.act_del_selected.setEnabled(False)
        else:
            self.stack_details.setCurrentWidget(self.page_empty)
            self.act_txt_sel.setEnabled(False)
            self.act_jsonl_sel.setEnabled(False)
            self.act_del_selected.setEnabled(False)

    def _display_origin_details(self, meta: Optional[DocumentMetadata]):
        self.stack_details.setCurrentWidget(self.page_origin)
        if not meta:
            self.origin_info_lbl.setText("Metadata bilgisi okunamadı.")
            return

        lines = [
            f"Belge Kimliği (Doc ID) : {meta.doc_id}",
            f"Orijinal Dosya Adı     : {meta.original_filename}",
            f"Son Bilinen Dosya Yolu : {meta.last_known_path}",
            f"Oluşturulma Zamanı     : {format_local_timestamp(meta.created_at)}",
            f"Son Kayıt Zamanı       : {format_local_timestamp(meta.last_saved_at)}",
            f"Başlangıç SHA-256      : {meta.last_saved_content_sha256}",
            f"Aktif Epoch / Dal      : {meta.active_epoch}",
            f"Arşivlenmiş Epoch Sayısı: {len(meta.archived_epochs)}",
        ]
        self.origin_info_lbl.setText("\n".join(lines))

    def _display_changeset_details(self, cs: Optional[ChangeSet], index: int):
        self.stack_details.setCurrentWidget(self.page_changeset)
        if not cs:
            return

        self.cs_summary_title.setText(f"ChangeSet #{index} — Kayıt ID: {cs.save_id}")
        ts = format_local_timestamp(cs.timestamp)
        prev_sha = (cs.previous_content_sha256[:12] + "…") if cs.previous_content_sha256 else "(ilk)"
        exp_sha = (cs.expected_target_sha256[:12] + "…") if cs.expected_target_sha256 else "(bilinmiyor)"
        epoch_str = f" | Epoch: {cs.target_epoch_id}" if cs.target_epoch_id else ""

        details_text = (
            f"Tarih/Saat: {ts} | Değişiklik Sayısı: {len(cs.changes)}{epoch_str}\n"
            f"SHA-256 Geçişi: {prev_sha} ➔ {exp_sha}\n"
            f"Hedef Dosya: {cs.target_path}"
        )
        if cs.summary:
            details_text += f"\nAçıklama: {cs.summary}"
        self.cs_summary_details.setText(details_text)

        # Clear inspector
        self.lbl_inspector_xpath.setText("Aşağıdaki tablodan bir satır seçiniz")
        self.lbl_inspector_xpath.setToolTip("")
        self._current_xpath = ""
        self.btn_copy_xpath.setEnabled(False)
        self.txt_old_val.clear()
        self.txt_new_val.clear()

        # Populate table
        self._populate_changes_table(cs.changes)

    def _populate_changes_table(self, changes: List[ChangeRecord]):
        self.tbl_changes.setRowCount(0)
        filter_text = self.txt_filter_changes.text().strip().lower()

        visible_count = 0
        for i, rec in enumerate(changes, start=1):
            c_type = rec.change_type.value if hasattr(rec.change_type, "value") else str(rec.change_type)
            type_cfg = _get_type_config(c_type)

            tag_str = rec.tag or rec.new_tag or rec.old_tag or ""
            attr_str = rec.attr_name or ""
            xpath_str = rec.xpath or ""
            old_str = rec.old_value or ""
            new_str = rec.new_value or ""
            node_str = rec.node_type.value if hasattr(rec.node_type, "value") else str(rec.node_type)
            node_display = _format_node_type(rec.node_type, rec.change_type)

            # Row search filtering
            search_blob = f"{c_type} {type_cfg['label']} {node_display} {node_str} {tag_str} {attr_str} {xpath_str} {old_str} {new_str}".lower()
            if filter_text and filter_text not in search_blob:
                continue

            row_idx = self.tbl_changes.rowCount()
            self.tbl_changes.insertRow(row_idx)

            # 0: Index
            item_idx = QTableWidgetItem(str(i))
            item_idx.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_idx.setToolTip(str(i))
            self.tbl_changes.setItem(row_idx, 0, item_idx)

            # 1: Change Type (with badge styling)
            item_type = QTableWidgetItem(type_cfg["label"])
            item_type.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_type.setToolTip(f"{type_cfg['label']} ({c_type})")
            is_dark = self.theme_manager.is_dark()
            bg_color = QColor(type_cfg["bg_dark"] if is_dark else type_cfg["bg_light"])
            fg_color = QColor(type_cfg["fg_dark"] if is_dark else type_cfg["fg_light"])
            item_type.setBackground(bg_color)
            item_type.setForeground(fg_color)
            self.tbl_changes.setItem(row_idx, 1, item_type)

            # 2: Node Type
            item_node = QTableWidgetItem(node_display)
            item_node.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_node.setToolTip(f"Düğüm Türü: {node_display} (DOM: {node_str})")
            self.tbl_changes.setItem(row_idx, 2, item_node)

            # 3: Tag
            item_tag = QTableWidgetItem(tag_str)
            item_tag.setToolTip(tag_str)
            self.tbl_changes.setItem(row_idx, 3, item_tag)

            # 4: Attribute
            item_attr = QTableWidgetItem(attr_str)
            item_attr.setToolTip(attr_str)
            self.tbl_changes.setItem(row_idx, 4, item_attr)

            # 5: XPath
            item_xpath = QTableWidgetItem(xpath_str)
            item_xpath.setToolTip(xpath_str)
            self.tbl_changes.setItem(row_idx, 5, item_xpath)

            # 6: Summary
            if c_type == "VALUE_UPDATE":
                summary = f"\"{old_str}\" ➔ \"{new_str}\""
            elif c_type == "ATTR_UPDATE":
                summary = f"@{attr_str}: \"{old_str}\" ➔ \"{new_str}\""
            elif c_type in ("ATTR_INSERT", "ELEMENT_INSERT"):
                summary = f"Eklendi: {new_str or tag_str}"
            elif c_type in ("ATTR_DELETE", "ELEMENT_DELETE"):
                summary = f"Silindi: {old_str or tag_str}"
            elif c_type == "ORDER_CHANGED":
                summary = f"Konum: {rec.old_position} ➔ {rec.new_position}"
            else:
                summary = c_type

            item_summary = QTableWidgetItem(summary[:120] + ("…" if len(summary) > 120 else ""))
            item_summary.setToolTip(summary)
            # Attach full record to item data
            item_summary.setData(Qt.ItemDataRole.UserRole, rec)
            self.tbl_changes.setItem(row_idx, 6, item_summary)

            visible_count += 1

        self.lbl_changes_count.setText(f"{visible_count} / {len(changes)} değişiklik gösteriliyor")
        if self.tbl_changes.rowCount() > 0:
            self.tbl_changes.selectRow(0)

    def _display_corrupted_details(self, count: int):
        self.stack_details.setCurrentWidget(self.page_origin)
        text = (
            f"DİKKAT: History kütüğünde {count} adet geçersiz / bozuk JSON satırı atlandı.\n\n"
            "Bu durum beklenmeyen bir kesinti (elektrik kesintisi veya yarım yazma) "
            "nedeniyle oluşmuş olabilir.\n\n"
            "History Viewer salt okunur çalıştığı için dosya üzerinde herhangi bir değişiklik "
            "yapılmamıştır. Mevcut XML içeriğiniz güvendedir."
        )
        self.origin_info_lbl.setText(text)

    def _on_change_row_selected(self):
        selected_rows = self.tbl_changes.selectionModel().selectedRows()
        if not selected_rows:
            return

        row = selected_rows[0].row()
        item_summary = self.tbl_changes.item(row, 6)
        if not item_summary:
            return

        rec: Optional[ChangeRecord] = item_summary.data(Qt.ItemDataRole.UserRole)
        if not rec:
            return

        c_type = rec.change_type.value if hasattr(rec.change_type, "value") else str(rec.change_type)
        type_cfg = _get_type_config(c_type)

        xpath_display = rec.xpath or "(belirtilmemiş)"
        self._current_xpath = rec.xpath or ""
        self.lbl_inspector_xpath.setText(f"[{type_cfg['label']}] Konum: {xpath_display}")
        self.lbl_inspector_xpath.setToolTip(xpath_display)
        self.btn_copy_xpath.setEnabled(bool(rec.xpath))

        self.txt_old_val.setPlainText(rec.old_value if rec.old_value is not None else "(yok)")
        self.txt_new_val.setPlainText(rec.new_value if rec.new_value is not None else "(yok)")

    def _copy_current_xpath(self):
        """Copies the currently displayed XPath to the system clipboard."""
        if not hasattr(self, "_current_xpath") or not self._current_xpath:
            return
        app = QApplication.instance()
        if app and app.clipboard():
            app.clipboard().setText(self._current_xpath)
            orig_text = self.btn_copy_xpath.text()
            self.btn_copy_xpath.setText("Kopyalandı!")
            QTimer.singleShot(1500, lambda: self.btn_copy_xpath.setText(orig_text))

    def _filter_history_list(self, query: str):
        query = query.strip().lower()
        for idx in range(self.list_history.count()):
            item = self.list_history.item(idx)
            data = item.data(Qt.ItemDataRole.UserRole) or {}
            search_str = data.get("text_search", "")
            if not query or query in item.text().lower() or query in search_str:
                item.setHidden(False)
            else:
                item.setHidden(True)

    def _filter_changes_table(self):
        if not self._selected_item_data:
            return
        cs = self._selected_item_data.get("changeset")
        if cs:
            self._populate_changes_table(cs.changes)

    # ==================================================================
    #  EXPORT FUNCTIONALITY (TXT & JSONL)
    # ==================================================================

    def _export_txt(self, export_selected: bool = False):
        """Exports history records to a human-readable UTF-8 plain text file."""
        if not self._audit_data:
            QMessageBox.warning(self, "Dışa Aktarma Hatası", "Dışa aktarılacak geçmiş verisi bulunamadı.")
            return

        records = self._current_records
        meta: Optional[DocumentMetadata] = self._audit_data.get("metadata")
        file_name = Path(self.doc_session.file_path).name if self.doc_session and self.doc_session.file_path else "document.xml"

        if export_selected:
            if not self._selected_item_data:
                QMessageBox.warning(self, "Seçim Yok", "Lütfen dışa aktarmak için bir kayıt seçin.")
                return
            export_target_name = f"{file_name}_history_selected.txt"
        else:
            export_target_name = f"{file_name}_history_audit.txt"

        dest_path, _ = QFileDialog.getSaveFileName(
            self,
            "Geçmişi TXT Olarak Kaydet",
            export_target_name,
            "Text Files (*.txt);;All Files (*)"
        )
        if not dest_path:
            return

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        lines: List[str] = [
            "=" * 80,
            "XML ANALYZER — GEÇMİŞ DENETİM RAPORU (AUDIT TRAIL)",
            "=" * 80,
            f"Belge Adı        : {file_name}",
            f"Tam Dosya Yolu   : {self.doc_session.file_path}",
            f"Rapor Tarihi     : {now_str}",
            f"Toplam ChangeSet : {len(records)}",
        ]

        if meta:
            lines.extend([
                "-" * 80,
                "[BAŞLANGIÇ TEMEL KAYDI (ORIGIN BASELINE)]",
                f"Oluşturulma Tarihi : {format_local_timestamp(meta.created_at)}",
                f"Son Kayıt Tarihi   : {format_local_timestamp(meta.last_saved_at)}",
                f"Başlangıç SHA-256  : {meta.last_saved_content_sha256}",
                f"Aktif Epoch / Dal  : {meta.active_epoch}",
            ])

        lines.extend(["-" * 80, "DEĞİŞİKLİK SETLERİ VE AYRINTILARI", "-" * 80])

        target_records = records
        if export_selected:
            item_type = self._selected_item_data.get("type")
            if item_type == "changeset":
                target_records = [self._selected_item_data["changeset"]]
            elif item_type == "origin":
                lines.append("Seçili kayıt: Origin Baseline (ayrıntıları yukarıda belirtilmiştir).")
                target_records = []

        for idx, cs in enumerate(target_records, start=1):
            lines.append("")
            lines.append(f"[{idx}] Save #{cs.save_id}")
            lines.append(f"  Tarih           : {format_local_timestamp(cs.timestamp)}")
            lines.append(f"  Hedef Yol       : {cs.target_path}")
            lines.append(f"  Önceki SHA-256  : {cs.previous_content_sha256}")
            lines.append(f"  Hedef SHA-256   : {cs.expected_target_sha256}")
            if cs.summary:
                lines.append(f"  Açıklama        : {cs.summary}")
            lines.append(f"  Değişiklik Sayısı: {len(cs.changes)}")
            lines.append("  Değişiklik Maddeleri:")

            for c_idx, rec in enumerate(cs.changes, start=1):
                c_type = rec.change_type.value if hasattr(rec.change_type, "value") else str(rec.change_type)
                type_cfg = _get_type_config(c_type)
                lines.append(f"    {c_idx}. [{type_cfg['label']}] ({c_type}) XPath: {rec.xpath}")
                if rec.tag:
                    lines.append(f"       Tag       : <{rec.tag}>")
                if rec.attr_name:
                    lines.append(f"       Attribute : @{rec.attr_name}")
                if rec.old_value is not None:
                    lines.append(f"       Eski Değer: {rec.old_value}")
                if rec.new_value is not None:
                    lines.append(f"       Yeni Değer: {rec.new_value}")
                if rec.old_position is not None or rec.new_position is not None:
                    lines.append(f"       Konum     : {rec.old_position} ➔ {rec.new_position}")

        lines.append("")
        lines.append("=" * 80)
        lines.append("RAPOR SONU — XML ANALYZER")
        lines.append("=" * 80)

        try:
            with open(dest_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            QMessageBox.information(self, "Dışa Aktarma Başarılı", f"Geçmiş raporu başarıyla kaydedildi:\n{dest_path}")
        except Exception as e:
            QMessageBox.critical(self, "Dışa Aktarma Hatası", f"Dosya yazılırken hata oluştu:\n{e}")

    def _export_jsonl(self, export_selected: bool = False):
        """Exports valid history records directly from source history.jsonl to preserve original raw content."""
        if not self.doc_session or not self.doc_session.file_path:
            QMessageBox.warning(self, "Dışa Aktarma Hatası", "Açık bir belge bulunamadı.")
            return

        file_name = Path(self.doc_session.file_path).name

        # 1. Check selection if export_selected
        target_save_id: Optional[str] = None
        target_doc_id: Optional[str] = None
        if export_selected:
            if not self._selected_item_data:
                QMessageBox.warning(self, "Seçim Hatası", "Lütfen JSONL olarak aktarmak için bir kayıt seçin.")
                return

            item_type = self._selected_item_data.get("type")
            if item_type == "origin":
                QMessageBox.information(
                    self,
                    "Origin Baseline Kaydı",
                    "Seçili kayıt belgenin başlangıç durumunu (Origin Baseline) temsil etmektedir.\n\n"
                    "Başlangıç durumunda henüz bir değişiklik yapılmadığı için ayrı bir JSONL değişiklik kaydı bulunmamaktadır."
                )
                return

            if item_type != "changeset":
                QMessageBox.warning(self, "Seçim Hatası", "Lütfen JSONL olarak aktarmak için geçerli bir ChangeSet seçin.")
                return

            cs: Optional[ChangeSet] = self._selected_item_data.get("changeset")
            if not cs or not getattr(cs, "save_id", None):
                QMessageBox.warning(self, "Seçim Hatası", "Seçili ChangeSet kaydına ulaşılamadı.")
                return

            target_save_id = cs.save_id
            target_doc_id = cs.doc_id
            default_name = f"{file_name}_changeset_{target_save_id}.jsonl"
        else:
            default_name = f"{file_name}_history.jsonl"

        # 2. Check source history file
        doc_id = get_document_id(str(self.doc_session.file_path))
        doc_dir = self.doc_session.history_manager.get_document_directory(doc_id)
        source_history_path = os.path.join(doc_dir, "history.jsonl")

        if not os.path.exists(source_history_path) or os.path.getsize(source_history_path) == 0:
            QMessageBox.warning(self, "Dışa Aktarma Hatası", "Belgeye ait JSONL geçmiş kütüğü bulunamadı veya dosya boş.")
            return

        # 3. Read raw lines in binary mode, validate each line, filter if needed
        lines_to_write: List[bytes] = []
        try:
            with open(source_history_path, "rb") as f:
                for line_bytes in f:
                    line_trimmed = line_bytes.strip()
                    if not line_trimmed:
                        continue

                    # Strict UTF-8 validation
                    try:
                        line_str = line_trimmed.decode("utf-8")
                    except UnicodeDecodeError:
                        continue

                    # Strict JSON validation
                    try:
                        data = json.loads(line_str)
                    except Exception:
                        continue

                    if not isinstance(data, dict):
                        continue

                    if export_selected:
                        # Match target ChangeSet by save_id (and doc_id if present)
                        rec_save_id = data.get("save_id")
                        if rec_save_id and rec_save_id == target_save_id:
                            if target_doc_id and data.get("doc_id") and data.get("doc_id") != target_doc_id:
                                continue
                            lines_to_write.append(line_trimmed + b"\n")
                            break
                    else:
                        # Full export: preserve all valid raw JSONL lines (including BRANCH_INITIALIZED and future keys)
                        lines_to_write.append(line_trimmed + b"\n")

        except Exception as e:
            QMessageBox.critical(self, "Okuma Hatası", f"Kaynak geçmiş kütüğü okunurken hata oluştu:\n{e}")
            return

        if not lines_to_write:
            if export_selected:
                QMessageBox.warning(
                    self,
                    "Kayıt Bulunamadı",
                    f"Seçili ChangeSet (ID: {target_save_id}) kaynak geçmiş kütüğünde bulunamadı veya satır bozuk."
                )
            else:
                QMessageBox.warning(
                    self,
                    "Kayıt Bulunamadı",
                    "Geçmiş kütüğünde dışa aktarılabilecek geçerli bir JSONL kaydı bulunamadı."
                )
            return

        # 4. Prompt user for destination file path
        dest_path, _ = QFileDialog.getSaveFileName(
            self,
            "Geçmişi JSONL Olarak Kaydet",
            default_name,
            "JSON Lines (*.jsonl);;All Files (*)"
        )
        if not dest_path:
            return

        # 5. Write raw bytes to destination file atomically/safely
        try:
            with open(dest_path, "wb") as f:
                for line in lines_to_write:
                    f.write(line)
            QMessageBox.information(
                self,
                "Dışa Aktarma Başarılı",
                f"{len(lines_to_write)} adet JSONL kaydı orijinal haliyle başarıyla kaydedildi:\n{dest_path}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Dışa Aktarma Hatası", f"JSONL dosyası yazılırken hata oluştu:\n{e}")

    # ==================================================================
    #  THEME STYLING & CONTEXT ACTIONS
    # ==================================================================

    def _apply_theme(self):
        """Applies semantic color styling according to the active theme."""
        is_dark = self.theme_manager.is_dark()
        pal = PALETTE["dark"] if is_dark else PALETTE["light"]

        dialog_bg = "#1e1e24" if is_dark else "#f4f6f8"
        card_bg = "#21222c" if is_dark else "#ffffff"
        card_border = "#383a59" if is_dark else "#e2e8f0"
        header_bg = "#282a36" if is_dark else "#ffffff"
        table_hdr_bg = "#282a36" if is_dark else "#f1f5f9"
        input_bg = "#282a36" if is_dark else "#ffffff"
        val_box_bg = "#191a21" if is_dark else "#fafafa"

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {dialog_bg};
                color: {pal["text_primary"]};
            }}
            QFrame#headerFrame {{
                background-color: {header_bg};
                border: 1px solid {card_border};
                border-radius: 6px;
            }}
            QLabel#viewerTitle {{
                color: {pal["text_primary"]};
                background: transparent;
            }}
            QLabel#viewerSubtitle {{
                color: {pal["text_muted"]};
                font-size: 11px;
                background: transparent;
            }}
            QLabel#panelHeaderLabel {{
                color: {pal["text_secondary"]};
                font-size: 11px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
                background: transparent;
            }}
            QListWidget#historyList {{
                background-color: {card_bg};
                border: 1px solid {pal["border_default"]};
                border-radius: 4px;
                color: {pal["text_primary"]};
                padding: 4px;
            }}
            QListWidget#historyList::item {{
                padding: 8px;
                border-bottom: 1px solid {pal["border_muted"]};
                border-radius: 4px;
            }}
            QListWidget#historyList::item:hover {{
                background-color: {pal["menu_item_hover"]};
            }}
            QListWidget#historyList::item:selected {{
                background-color: {pal["selection_bg"]};
                color: {pal["selection_fg"]};
                font-weight: bold;
            }}
            QFrame#originCard, QFrame#summaryCard, QFrame#inspectorCard {{
                background-color: {card_bg};
                border: 1px solid {card_border};
                border-radius: 6px;
            }}
            QLabel#cardTitle, QLabel#csSummaryTitle {{
                color: {pal["text_primary"]};
                background: transparent;
            }}
            QLabel#cardContent, QLabel#csSummaryDetails {{
                color: {pal["text_secondary"]};
                font-family: Consolas, "Courier New", monospace;
                font-size: 11px;
                background: transparent;
            }}
            QLabel#cardDescription {{
                color: {pal["text_muted"]};
                font-size: 11px;
                line-height: 1.4;
                background: transparent;
            }}
            QLabel#inspectorXPath {{
                color: {pal["text_primary"]};
                font-family: Consolas, "Courier New", monospace;
                font-size: 11px;
                background: transparent;
            }}
            QPushButton#btnCopyXPath {{
                background-color: {card_bg};
                border: 1px solid {pal["border_default"]};
                border-radius: 3px;
                color: {pal["text_primary"]};
                padding: 2px 8px;
                font-size: 11px;
            }}
            QPushButton#btnCopyXPath:hover {{
                border-color: {pal["accent"]};
                background-color: {pal["menu_item_hover"]};
            }}
            QTableWidget#changesTable {{
                background-color: {card_bg};
                border: 1px solid {pal["border_default"]};
                border-radius: 4px;
                color: {pal["text_primary"]};
                gridline-color: {pal["grid_border"]};
            }}
            QTableWidget#changesTable QHeaderView::section {{
                background-color: {table_hdr_bg};
                color: {pal["text_secondary"]};
                font-weight: bold;
                border: 1px solid {pal["border_muted"]};
                padding: 4px;
            }}
            QTableWidget#changesTable::item:selected {{
                background-color: {pal["selection_bg"]};
                color: {pal["selection_fg"]};
            }}
            QLineEdit {{
                background-color: {input_bg};
                border: 1px solid {pal["border_default"]};
                border-radius: 4px;
                color: {pal["text_primary"]};
                padding: 5px 8px;
            }}
            QLineEdit:focus {{
                border: 1px solid {pal["accent"]};
            }}
            QTextEdit#valBoxOld, QTextEdit#valBoxNew {{
                background-color: {val_box_bg};
                border: 1px solid {pal["border_default"]};
                border-radius: 4px;
                color: {pal["text_primary"]};
                font-family: Consolas, "Courier New", monospace;
                font-size: 11px;
            }}
            QLabel#valColHeader {{
                color: {pal["text_secondary"]};
                font-weight: bold;
                font-size: 11px;
                background: transparent;
            }}
            QPushButton {{
                background-color: {card_bg};
                border: 1px solid {pal["border_default"]};
                border-radius: 4px;
                color: {pal["text_primary"]};
                padding: 6px 12px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {pal["menu_item_hover"]};
                border-color: {pal["accent"]};
            }}
            QPushButton:pressed {{
                background-color: {pal["selection_bg"]};
            }}
            QLabel#statsLabel {{
                color: {pal["text_muted"]};
                font-size: 11px;
                background: transparent;
            }}
            QLabel#emptyStateLabel {{
                color: {pal["text_muted"]};
                font-size: 13px;
                background: transparent;
            }}
            QMenu {{
                background-color: {pal["menu_bg"]};
                color: {pal["text_primary"]};
                border: 1px solid {pal["border_default"]};
            }}
            QMenu::item:selected {{
                background-color: {pal["menu_item_hover"]};
            }}
        """)

        # Re-apply tinted icons
        if hasattr(self, "icon_lbl"):
            self.icon_lbl.setPixmap(IconProvider.get_pixmap("history", size=24, widget=self))
        if hasattr(self, "btn_refresh"):
            self.btn_refresh.setIcon(IconProvider.get_icon("redo-2"))
        if hasattr(self, "btn_delete_history"):
            self.btn_delete_history.setIcon(IconProvider.get_icon("trash-2"))
        if hasattr(self, "act_del_selected"):
            self.act_del_selected.setIcon(IconProvider.get_icon("trash-2"))
        if hasattr(self, "act_clear_all"):
            self.act_clear_all.setIcon(IconProvider.get_icon("alert-triangle"))
        if hasattr(self, "btn_export_txt"):
            self.btn_export_txt.setIcon(IconProvider.get_icon("file-text"))
        if hasattr(self, "btn_export_jsonl"):
            self.btn_export_jsonl.setIcon(IconProvider.get_icon("file-code"))
        if hasattr(self, "btn_copy_xpath"):
            self.btn_copy_xpath.setIcon(IconProvider.get_icon("copy"))

        # Re-apply status badge colors for current theme
        self._update_status_badge()

        # Re-populate list and refresh table badges
        if hasattr(self, "list_history") and self._audit_data:
            current_row = self.list_history.currentRow()
            self._populate_history_list()
            if current_row >= 0 and current_row < self.list_history.count():
                self.list_history.setCurrentRow(current_row)

    def _update_status_badge(self):
        """Updates status badge text and theme-specific colors."""
        if not self._audit_data:
            return
        is_dark = self.theme_manager.is_dark()
        audit = self._audit_data
        corrupted = audit.get("corrupted_lines", 0)
        quarantine = audit.get("quarantine_files", [])
        is_locked = audit.get("has_active_lock", False)
        has_pending = audit.get("has_pending_transaction", False)

        if is_locked:
            self.status_badge.setText("🔒 Aktif İşlem / Kilitli")
            bg = "#78350f" if is_dark else "#fef3c7"
            fg = "#fde68a" if is_dark else "#92400e"
            border = "#b45309" if is_dark else "#fcd34d"
        elif has_pending:
            self.status_badge.setText("⚠️ Tamamlanmamış İşlem / Recovery Bekliyor")
            bg = "#7c2d12" if is_dark else "#ffedd5"
            fg = "#fed7aa" if is_dark else "#9a3412"
            border = "#ea580c" if is_dark else "#fdba74"
        elif corrupted > 0:
            self.status_badge.setText(f"⚠️ {corrupted} Bozuk Kayıt Atlandı")
            bg = "#7f1d1d" if is_dark else "#fee2e2"
            fg = "#fecaca" if is_dark else "#991b1b"
            border = "#dc2626" if is_dark else "#fca5a5"
        elif quarantine:
            self.status_badge.setText(f"⚠️ {len(quarantine)} Karantina Dosyası")
            bg = "#7f1d1d" if is_dark else "#fee2e2"
            fg = "#fecaca" if is_dark else "#991b1b"
            border = "#dc2626" if is_dark else "#fca5a5"
        else:
            self.status_badge.setText("✓ Bütünlük Doğrulandı")
            bg = "#064e3b" if is_dark else "#d1fae5"
            fg = "#a7f3d0" if is_dark else "#065f46"
            border = "#059669" if is_dark else "#6ee7b7"

        self.status_badge.setStyleSheet(
            f"background-color: {bg}; color: {fg}; border: 1px solid {border}; "
            f"border-radius: 4px; padding: 4px 8px; font-weight: bold;"
        )

    def keyPressEvent(self, event):
        """Allows deleting the selected ChangeSet directly with the Delete key."""
        if event.key() == Qt.Key.Key_Delete:
            if self.list_history.hasFocus() or self.tbl_changes.hasFocus():
                self._delete_selected_history_item()
                return
        super().keyPressEvent(event)

    def _show_history_context_menu(self, pos):
        """Displays right-click context menu on history list for quick actions."""
        item = self.list_history.itemAt(pos)
        if not item:
            return

        menu = QMenu(self)
        data = item.data(Qt.ItemDataRole.UserRole) or {}
        item_type = data.get("type")

        if item_type == "changeset":
            act_del = menu.addAction(IconProvider.get_icon("trash-2"), "Seçili Kaydı Sil (Del)")
            act_del.triggered.connect(self._delete_selected_history_item)
            menu.addSeparator()

        act_clear = menu.addAction(IconProvider.get_icon("alert-triangle"), "Tüm Geçmişi Temizle...")
        act_clear.triggered.connect(self._clear_all_history)
        menu.exec(self.list_history.mapToGlobal(pos))

    def _delete_selected_history_item(self):
        """Deletes the currently selected ChangeSet record after user confirmation."""
        if not self._selected_item_data:
            QMessageBox.warning(self, "Seçim Yok", "Lütfen silmek istediğiniz geçmiş kaydını listeden seçin.")
            return

        item_type = self._selected_item_data.get("type")
        if item_type == "origin":
            QMessageBox.information(
                self,
                "Silme İşlemi",
                "Başlangıç temel kaydı (Origin Baseline) tek başına silinemez.\n\n"
                "Tüm geçmişi sıfırlamak istiyorsanız 'Tüm Geçmişi Temizle' seçeneğini kullanabilirsiniz."
            )
            return

        if item_type != "changeset":
            QMessageBox.warning(self, "Seçim Hatası", "Yalnızca geçerli ChangeSet kayıtları silinebilir.")
            return

        cs: Optional[ChangeSet] = self._selected_item_data.get("changeset")
        if not cs or not getattr(cs, "save_id", None):
            QMessageBox.warning(self, "Kayıt Bulunamadı", "Seçili ChangeSet kaydına ulaşılamadı.")
            return

        idx = self._selected_item_data.get("index", 1)
        ts_local = format_local_timestamp(cs.timestamp)
        reply = QMessageBox.question(
            self,
            "Geçmiş Kaydını Sil",
            f"Save #{cs.save_id[:12]}… (#{idx}) geçmiş kaydını silmek istediğinize emin misiniz?\n\n"
            f"Tarih / Saat: {ts_local}\n"
            f"Değişiklik Sayısı: {len(cs.changes)}\n\n"
            "Bu işlem kaydı kütükten kalıcı olarak kaldıracaktır.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        success = False
        try:
            success = self.doc_session.delete_history_changeset(cs.save_id)
        except Exception as e:
            logger.error("Error deleting changeset %s: %s", cs.save_id, e)
            QMessageBox.critical(self, "Silme Hatası", f"Kayıt silinirken hata oluştu:\n{e}")
            return

        if success:
            self.reload()
            QMessageBox.information(self, "Kayıt Silindi", f"Save #{cs.save_id[:12]}… geçmiş kaydı başarıyla silindi.")
        else:
            QMessageBox.warning(self, "Silme Başarısız", "Kayıt silinemedi veya kilit uyuşmazlığı yaşandı.")

    def _clear_all_history(self):
        """Completely clears all history records after user confirmation."""
        if not self.doc_session or not self.doc_session.file_path:
            QMessageBox.warning(self, "Hata", "Açık bir belge bulunamadı.")
            return

        reply = QMessageBox.warning(
            self,
            "Tüm Geçmişi Temizle",
            "DİKKAT: Bu belgeye ait TÜM geçmiş kayıtları ve denetim kütüğü kalıcı olarak silinecektir.\n\n"
            "Belgenin diskteki mevcut hali yeni temiz başlangıç (Origin Baseline) olarak kabul edilecektir.\n\n"
            "Bu işlemi onaylıyor musunuz?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        success = False
        try:
            success = self.doc_session.clear_all_history()
        except Exception as e:
            logger.error("Error clearing all history: %s", e)
            QMessageBox.critical(self, "Temizleme Hatası", f"Geçmiş temizlenirken hata oluştu:\n{e}")
            return

        if success:
            self.reload()
            QMessageBox.information(self, "Geçmiş Temizlendi", "Belgenin tüm geçmiş kayıtları başarıyla temizlendi.")
        else:
            QMessageBox.warning(self, "İşlem Başarısız", "Geçmiş temizlenemedi veya kilit açılamadı.")
