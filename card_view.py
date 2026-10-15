"""卡片式列表视图 供服务器页与项目页共用"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QListWidget, QListView, QStyle, QStyledItemDelegate, QWidget

import icons

ROLE_ICON = Qt.ItemDataRole.UserRole + 1
ROLE_TITLE = Qt.ItemDataRole.UserRole + 2
ROLE_SUBTITLE = Qt.ItemDataRole.UserRole + 3
ROLE_EXTRA = Qt.ItemDataRole.UserRole + 4
ROLE_BADGE = Qt.ItemDataRole.UserRole + 5
ROLE_KEY = Qt.ItemDataRole.UserRole + 6

GRID_SIZE = QSize(292, 104)
PADDING = 6
ICON_SIZE = 40

COLOR_CARD = "#ffffff"
COLOR_BORDER = "#e3e7ee"
COLOR_HOVER_BG = "#f2f5f9"
COLOR_HOVER_BORDER = "#c9d3e2"
COLOR_SELECT_BG = "#eaf3ff"
COLOR_SELECT_BORDER = "#1e90ff"
COLOR_TITLE = "#1f2937"
COLOR_SUBTITLE = "#6b7280"
COLOR_EXTRA = "#9ca3af"
COLOR_ICON = "#64748b"
COLOR_ICON_SELECT = "#1e90ff"
COLOR_BADGE_BG = "#eef2f7"
COLOR_BADGE_TEXT = "#64748b"


def make_card_view(parent: QWidget | None = None) -> QListWidget:
    """创建一个图标网格模式 单选 不可拖动的卡片列表"""
    view = QListWidget(parent)
    view.setObjectName("cards")
    view.setViewMode(QListView.ViewMode.IconMode)
    view.setFlow(QListView.Flow.LeftToRight)
    view.setWrapping(True)
    view.setResizeMode(QListView.ResizeMode.Adjust)
    view.setMovement(QListView.Movement.Static)
    view.setGridSize(GRID_SIZE)
    view.setUniformItemSizes(True)
    view.setWordWrap(False)
    view.setSelectionMode(QListView.SelectionMode.SingleSelection)
    view.setEditTriggers(QListView.EditTrigger.NoEditTriggers)
    view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
    view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    view.setFrameShape(QListView.Shape.NoFrame)
    view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    view.setItemDelegate(CardDelegate(view))
    return view


class CardDelegate(QStyledItemDelegate):
    """把每个列表项画成一张带图标与多行文字的卡片"""

    NAME_HEIGHT = 20
    LINE_HEIGHT = 17
    BADGE_HEIGHT = 17

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icons: dict[tuple, QPixmap] = {}

    def sizeHint(self, option, index) -> QSize:
        """格子尺寸由视图的 gridSize 统一决定"""
        return option.widget.gridSize() if option.widget else GRID_SIZE

    def paint(self, painter: QPainter, option, index) -> None:
        """绘制卡片底色 图标 标题 副标题与角标"""
        rect = option.rect.adjusted(PADDING, PADDING, -PADDING, -PADDING)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        border = COLOR_SELECT_BORDER if selected else COLOR_HOVER_BORDER if hovered else COLOR_BORDER
        background = COLOR_SELECT_BG if selected else COLOR_HOVER_BG if hovered else COLOR_CARD
        painter.setPen(QPen(QColor(border), 1))
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 10, 10)

        icon_rect = QRect(rect.left() + 16, rect.top() + (rect.height() - ICON_SIZE) // 2, ICON_SIZE, ICON_SIZE)
        painter.drawPixmap(icon_rect, self._icon(index.data(ROLE_ICON), selected, option.widget))

        left = icon_rect.right() + 14
        right = rect.right() - 16
        lines = [text for text in (index.data(ROLE_SUBTITLE), index.data(ROLE_EXTRA)) if text]
        top = rect.center().y() - (self.NAME_HEIGHT + self.LINE_HEIGHT * len(lines)) // 2

        badge = index.data(ROLE_BADGE) or ""
        badge_width = self._measure_badge(option.font, badge) if badge else 0
        title_right = right - badge_width - 8 if badge_width else right
        self._draw_title(painter, option.font, left, title_right, top, index.data(ROLE_TITLE) or "")
        self._draw_body(painter, option.font, left, right, top, lines)
        if badge_width:
            self._draw_badge(painter, option.font, right, top, badge, badge_width)
        painter.restore()

    def _draw_title(self, painter: QPainter, base: QFont, left: int, right: int, top: int, title: str) -> None:
        """绘制加粗标题 宽度不够时省略"""
        font = QFont(base)
        font.setPixelSize(14)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(COLOR_TITLE))
        text = QFontMetrics(font).elidedText(title, Qt.TextElideMode.ElideRight, right - left)
        painter.drawText(QRect(left, top, right - left, self.NAME_HEIGHT), self._align(), text)

    def _draw_body(self, painter: QPainter, base: QFont, left: int, right: int, top: int, lines: list[str]) -> None:
        """绘制副标题与第三行 颜色逐行变浅"""
        font = QFont(base)
        font.setPixelSize(12)
        metrics = QFontMetrics(font)
        painter.setFont(font)
        for row, text in enumerate(lines):
            painter.setPen(QColor(COLOR_SUBTITLE if row == 0 else COLOR_EXTRA))
            y = top + self.NAME_HEIGHT + row * self.LINE_HEIGHT
            width = right - left
            painter.drawText(QRect(left, y, width, self.LINE_HEIGHT), self._align(), metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, width))

    def _draw_badge(self, painter: QPainter, base: QFont, right: int, top: int, badge: str, badge_width: int) -> None:
        """绘制标题行右侧的角标"""
        font = QFont(base)
        font.setPixelSize(10)
        painter.setFont(font)
        rect = QRect(right - badge_width, top + (self.NAME_HEIGHT - self.BADGE_HEIGHT) // 2, badge_width, self.BADGE_HEIGHT)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLOR_BADGE_BG))
        painter.drawRoundedRect(rect, 6, 6)
        painter.setPen(QColor(COLOR_BADGE_TEXT))
        painter.drawText(rect, self._align(), badge)

    def _icon(self, key: str, selected: bool, widget: QWidget | None) -> QPixmap:
        """按状态渲染矢量图标 结果缓存避免重复解析 SVG"""
        color = COLOR_ICON_SELECT if selected else COLOR_ICON
        ratio = widget.devicePixelRatioF() if widget else 1.0
        cached = self._icons.get((key, color, ratio))
        if cached is None:
            cached = icons.render(key, color, ICON_SIZE, ratio)
            self._icons[(key, color, ratio)] = cached
        return cached

    @staticmethod
    def _measure_badge(base: QFont, badge: str) -> int:
        """角标的宽度 由文字宽度加内边距得出"""
        font = QFont(base)
        font.setPixelSize(10)
        return QFontMetrics(font).horizontalAdvance(badge) + 14

    @staticmethod
    def _align() -> int:
        """统一的左对齐加垂直居中"""
        return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)