"""
GUI/theme_manager.py — Centralized Theme Management for XML Analyzer

Provides theme state management, QSettings persistence, semantic color tokens,
and hot-reloading of QSS stylesheets and icon tinting.
"""

import logging
import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, QSettings, pyqtSignal
from PyQt6.QtWidgets import QApplication

from GUI.icon_provider import IconProvider

logger = logging.getLogger(__name__)


def get_styles_dir() -> Path:
    """Resolves the styles directory path in dev or PyInstaller frozen bundles."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "resources" / "styles"
    return Path(__file__).resolve().parent.parent / "resources" / "styles"


# ======================================================================
#  SEMANTIC COLOR PALETTE
# ======================================================================
PALETTE: dict[str, dict[str, str]] = {
    "light": {
        # ── General Window & Surfaces ──
        "window_bg": "#f0f0f0",
        "panel_base": "#ffffff",
        "header_bg": "#e8e8e8",
        "border_default": "#c8c8c8",
        "border_muted": "#dcdcdc",
        "text_primary": "#1e1e1e",
        "text_muted": "#606060",
        "text_secondary": "#808080",
        "accent": "#0066cc",
        "accent_hover": "#0052a3",
        "accent_pressed": "#003d7a",
        "selection_bg": "#dce7f5",
        "selection_fg": "#1e1e1e",
        # ── Icon Colors ──
        "icon_default": "#1e1e1e",
        "icon_muted": "#505050",
        "icon_subtle": "#707070",
        "icon_on_accent": "#ffffff",
        # ── Menu & Toolbar ──
        "menu_bg": "#ffffff",
        "menu_item_hover": "#e8e8e8",
        "toolbar_btn_hover": "#e0e0e0",
        "toolbar_btn_pressed": "#d4d4d4",
        # ── Text Editor & Syntax Highlighting ──
        "editor_bg": "#ffffff",
        "editor_gutter_bg": "#f0f0f0",
        "editor_gutter_border": "#d0d0d0",
        "editor_gutter_text": "#888888",
        "editor_current_line": "#fffde7",
        "editor_search_match": "#ffe082",
        "editor_syntax_tag": "#0000ff",
        "editor_syntax_attr_name": "#800080",
        "editor_syntax_attr_val": "#ff0000",
        "editor_syntax_comment": "#008000",
        "editor_syntax_decl": "#808080",
        # ── Grid View ──
        "grid_bg": "#ffffff",
        "grid_frame_header": "#f1f4f8",
        "grid_frame_border": "#d4d8dc",
        "grid_table_header": "#e8e8e8",
        "grid_cell_bg": "#ffffff",
        "grid_cell_alt": "#f7f7f7",
        "grid_border": "#d8d8d8",
        "grid_search_bg": "#fffacd",
        "grid_search_border": "#e6b800",
        "grid_search_text": "#1e1e1e",
        "grid_search_row_bg": "#fff8dc",
        "grid_search_cell_bg": "#fffdf0",
        "grid_active_bg": "#fff8e1",
        "grid_active_border": "#ffb74d",
        "grid_comment_bg": "#f6fcf6",
        "grid_comment_text": "#1b5e20",
        # ── Search Panel & Filter Explorer ──
        "search_input_bg": "#ffffff",
        "search_input_border": "#b8b8b8",
        "filter_cond_bg": "#f8f8f8",
        "filter_cond_border": "#dcdcdc",
        # ── Validation Bar ──
        "val_bg": "#fdf2f2",
        "val_border": "#f5c6cb",
        "val_text": "#491217",
        "val_badge_bg": "#dc3545",
        "val_badge_fg": "#ffffff",
        # ── WelcomeWidget ──
        "welcome_outer_bg": "#f7f7f7",
        "welcome_card_bg": "#ffffff",
        "welcome_card_border": "#d0d0d0",
        "welcome_recent_bg": "#f2f2f2",
        "welcome_recent_hover": "#e5e5e5",
        "welcome_recent_border": "#dcdcdc",
        # ── Sync Warning Banner ──
        "sync_banner_bg": "#fff3cd",
        "sync_banner_border": "#ffeeba",
        "sync_banner_text": "#856404",
        # ── JSON Preview ──
        "json_copy_bg": "#f5f5f5",
        "json_copy_border": "#c0c0c0",
        "json_copy_text": "#333333",
    },
    "dark": {
        # ── General Window & Surfaces ──
        "window_bg": "#18181a",
        "panel_base": "#1e1e22",
        "header_bg": "#25252a",
        "border_default": "#333338",
        "border_muted": "#2a2a2f",
        "text_primary": "#e4e4e7",
        "text_muted": "#909096",
        "text_secondary": "#a0a0a8",
        "accent": "#3b82f6",
        "accent_hover": "#2563eb",
        "accent_pressed": "#1d4ed8",
        "selection_bg": "#264f78",
        "selection_fg": "#ffffff",
        # ── Icon Colors ──
        "icon_default": "#e0e0e0",
        "icon_muted": "#a0a0a4",
        "icon_subtle": "#85858a",
        "icon_on_accent": "#ffffff",
        # ── Menu & Toolbar ──
        "menu_bg": "#222226",
        "menu_item_hover": "#2d2d33",
        "toolbar_btn_hover": "#2d2d33",
        "toolbar_btn_pressed": "#37373f",
        # ── Text Editor & Syntax Highlighting ──
        "editor_bg": "#1e1e22",
        "editor_gutter_bg": "#18181a",
        "editor_gutter_border": "#2c2c32",
        "editor_gutter_text": "#75757d",
        "editor_current_line": "#26262c",
        "editor_search_match": "#614300",
        "editor_syntax_tag": "#569cd6",
        "editor_syntax_attr_name": "#9cdcfe",
        "editor_syntax_attr_val": "#ce9178",
        "editor_syntax_comment": "#6a9955",
        "editor_syntax_decl": "#808080",
        # ── Grid View ──
        "grid_bg": "#1e1e22",
        "grid_frame_header": "#25252b",
        "grid_frame_border": "#383842",
        "grid_table_header": "#2a2a30",
        "grid_cell_bg": "#1e1e22",
        "grid_cell_alt": "#242428",
        "grid_border": "#333338",
        "grid_search_bg": "#4a3800",
        "grid_search_border": "#ffd54f",
        "grid_search_text": "#ffffff",
        "grid_search_row_bg": "#4a3800",
        "grid_search_cell_bg": "#3d2f0a",
        "grid_active_bg": "#3d2f0a",
        "grid_active_border": "#ffb74d",
        "grid_comment_bg": "#1a2e1d",
        "grid_comment_text": "#81c784",
        # ── Search Panel & Filter Explorer ──
        "search_input_bg": "#18181a",
        "search_input_border": "#3d3d44",
        "filter_cond_bg": "#242428",
        "filter_cond_border": "#35353c",
        # ── Validation Bar ──
        "val_bg": "#341818",
        "val_border": "#5c2424",
        "val_text": "#fca5a5",
        "val_badge_bg": "#ef4444",
        "val_badge_fg": "#ffffff",
        # ── WelcomeWidget ──
        "welcome_outer_bg": "#141416",
        "welcome_card_bg": "#202024",
        "welcome_card_border": "#33333a",
        "welcome_recent_bg": "#27272c",
        "welcome_recent_hover": "#323238",
        "welcome_recent_border": "#3a3a42",
        # ── Sync Warning Banner ──
        "sync_banner_bg": "#38290a",
        "sync_banner_border": "#5c430e",
        "sync_banner_text": "#fbbf24",
        # ── JSON Preview ──
        "json_copy_bg": "#27272c",
        "json_copy_border": "#3a3a42",
        "json_copy_text": "#e0e0e0",
    },
}


class ThemeManager(QObject):
    """Centralized manager for application-wide theme state, stylesheets, and palettes."""

    theme_changed = pyqtSignal(str)

    SETTINGS_ORG = "XMLAnalyzer"
    SETTINGS_APP = "XMLAnalyzer"
    SETTINGS_KEY = "theme"

    THEME_LIGHT = "light"
    THEME_DARK = "dark"
    SUPPORTED_THEMES = (THEME_LIGHT, THEME_DARK)

    def __init__(self, parent: Optional[QObject] = None, settings_override: Optional[QSettings] = None):
        super().__init__(parent)
        self._settings = settings_override or QSettings(self.SETTINGS_ORG, self.SETTINGS_APP)
        self._current_theme = self.get_saved_theme()

    def get_saved_theme(self) -> str:
        """Reads the persisted theme preference from QSettings."""
        val = self._settings.value(self.SETTINGS_KEY, self.THEME_LIGHT)
        if isinstance(val, str) and val.lower() in self.SUPPORTED_THEMES:
            return val.lower()
        return self.THEME_LIGHT

    def get_current_theme(self) -> str:
        """Returns the actively applied theme name ('light' or 'dark')."""
        return self._current_theme

    def is_dark(self) -> bool:
        """Returns True if the active theme is 'dark'."""
        return self._current_theme == self.THEME_DARK

    def get_palette(self, theme: Optional[str] = None) -> dict[str, str]:
        """Returns the dictionary of color tokens for the specified or current theme."""
        t = (theme or self._current_theme).lower()
        return PALETTE.get(t, PALETTE[self.THEME_LIGHT])

    def get_color(self, token: str, theme: Optional[str] = None) -> str:
        """Returns a specific color token string (e.g. '#0066cc')."""
        pal = self.get_palette(theme)
        return pal.get(token, "#1e1e1e")

    def get_qss(self, theme: Optional[str] = None) -> str:
        """Loads and returns the raw QSS stylesheet text for the requested theme."""
        t = (theme or self._current_theme).lower()
        filename = "dark_theme.qss" if t == self.THEME_DARK else "light_theme.qss"
        qss_path = get_styles_dir() / filename
        if not qss_path.is_file():
            logger.warning(f"QSS file not found: {qss_path}")
            return ""
        try:
            with open(qss_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.error(f"Failed to read QSS file {qss_path}: {e}")
            return ""

    def apply_theme(self, theme_name: str, save_preference: bool = True, force: bool = False) -> bool:
        """Applies the requested theme to QApplication and notifies all components."""
        normalized = theme_name.lower().strip()
        if normalized not in self.SUPPORTED_THEMES:
            logger.warning(f"Unsupported theme: {theme_name}. Falling back to 'light'.")
            normalized = self.THEME_LIGHT

        # Early return if already applied, avoiding duplicate I/O and signals
        app_inst = QApplication.instance()
        if not force and normalized == self._current_theme and app_inst and app_inst.styleSheet():
            if save_preference:
                self._settings.setValue(self.SETTINGS_KEY, normalized)
            return True

        # 1. Persist preference if requested
        if save_preference:
            self._settings.setValue(self.SETTINGS_KEY, normalized)

        # 2. Update default icon color and reset cache
        icon_color = PALETTE[normalized]["icon_default"]
        IconProvider.set_default_color(icon_color)
        IconProvider.clear_cache()

        # 3. Apply stylesheet at QApplication level
        qss_content = self.get_qss(normalized)
        app_inst = QApplication.instance()
        if app_inst:
            app_inst.setStyleSheet(qss_content)

        # 4. Update internal state
        self._current_theme = normalized

        # 5. Emit signal for in-place component updates
        self.theme_changed.emit(normalized)
        logger.info(f"Theme successfully applied: {normalized}")
        return True

    def toggle_theme(self) -> str:
        """Toggles between light and dark theme and returns the newly active theme."""
        target = self.THEME_DARK if self._current_theme == self.THEME_LIGHT else self.THEME_LIGHT
        self.apply_theme(target, save_preference=True)
        return target

