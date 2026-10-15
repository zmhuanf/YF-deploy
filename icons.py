"""内嵌 SVG 图标库与渲染工具 不依赖外部图片文件"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# 所有图标共用同一套描边风格 用 {color} 占位符实现按状态换色
_HEAD = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}"'
    ' stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
)

ICONS = {
    "server": _HEAD + '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/></svg>',
    "project": _HEAD + '<path d="M3 6.5A2.5 2.5 0 0 1 5.5 4h3.3a2 2 0 0 1 1.7.9l1.1 1.6h7.9A2.5 2.5 0 0 1 21 9v8.5a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/></svg>',
    "folder": _HEAD + '<path d="M3 6.5A2.5 2.5 0 0 1 5.5 4h3.3a2 2 0 0 1 1.7.9l1.1 1.6h7.9A2.5 2.5 0 0 1 21 9v8.5a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/></svg>',
    "file": _HEAD + '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>',
    "code": _HEAD + '<path d="M9 8l-4 4 4 4"/><path d="M15 8l4 4-4 4"/></svg>',
    "archive": _HEAD + '<rect x="3" y="4" width="18" height="5" rx="1.5"/><path d="M5 9v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9"/><path d="M10 13h4"/></svg>',
    "image": _HEAD + '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9.5" r="1.4"/><path d="M21 15.5l-5-5-6.5 6.5-2.5-2.5-4 4"/></svg>',
    "text": _HEAD + '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h4"/></svg>',
}

# 后缀到图标名的映射 未命中的一律用通用文件图标
SUFFIX_ICONS = {
    ".py": "code", ".js": "code", ".ts": "code", ".jsx": "code", ".tsx": "code",
    ".java": "code", ".go": "code", ".rs": "code", ".rb": "code", ".php": "code",
    ".c": "code", ".h": "code", ".cpp": "code", ".hpp": "code", ".cs": "code",
    ".sh": "code", ".bash": "code", ".zsh": "code", ".sql": "code", ".lua": "code",
    ".json": "code", ".yml": "code", ".yaml": "code", ".toml": "code", ".ini": "code",
    ".xml": "code", ".html": "code", ".css": "code", ".scss": "code", ".less": "code",
    ".zip": "archive", ".tar": "archive", ".gz": "archive", ".tgz": "archive",
    ".bz2": "archive", ".xz": "archive", ".7z": "archive", ".rar": "archive", ".jar": "archive",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".bmp": "image",
    ".svg": "image", ".webp": "image", ".ico": "image", ".tiff": "image",
    ".txt": "text", ".md": "text", ".log": "text", ".csv": "text", ".conf": "text", ".cfg": "text",
}


def icon_name_for(name: str, is_dir: bool) -> str:
    """按目录标记与文件后缀决定用哪个图标"""
    if is_dir:
        return "folder"
    return SUFFIX_ICONS.get(Path(name).suffix.lower(), "file")


def render(icon: str, color: str, size: int, ratio: float = 1.0) -> QPixmap:
    """按颜色与逻辑尺寸渲染矢量图标 高分屏按 ratio 放大后端再交回"""
    pixels = max(1, round(size * ratio))
    pixmap = QPixmap(pixels, pixels)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    svg = ICONS.get(icon, ICONS["file"]).format(color=color)
    QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(0, 0, pixels, pixels))
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap