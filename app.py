"""YF Deploy 运维小工具入口"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QWidget,
)

from browser_page import BrowserPage
from project_page import ProjectPage
from server_page import ServerPage
from store import Server, Store

APP_QSS = """
QListWidget#nav {
    background: #2f3542;
    border: none;
    outline: none;
    color: #d5dae2;
    font-size: 14px;
}
QListWidget#nav::item {
    height: 46px;
    padding-left: 20px;
}
QListWidget#nav::item:selected {
    background: #1e90ff;
    color: #ffffff;
}
QListWidget#nav::item:hover:!selected {
    background: #3d4553;
}
QListWidget#cards {
    background: #f6f7f9;
    border: none;
    outline: none;
}
QPlainTextEdit#log, QPlainTextEdit#viewer {
    background: #1e1f22;
    color: #d7dae0;
    font-family: Consolas, "Courier New", monospace;
    border: none;
}
"""

NAV_ITEMS = ("服务器", "项目")


class MainWindow(QMainWindow):
    """左侧导航 + 右侧页面容器"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("YF Deploy")
        self.resize(1160, 740)

        self.store = Store()
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(140)
        self.nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.pages = QStackedWidget()

        self.server_page = ServerPage(self.store)
        self.project_page = ProjectPage(self.store)
        self.browser_page = BrowserPage()
        for title, page in zip(NAV_ITEMS, (self.server_page, self.project_page)):
            self.nav.addItem(QListWidgetItem(title))
            self.pages.addWidget(page)
        self.pages.addWidget(self.browser_page)

        self.nav.currentRowChanged.connect(self._on_nav_changed)
        self.nav.itemClicked.connect(self._on_nav_clicked)
        self.nav.setCurrentRow(0)
        self.server_page.changed.connect(self.project_page.refresh)
        self.server_page.open_requested.connect(self._open_browser)
        self.browser_page.closed.connect(self._back_to_list)
        self.statusBar().showMessage("就绪")

        body = QWidget()
        layout = QHBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.nav)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(body)

    def _open_browser(self, server: Server) -> None:
        """双击服务器进入远端文件管理器"""
        self.pages.setCurrentWidget(self.browser_page)
        self.browser_page.open_server(server)

    def _back_to_list(self) -> None:
        """从文件管理器返回服务器列表"""
        self.browser_page.close_session()
        self.nav.setCurrentRow(0)
        self.pages.setCurrentIndex(0)

    def _on_nav_changed(self, row: int) -> None:
        """切换导航时收起文件管理器并断开连接"""
        if self.pages.currentWidget() is self.browser_page:
            self.browser_page.close_session()
        self.pages.setCurrentIndex(row)

    def _on_nav_clicked(self, item: QListWidgetItem) -> None:
        """点已选中的导航项也要能离开文件管理器"""
        self._on_nav_changed(self.nav.row(item))


def main() -> int:
    """创建应用进入事件循环"""
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_QSS)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())