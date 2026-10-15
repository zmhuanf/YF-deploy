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

from project_page import ProjectPage
from server_page import ServerPage
from store import Store

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
QPlainTextEdit#log {
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
        for title, page in zip(NAV_ITEMS, (self.server_page, self.project_page)):
            self.nav.addItem(QListWidgetItem(title))
            self.pages.addWidget(page)

        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)
        self.server_page.changed.connect(self.project_page.refresh)
        self.statusBar().showMessage("就绪")

        body = QWidget()
        layout = QHBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.nav)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(body)


def main() -> int:
    """创建应用进入事件循环"""
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_QSS)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())