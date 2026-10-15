"""项目管理页 本地目录上传与远程命令执行"""

from __future__ import annotations

import shlex
import threading
import time
from dataclasses import replace
from pathlib import Path

import paramiko
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
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
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import ssh
from card_view import ROLE_EXTRA, ROLE_ICON, ROLE_KEY, ROLE_SUBTITLE, ROLE_TITLE, make_card_view
from store import Project, Server, Store


def project_tooltip(project: Project, server: Server | None) -> str:
    """悬停时补全卡片上放不下的信息"""
    rows = [
        project.name,
        f"服务器 {server.name}（{server.username}@{server.address}）" if server else "服务器 已删除",
        f"本地 {project.local_path}",
        f"远程 {project.remote_path}",
    ]
    if project.command:
        rows.append(f"命令 {project.command}")
    return "\n".join(rows)


class DeployWorker(QThread):
    """后台线程完成连接 上传 执行命令 日志通过信号回主线程"""

    log = Signal(str)
    done = Signal(bool, str)

    def __init__(self, project: Project, server: Server, execute: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project = project
        self.server = server
        self.execute = execute
        self._stop = threading.Event()
        self._client: paramiko.SSHClient | None = None

    def stop(self) -> None:
        """请求中断 直接关闭连接让上传与读取立即抛错退出"""
        self._stop.set()
        if self._client:
            self._client.close()

    def run(self) -> None:
        """线程主体 先上传再按需执行"""
        start = time.monotonic()
        try:
            self._client = ssh.connect(self.server)
            sftp = self._client.open_sftp()
            self._note(f"已连接 {self.server.username}@{self.server.address}")
            remote = ssh.expand_path(sftp, self.project.remote_path)
            count = ssh.upload_tree(
                sftp,
                Path(self.project.local_path),
                remote,
                self.server.os_type,
                lambda path, target: self.log.emit(f"↑ {path.name} → {target}\n"),
            )
            sftp.close()
            self._note(f"上传完成 共 {count} 个文件 目标目录 {remote}")
            if self._stop.is_set():
                self.done.emit(False, "任务已停止")
                return
            if not self.execute:
                self.done.emit(True, f"仅上传完成 共 {count} 个文件 耗时 {time.monotonic() - start:.1f}s")
                return
            self._run_remote(remote, count, start)
        except Exception as error:
            self.done.emit(False, "任务已停止" if self._stop.is_set() else f"执行失败：{error}")
        finally:
            if self._client:
                self._client.close()
                self._client = None

    def _run_remote(self, remote: str, count: int, start: float) -> None:
        """在远程目标目录下执行项目配置的命令"""
        command = self.project.command.strip()
        if not command:
            self.done.emit(True, f"上传完成 共 {count} 个文件；项目未配置执行命令")
            return
        self.log.emit(f"\n$ {command}\n")
        code = ssh.run_command(self._client, f"cd {shlex.quote(remote)} && {command}", self.log.emit, self._stop.is_set)
        self.log.emit("\n")
        if code == -1 and self._stop.is_set():
            self.done.emit(False, "任务已停止")
            return
        self.done.emit(code == 0, f"命令结束 退出码 {code} 耗时 {time.monotonic() - start:.1f}s")

    def _note(self, text: str) -> None:
        """输出一条带时间戳的阶段日志"""
        self.log.emit(f"[{time.strftime('%H:%M:%S')}] {text}\n")


class ProjectDialog(QDialog):
    """新建/编辑项目的表单"""

    def __init__(self, parent: QWidget | None = None, project: Project | None = None, servers: list[Server] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑项目" if project else "新建项目")
        self.setMinimumWidth(620)
        self._origin = project

        self.name_edit = QLineEdit(project.name if project else "")
        self.name_edit.setPlaceholderText("例如 后端服务")
        self.server_combo = QComboBox()
        for server in servers or []:
            self.server_combo.addItem(f"{server.name}　{server.username}@{server.address}", server.id)
        if project:
            index = self.server_combo.findData(project.server_id)
            if index < 0:
                # 原服务器已被删除时插占位项 避免静默绑定到列表里的其它服务器
                self.server_combo.insertItem(0, "（原服务器已删除 请重新选择）", "")
                index = 0
            self.server_combo.setCurrentIndex(index)
        self.local_edit = QLineEdit(project.local_path if project else "")
        self.local_edit.setPlaceholderText("要上传的本机目录")
        self.remote_edit = QLineEdit(project.remote_path if project else "")
        self.remote_edit.setPlaceholderText("远程目标目录 不存在会自动创建")
        self.command_edit = QLineEdit(project.command if project else "")
        self.command_edit.setPlaceholderText("在远程目录下执行的命令 例如 sh start.sh")

        browse = QPushButton("浏览…")
        browse.clicked.connect(self._pick_folder)
        local_row = QWidget()
        local_layout = QHBoxLayout(local_row)
        local_layout.setContentsMargins(0, 0, 0, 0)
        local_layout.addWidget(self.local_edit, 1)
        local_layout.addWidget(browse)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.addRow("项目名 *", self.name_edit)
        form.addRow("服务器 *", self.server_combo)
        form.addRow("本地路径 *", local_row)
        form.addRow("远程路径 *", self.remote_edit)
        form.addRow("执行命令", self.command_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def accept(self) -> None:
        """名称 服务器 路径都合法才允许保存"""
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "信息不完整", "请填写项目名")
            return
        if not self.server_combo.currentData():
            QMessageBox.warning(self, "信息不完整", "请选择服务器")
            return
        if not Path(self.local_edit.text().strip()).is_dir():
            QMessageBox.warning(self, "本地路径无效", "本机目录不存在，请重新选择")
            return
        if not self.remote_edit.text().strip():
            QMessageBox.warning(self, "信息不完整", "请填写远程路径")
            return
        super().accept()

    def result_project(self) -> Project:
        """按表单内容生成项目对象 编辑时沿用原 id"""
        project = replace(self._origin) if self._origin else Project()
        project.name = self.name_edit.text().strip()
        project.server_id = self.server_combo.currentData()
        project.local_path = self.local_edit.text().strip()
        project.remote_path = self.remote_edit.text().strip()
        project.command = self.command_edit.text().strip()
        return project

    def _pick_folder(self) -> None:
        """选择要上传的本机目录"""
        path = QFileDialog.getExistingDirectory(self, "选择本地目录", self.local_edit.text())
        if path:
            self.local_edit.setText(path)


class ProjectPage(QWidget):
    """项目列表页 右键上传并执行或仅上传 日志显示在下方"""

    def __init__(self, store: Store, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.worker: DeployWorker | None = None

        hint = QLabel("右键空白处新建项目；右键某个项目可上传并执行或仅上传")
        hint.setStyleSheet("color:#6b7280;padding:8px 12px;")

        self.view = make_card_view()
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.itemDoubleClicked.connect(lambda _: self._edit_current())

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("log")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)

        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop)
        clear_button = QPushButton("清空日志")
        clear_button.clicked.connect(self.log_view.clear)
        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("执行日志"))
        log_header.addStretch(1)
        log_header.addWidget(self.stop_button)
        log_header.addWidget(clear_button)

        log_panel = QWidget()
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(8, 0, 8, 8)
        log_layout.addLayout(log_header)
        log_layout.addWidget(self.log_view)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.view)
        splitter.addWidget(log_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(hint)
        layout.addWidget(splitter, 1)

        self.refresh()

    def refresh(self) -> None:
        """按 store 里的项目重建卡片"""
        self.view.clear()
        for project in self.store.projects:
            server = self.store.server(project.server_id)
            item = QListWidgetItem()
            item.setData(ROLE_ICON, "project")
            item.setData(ROLE_TITLE, project.name)
            item.setData(ROLE_SUBTITLE, server.name if server else "（服务器已删除）")
            item.setData(ROLE_EXTRA, project.remote_path)
            item.setData(ROLE_KEY, project.id)
            item.setToolTip(project_tooltip(project, server))
            self.view.addItem(item)

    def _context_menu(self, pos) -> None:
        """按点击位置弹出菜单 命中卡片与命中空白处菜单不同"""
        index = self.view.indexAt(pos)
        project = self.store.project(index.data(ROLE_KEY))
        menu = QMenu(self)
        if project is None:
            self.view.clearSelection()
            menu.addAction("新建项目").setData("new")
        else:
            self.view.setCurrentIndex(index)
            menu.addAction("上传并执行").setData("deploy")
            menu.addAction("仅上传").setData("upload")
            menu.addSeparator()
            menu.addAction("编辑").setData("edit")
            menu.addAction("删除").setData("delete")
        menu.addSeparator()
        menu.addAction("清空日志").setData("clear")

        action = menu.exec(self.view.viewport().mapToGlobal(pos))
        if action is None:
            return
        match action.data():
            case "new":
                self._create()
            case "deploy":
                self._deploy(project, execute=True)
            case "upload":
                self._deploy(project, execute=False)
            case "edit":
                self._edit(project)
            case "delete":
                self._delete(project)
            case "clear":
                self.log_view.clear()

    def _create(self) -> None:
        """新建项目 没有可用服务器时先提示"""
        if not self.store.servers:
            QMessageBox.warning(self, "缺少服务器", "请先在「服务器」页新建一台服务器")
            return
        dialog = ProjectDialog(self, servers=self.store.servers)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.store.upsert_project(dialog.result_project())
        self.refresh()

    def _edit(self, project: Project) -> None:
        """编辑项目"""
        if not self.store.servers:
            QMessageBox.warning(self, "缺少服务器", "请先在「服务器」页新建一台服务器")
            return
        dialog = ProjectDialog(self, project, self.store.servers)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.store.upsert_project(dialog.result_project())
        self.refresh()

    def _edit_current(self) -> None:
        """双击卡片进入编辑"""
        project = self.store.project(self.view.currentIndex().data(ROLE_KEY))
        if project is not None:
            self._edit(project)

    def _delete(self, project: Project) -> None:
        """二次确认后删除项目"""
        answer = QMessageBox.question(
            self,
            "删除确认",
            f"确定删除项目「{project.name}」吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.store.remove_project(project.id)
        self.refresh()

    def _deploy(self, project: Project, execute: bool) -> None:
        """启动后台任务上传本地目录 并按需执行命令"""
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "任务进行中", "已有任务在运行，请等它结束或先点「停止」")
            return
        server = self.store.server(project.server_id)
        if server is None:
            QMessageBox.warning(self, "缺少服务器", "该项目关联的服务器已不存在，请编辑项目重新选择")
            return
        if not Path(project.local_path).is_dir():
            QMessageBox.warning(self, "本地路径无效", f"本机目录不存在：{project.local_path}")
            return
        if not project.remote_path.strip():
            QMessageBox.warning(self, "远程路径为空", "请先编辑项目填写远程目标目录")
            return

        self._append(f"\n{'=' * 56}\n{project.name} → {server.name}｜{'上传并执行' if execute else '仅上传'}\n")
        worker = DeployWorker(project, server, execute, self)
        worker.log.connect(self._append)
        worker.done.connect(self._on_done)
        worker.finished.connect(worker.deleteLater)
        self.worker = worker
        self.stop_button.setEnabled(True)
        worker.start()

    def _stop(self) -> None:
        """中断当前任务"""
        if self.worker and self.worker.isRunning():
            self._append("\n正在停止…\n")
            self.worker.stop()

    def _on_done(self, ok: bool, message: str) -> None:
        """任务收尾 输出结论并复原按钮"""
        self._append(f"{'✅' if ok else '❌'} {message}\n")
        if self.sender() is self.worker:
            self.stop_button.setEnabled(False)
            self.worker = None

    def _append(self, text: str) -> None:
        """追加日志并滚到底部 不自动补换行以还原命令的原始输出"""
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)
        self.log_view.insertPlainText(text)