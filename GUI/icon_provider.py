"""
GUI/icon_provider.py — Centralized High-DPI Vector SVG Icon Provider

Provides cached, theme-tintable, and multi-resolution QIcon / QPixmap instances
rendered from vector Lucide SVG templates in resources/icons/.
"""

import logging
import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QByteArray, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication, QWidget

logger = logging.getLogger(__name__)


def get_icons_dir() -> Path:
    """Resolves the icons directory path in dev or PyInstaller frozen bundles."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "resources" / "icons"
    return Path(__file__).resolve().parent.parent / "resources" / "icons"


class IconProvider:
    """Centralized, cache-backed, theme-tintable vector SVG icon manager."""

    DEFAULT_COLOR = "#1e1e1e"
    _current_default_color: str = "#1e1e1e"
    DEFAULT_SIZES = (16, 20, 24)
    SUPPORTED_DPRS = (1.0, 1.25, 1.5, 1.75, 2.0)

    _svg_template_cache: dict[str, str] = {}
    _icon_cache: dict[tuple[str, str, tuple[int, ...]], QIcon] = {}
    _pixmap_cache: dict[tuple[str, str, int, float], QPixmap] = {}

    @classmethod
    def set_default_color(cls, color: str) -> None:
        """Sets the active default color used when color=None in get_icon/get_pixmap."""
        clean = str(color).strip()
        if QColor.isValidColor(clean):
            cls._current_default_color = QColor(clean).name().lower()
        else:
            cls._current_default_color = cls.DEFAULT_COLOR

    @classmethod
    def get_default_color(cls) -> str:
        """Returns the active default color."""
        return cls._current_default_color

    @classmethod
    def _normalize_color(cls, color: Optional[str]) -> str:
        """Validates and normalizes color string to lower-case 7-char hex."""
        if not color or not str(color).strip():
            return cls._current_default_color
        color_str = str(color).strip()
        if QColor.isValidColor(color_str):
            return QColor(color_str).name().lower()
        return cls._current_default_color

    @classmethod
    def _load_template(cls, name: str) -> Optional[str]:
        """Loads and caches raw SVG template text from resources/icons/."""
        clean_name = name.lower().strip()
        if not clean_name.endswith(".svg"):
            clean_name += ".svg"

        if clean_name in cls._svg_template_cache:
            return cls._svg_template_cache[clean_name]

        file_path = get_icons_dir() / clean_name
        if not file_path.is_file():
            logger.warning("SVG icon file not found: %s", file_path)
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            cls._svg_template_cache[clean_name] = content
            return content
        except Exception as e:
            logger.error("Failed to read SVG file %s: %s", file_path, e)
            return None

    @classmethod
    def get_icon(
        cls,
        name: str,
        color: Optional[str] = None,
        sizes: tuple[int, ...] = DEFAULT_SIZES,
    ) -> QIcon:
        """Returns a multi-resolution, Hi-DPI ready QIcon with injected stroke color."""
        clean_name = name.lower().strip()
        if clean_name.endswith(".svg"):
            clean_name = clean_name[:-4]

        norm_color = cls._normalize_color(color)
        normalized_sizes = tuple(sorted(set(sizes)))
        cache_key = (clean_name, norm_color, normalized_sizes)

        if cache_key in cls._icon_cache:
            return cls._icon_cache[cache_key]

        template = cls._load_template(clean_name)
        if template is None:
            return QIcon()

        tinted_svg = template.replace("{{COLOR}}", norm_color)
        renderer = QSvgRenderer(QByteArray(tinted_svg.encode("utf-8")))
        if not renderer.isValid():
            logger.warning("Corrupt or invalid SVG renderer for icon: %s", clean_name)
            return QIcon()

        icon = QIcon()
        for logical_size in normalized_sizes:
            for dpr in cls.SUPPORTED_DPRS:
                phys_size = int(round(logical_size * dpr))
                pixmap = QPixmap(phys_size, phys_size)
                pixmap.fill(Qt.GlobalColor.transparent)
                pixmap.setDevicePixelRatio(dpr)

                painter = QPainter(pixmap)
                renderer.render(painter, QRectF(0, 0, logical_size, logical_size))
                painter.end()

                icon.addPixmap(pixmap)

        cls._icon_cache[cache_key] = icon
        return icon

    @classmethod
    def get_pixmap(
        cls,
        name: str,
        size: int = 16,
        color: Optional[str] = None,
        dpr: Optional[float] = None,
        widget: Optional[QWidget] = None,
    ) -> QPixmap:
        """Returns a single crisp QPixmap dynamically rendered for the target DPR."""
        clean_name = name.lower().strip()
        if clean_name.endswith(".svg"):
            clean_name = clean_name[:-4]

        # Resolve DPR dynamically if not explicitly specified
        if dpr is None or dpr <= 0.0:
            if widget is not None and hasattr(widget, "devicePixelRatioF"):
                dpr = widget.devicePixelRatioF()
            if dpr is None or dpr <= 0.0:
                screen = QApplication.primaryScreen()
                dpr = screen.devicePixelRatio() if screen else 1.0

        dpr_key = round(float(dpr), 2)
        norm_color = cls._normalize_color(color)
        cache_key = (clean_name, norm_color, size, dpr_key)

        if cache_key in cls._pixmap_cache:
            return cls._pixmap_cache[cache_key]

        template = cls._load_template(clean_name)
        if template is None:
            return QPixmap()

        tinted_svg = template.replace("{{COLOR}}", norm_color)
        renderer = QSvgRenderer(QByteArray(tinted_svg.encode("utf-8")))
        if not renderer.isValid():
            return QPixmap()

        phys_size = int(round(size * dpr_key))
        pixmap = QPixmap(phys_size, phys_size)
        pixmap.fill(Qt.GlobalColor.transparent)
        pixmap.setDevicePixelRatio(dpr_key)

        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()

        cls._pixmap_cache[cache_key] = pixmap
        return pixmap

    @classmethod
    def clear_cache(cls):
        """Clears all cached SVG templates, QIcons, and QPixmaps."""
        cls._svg_template_cache.clear()
        cls._icon_cache.clear()
        cls._pixmap_cache.clear()

