"""服务器端口列表弹框 展示已记录的端口 支持扫描与备注编辑"""

from __future__ import annotations

import paramiko
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import ports
import ssh
from store import PortRecord, ScannedPort, Server, Store

COLUMNS = ("路径", "程序", "端口", "存活状态", "备注")
PORT_COLUMN = 2
REMARK_COLUMN = 4
STATUS_ALIVE = "存活"
STATUS_STOPPED = "已停止"
COLOR_ALIVE = "#16a34a"
COLOR_STOPPED = "#9ca3af"


class PortScanWorker(QThread):
    """后台连接服务器扫描正在监听的端口"""

    scanned = Signal(object)
    failed = Signal(str)

    def __init__(self, server: Server, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.server = server
        self._client: paramiko.SSHClient | None = None

    def stop(self) -> None:
        """断开连接让阻塞中的读取立即抛错退出"""
        if self._client:
            self._client.close()

    def run(self) -> None:
        """连上远端执行扫描 结果交回主线程落盘"""
        try:
            self._client = ssh.connect(self.server)
            self.scanned.emit(ports.scan(self._client, self.server.os_type))
        except Exception as error:
            self.failed.emit(f"扫描失败：{error}")
        finally:
            if self._client:
                self._client.close()
                self._client = None


class PortDialog(QDialog):
    """一台服务器的端口记录 扫描后按占用情况增删 备注可单击编辑"""

    def __init__(self, store: Store, server: Server, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"端口列表 - {server.name}")
        self.resize(720, 480)
        self.store = store
        self.server = server
        self.worker: PortScanWorker | None = None
        self._loading = False

        self.scan_button = QPushButton("扫描所有端口")
        self.scan_button.clicked.connect(self._scan)
        self.status = QLabel()
        self.status.setStyleSheet("color:#6b7280;")
        header = QHBoxLayout()
        header.addWidget(self.scan_button)
        header.addWidget(self.status, 1)

        hint = QLabel("单击「备注」即可填写；扫描后无备注且已不占用的记录会被移除，有备注的只标记为已停止")
        hint.setStyleSheet("color:#6b7280;")
        hint.setWordWrap(True)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.EditKeyPressed | QTableWidget.EditTrigger.DoubleClicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.cellClicked.connect(self._edit_remark)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.itemChanged.connect(self._on_remark_changed)
        headers = self.table.horizontalHeader()
        headers.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        headers.setSectionResizeMode(REMARK_COLUMN, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            headers.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(hint)
        layout.addWidget(self.table, 1)
        layout.addWidget(buttons)

        self._reload()

    def closeEvent(self, event) -> None:
        """扫描没结束时先断连接等线程收尾 避免回调打到已关闭的窗口"""
        if self.worker and self.worker.isRunning():
            self.worker.disconnect()
            self.worker.stop()
            self.worker.wait(5000)
        super().closeEvent(event)

    def _reload(self) -> None:
        """按 store 里的记录重建表格 端口号升序"""
        self._loading = True
        records = self.store.ports_for(self.server.id)
        self.table.setRowCount(len(records))
        for row, record in enumerate(records):
            self._fill_row(row, record)
        self._loading = False
        alive = sum(1 for record in records if record.alive)
        self.status.setText(f"共 {len(records)} 个端口 存活 {alive} 个")

    def _fill_row(self, row: int, record: PortRecord) -> None:
        """路径 程序 端口 存活状态只读 备注列可编辑 端口号挂在数据角色上供回查"""
        path = self._cell(record.path or "—")
        path.setToolTip(record.path)
        port = self._cell(str(record.port))
        port.setData(Qt.ItemDataRole.UserRole, record.port)
        status = self._cell(STATUS_ALIVE if record.alive else STATUS_STOPPED)
        status.setForeground(QColor(COLOR_ALIVE if record.alive else COLOR_STOPPED))
        remark = self._cell(record.remark)
        remark.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable)
        for column, item in enumerate((path, self._cell(record.program or "—"), port, status, remark)):
            self.table.setItem(row, column, item)

    def _edit_remark(self, row: int, column: int) -> None:
        """单击备注列直接进入编辑"""
        if column == REMARK_COLUMN:
            self.table.editItem(self.table.item(row, column))

    def _on_remark_changed(self, item: QTableWidgetItem) -> None:
        """备注改完立即落盘"""
        if self._loading or item.column() != REMARK_COLUMN:
            return
        record = self._record_at(item.row())
        if record is None or record.remark == item.text().strip():
            return
        record.remark = item.text().strip()
        self.store.upsert_port(record)

    def _record_at(self, row: int) -> PortRecord | None:
        """按行号回查对应的端口记录"""
        item = self.table.item(row, PORT_COLUMN)
        if item is None:
            return None
        port = item.data(Qt.ItemDataRole.UserRole)
        return next((record for record in self.store.ports if record.server_id == self.server.id and record.port == port), None)

    def _context_menu(self, pos) -> None:
        """右键某行可删掉这条记录"""
        index = self.table.indexAt(pos)
        record = self._record_at(index.row()) if index.isValid() else None
        if record is None:
            return
        menu = QMenu(self)
        menu.addAction(f"删除端口 {record.port} 的记录")
        if menu.exec(self.table.viewport().mapToGlobal(pos)) is not None:
            self._delete(record)

    def _delete(self, record: PortRecord) -> None:
        """二次确认后删掉记录 远端服务不受影响"""
        answer = QMessageBox.question(
            self,
            "删除确认",
            f"确定删除端口 {record.port} 的记录吗？\n只会移除本工具里的记录，不影响远端服务。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.store.remove_port(self.server.id, record.port)
        self._reload()

    def _scan(self) -> None:
        """连服务器扫一遍正在监听的端口"""
        if self.worker and self.worker.isRunning():
            return
        self.scan_button.setEnabled(False)
        self.status.setText("正在扫描…")
        worker = PortScanWorker(self.server, self)
        worker.scanned.connect(self._on_scanned)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        worker.finished.connect(worker.deleteLater)
        self.worker = worker
        worker.start()

    def _on_scanned(self, listeners: dict[int, ScannedPort]) -> None:
        """扫描结果交给 store 合并再刷新表格"""
        self.store.apply_scan(self.server.id, listeners)
        self._reload()
        self.status.setText(f"扫描完成 发现 {len(listeners)} 个监听端口 ｜ {self.status.text()}")

    def _on_failed(self, message: str) -> None:
        """扫描失败时给出原因"""
        self.status.setText(message)
        QMessageBox.warning(self, "扫描失败", message)

    def _on_finished(self) -> None:
        """扫描线程结束后复原按钮"""
        self.scan_button.setEnabled(True)
        self.worker = None

    @staticmethod
    def _cell(text: str) -> QTableWidgetItem:
        """只读单元格"""
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        return item