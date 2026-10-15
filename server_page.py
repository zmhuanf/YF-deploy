"""服务器管理页 列表展示与右键菜单"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from card_view import ROLE_BADGE, ROLE_ICON, ROLE_KEY, ROLE_SUBTITLE, ROLE_TITLE, make_card_view
from store import Server, Store

OS_LABELS = {"linux": "Linux", "windows": "Windows"}


def server_tooltip(server: Server) -> str:
    """悬停时补全卡片上放不下的信息"""
    auth = f"密钥 {server.key_file}" if server.key_file else "密码认证"
    os_label = OS_LABELS.get(server.os_type, server.os_type)
    return "\n".join((server.name, f"{server.username}@{server.address}", f"系统 {os_label}", f"认证 {auth}"))


class ServerDialog(QDialog):
    """新建/编辑服务器的表单"""

    def __init__(self, parent: QWidget | None = None, server: Server | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑服务器" if server else "新建服务器")
        self.setMinimumWidth(460)
        self._origin = server

        self.name_edit = QLineEdit(server.name if server else "")
        self.name_edit.setPlaceholderText("例如 生产机 A")
        self.host_edit = QLineEdit(server.host if server else "")
        self.host_edit.setPlaceholderText("IP 或域名")
        self.port_edit = QSpinBox()
        self.port_edit.setRange(1, 65535)
        self.port_edit.setValue(server.port if server else 22)
        self.user_edit = QLineEdit(server.username if server else "")
        self.password_edit = QLineEdit(server.password if server else "")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_password = QCheckBox("显示")
        self.show_password.toggled.connect(self._toggle_password)
        self.key_edit = QLineEdit(server.key_file if server else "")
        self.key_pass_edit = QLineEdit(server.key_passphrase if server else "")
        self.key_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.os_combo = QComboBox()
        self.os_combo.addItems([OS_LABELS["linux"], OS_LABELS["windows"]])
        if server and server.os_type == "windows":
            self.os_combo.setCurrentIndex(1)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.addRow("名称 *", self.name_edit)
        form.addRow("地址 *", self.host_edit)
        form.addRow("端口", self.port_edit)
        form.addRow("用户名 *", self.user_edit)
        form.addRow("密码", self._row(self.password_edit, self.show_password))
        form.addRow("密钥文件", self._row(self.key_edit, self._browse_button()))
        form.addRow("密钥口令", self.key_pass_edit)
        form.addRow("系统类型", self.os_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def accept(self) -> None:
        """名称 地址 用户名都填了才允许保存"""
        missing = [
            label
            for label, widget in (("名称", self.name_edit), ("地址", self.host_edit), ("用户名", self.user_edit))
            if not widget.text().strip()
        ]
        if missing:
            QMessageBox.warning(self, "信息不完整", "请填写：" + "、".join(missing))
            return
        super().accept()

    def result_server(self) -> Server:
        """按表单内容生成服务器对象 编辑时沿用原 id"""
        server = replace(self._origin) if self._origin else Server()
        server.name = self.name_edit.text().strip()
        server.host = self.host_edit.text().strip()
        server.port = self.port_edit.value()
        server.username = self.user_edit.text().strip()
        server.password = self.password_edit.text()
        server.key_file = self.key_edit.text().strip()
        server.key_passphrase = self.key_pass_edit.text()
        server.os_type = "windows" if self.os_combo.currentIndex() == 1 else "linux"
        return server

    def _toggle_password(self, visible: bool) -> None:
        """切换密码明文显示"""
        mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        self.password_edit.setEchoMode(mode)

    def _pick_key(self) -> None:
        """选择私钥文件"""
        path, _ = QFileDialog.getOpenFileName(self, "选择私钥文件")
        if path:
            self.key_edit.setText(path)

    def _browse_button(self) -> QPushButton:
        """密钥文件的选择按钮"""
        button = QPushButton("浏览…")
        button.clicked.connect(self._pick_key)
        return button

    @staticmethod
    def _row(edit: QLineEdit, *extra: QWidget) -> QWidget:
        """把输入框和附带的控件摆成一行"""
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit, 1)
        for widget in extra:
            layout.addWidget(widget)
        return holder


class ServerPage(QWidget):
    """服务器列表页 右键新建 复制连接指令 编辑 删除"""

    changed = Signal()

    def __init__(self, store: Store, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store

        hint = QLabel("右键空白处新建服务器；右键某台服务器可复制 SSH / pi 启动指令")
        hint.setStyleSheet("color:#6b7280;padding:8px 12px;")

        self.view = make_card_view()
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.itemDoubleClicked.connect(lambda _: self._edit_current())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(hint)
        layout.addWidget(self.view, 1)

        self.refresh()

    def refresh(self) -> None:
        """按 store 里的服务器重建卡片"""
        self.view.clear()
        for server in self.store.servers:
            item = QListWidgetItem()
            item.setData(ROLE_ICON, "server")
            item.setData(ROLE_TITLE, server.name)
            item.setData(ROLE_SUBTITLE, f"{server.username}@{server.address}")
            item.setData(ROLE_BADGE, OS_LABELS.get(server.os_type, server.os_type))
            item.setData(ROLE_KEY, server.id)
            item.setToolTip(server_tooltip(server))
            self.view.addItem(item)

    def _context_menu(self, pos) -> None:
        """按点击位置弹出菜单 命中卡片与命中空白处菜单不同"""
        index = self.view.indexAt(pos)
        server = self.store.server(index.data(ROLE_KEY))
        menu = QMenu(self)
        if server is None:
            self.view.clearSelection()
            menu.addAction("新建服务器").setData("new")
        else:
            self.view.setCurrentIndex(index)
            menu.addAction("复制 SSH 连接指令").setData("ssh")
            pi_action = menu.addAction("复制 pi 启动指令")
            pi_action.setData("pi")
            pi_action.setEnabled(bool(server.password))
            if not server.password:
                pi_action.setToolTip("该服务器未配置密码，无法生成 pi 启动指令")
            menu.addSeparator()
            menu.addAction("编辑").setData("edit")
            menu.addAction("删除").setData("delete")

        action = menu.exec(self.view.viewport().mapToGlobal(pos))
        if action is None:
            return
        match action.data():
            case "new":
                self._create()
            case "ssh":
                self._copy(server.ssh_command, "SSH 连接指令已复制到剪贴板")
            case "pi":
                self._copy(server.pi_command, "pi 启动指令已复制到剪贴板")
            case "edit":
                self._edit(server)
            case "delete":
                self._delete(server)

    def _create(self) -> None:
        """新建服务器"""
        dialog = ServerDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.store.upsert_server(dialog.result_server())
        self._commit()

    def _edit(self, server: Server) -> None:
        """编辑服务器"""
        dialog = ServerDialog(self, server)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.store.upsert_server(dialog.result_server())
        self._commit()

    def _edit_current(self) -> None:
        """双击卡片进入编辑"""
        server = self.store.server(self.view.currentIndex().data(ROLE_KEY))
        if server is not None:
            self._edit(server)

    def _delete(self, server: Server) -> None:
        """二次确认后删除 引用它的项目保留但需要重新选服务器"""
        answer = QMessageBox.question(
            self,
            "删除确认",
            f"确定删除服务器「{server.name}」吗？\n引用它的项目不会被删除，但需要重新选择服务器。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.store.remove_server(server.id)
        self._commit()

    def _copy(self, text: str, tip: str) -> None:
        """写入系统剪贴板并在状态栏提示"""
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        self.window().statusBar().showMessage(tip, 3000)

    def _commit(self) -> None:
        """落盘后刷新界面并通知其他页面"""
        self.refresh()
        self.changed.emit()