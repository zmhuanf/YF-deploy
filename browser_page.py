"""远端文件管理器 双击服务器后进入"""

from __future__ import annotations

import posixpath
import threading
import time
from pathlib import Path

import paramiko
from PySide6.QtCore import QRect, QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

import icons
import ssh
from ssh import RemoteEntry
from store import Server

ENTRY_ROLE = Qt.ItemDataRole.UserRole + 1
GRID_SIZE = QSize(128, 104)
ICON_SIZE = 48
SORT_FIELDS = (("名称", "name"), ("大小", "size"), ("修改时间", "mtime"), ("类型", "type"))

COLOR_FOLDER = "#eab308"
COLOR_FILE = "#64748b"
COLOR_SELECT = "#1e90ff"

# 探测文件类型时读取的开头字节数
PROBE_BYTES = 4096

# 扩展名能直接判定的走白名单 其余读开头字节探测
TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".log", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf",
    ".env", ".properties", ".sh", ".bash", ".zsh", ".bat", ".cmd", ".ps1", ".sql", ".csv",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".vue", ".java", ".go", ".rs", ".rb", ".php", ".lua",
    ".c", ".h", ".cpp", ".hpp", ".cs", ".swift", ".kt", ".scala", ".pl",
    ".xml", ".html", ".htm", ".css", ".scss", ".less", ".svg",
}

BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff", ".psd",
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".dat", ".db", ".sqlite", ".pyc", ".pyo",
    ".class", ".o", ".a", ".lib", ".obj", ".pdb", ".whl", ".deb", ".rpm", ".msi",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".rtf",
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".mp4", ".avi", ".mkv", ".mov", ".wmv",
    ".ttf", ".otf", ".woff", ".woff2", ".eot", ".iso", ".img", ".apk", ".ipa",
}


def text_kind(name: str) -> str:
    """按扩展名给出 text / binary / unknown"""
    suffix = Path(name).suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return "text"
    if suffix in BINARY_SUFFIXES:
        return "binary"
    return "unknown"


def format_size(size: int) -> str:
    """把字节数转成带单位的可读文本"""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.0f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def entry_tooltip(entry: RemoteEntry) -> str:
    """悬停时显示图标上放不下的文件详情"""
    rows = [entry.name, "文件夹" if entry.is_dir else format_size(entry.size)]
    if entry.mtime:
        rows.append(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry.mtime)))
    rows.append(entry.path)
    return "\n".join(rows)


# Windows 远端文件名里不允许出现的字符
WINDOWS_BAD_CHARS = '\\:*?"<>|'


def invalid_name_reason(name: str, os_type: str) -> str:
    """条目名不合法时给出原因 合法返回空串"""
    if not name:
        return "名称不能为空"
    if name in (".", ".."):
        return "名称不能是 . 或 .."
    bad = ("/" + WINDOWS_BAD_CHARS) if os_type == "windows" else "/"
    hit = next((char for char in bad if char in name), "")
    return f"名称不能包含 {hit}" if hit else ""


class FileDelegate(QStyledItemDelegate):
    """把文件项画成图标在上 名称在下的格子"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icons: dict[tuple, QPixmap] = {}

    def sizeHint(self, option, index) -> QSize:
        """格子尺寸由视图的 gridSize 统一决定"""
        return option.widget.gridSize() if option.widget else GRID_SIZE

    def paint(self, painter: QPainter, option, index) -> None:
        """绘制选中底色 类型图标与文件名"""
        entry: RemoteEntry | None = index.data(ENTRY_ROLE)
        if entry is None:
            return
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = option.rect

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#dbeafe" if selected else "#eef2f7"))
            painter.drawRoundedRect(rect.adjusted(4, 6, -4, -6), 8, 8)

        icon_rect = QRect(rect.center().x() - ICON_SIZE // 2, rect.top() + 16, ICON_SIZE, ICON_SIZE)
        painter.drawPixmap(icon_rect, self._icon(entry, selected, option.widget))

        font = QFont(option.font)
        font.setPixelSize(12)
        metrics = QFontMetrics(font)
        painter.setFont(font)
        painter.setPen(QColor("#1d4ed8" if selected else "#1f2937"))
        name_rect = QRect(rect.left() + 6, icon_rect.bottom() + 8, rect.width() - 12, 18)
        painter.drawText(name_rect, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter), metrics.elidedText(entry.name, Qt.TextElideMode.ElideMiddle, name_rect.width()))
        painter.restore()

    def _icon(self, entry: RemoteEntry, selected: bool, widget: QWidget | None) -> QPixmap:
        """按类型与选中状态取图标 结果缓存避免重复解析 SVG"""
        name = icons.icon_name_for(entry.name, entry.is_dir)
        color = COLOR_SELECT if selected else COLOR_FOLDER if entry.is_dir else COLOR_FILE
        ratio = widget.devicePixelRatioF() if widget else 1.0
        cached = self._icons.get((name, color, ratio))
        if cached is None:
            cached = icons.render(name, color, ICON_SIZE, ratio)
            self._icons[(name, color, ratio)] = cached
        return cached


class FileView(QListWidget):
    """文件图标视图 只负责接收资源管理器拖入的本地文件"""

    files_dropped = Signal(list)

    def dragEnterEvent(self, event) -> None:
        """只接受来自资源管理器的本地文件"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        """把拖入的本地路径交给页面处理"""
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()


class TextViewDialog(QDialog):
    """只读展示远端文本内容 支持刷新与跳到末尾"""

    def __init__(self, parent: QWidget | None, client: paramiko.SSHClient, entry: RemoteEntry, kind: str, text: str, truncated: bool) -> None:
        super().__init__(parent)
        self._client = client
        self._entry = entry
        self._kind = kind
        self._worker: TextWorker | None = None
        self._truncated = truncated
        self._origin = "开头"

        self.setWindowTitle(f"文本预览 - {entry.name}")
        self.resize(920, 660)

        header = QLabel(f"{entry.path}　{format_size(entry.size)}")
        header.setStyleSheet("color:#6b7280;padding:2px 0 8px 2px;")

        self.viewer = QPlainTextEdit()
        self.viewer.setObjectName("viewer")
        self.viewer.setReadOnly(True)
        self.viewer.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        self.footer = QLabel()
        self.footer.setStyleSheet("color:#6b7280;padding:6px 2px 0 2px;")

        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(lambda: self._load(False))
        self.tail_button = QPushButton(f"最后 {ssh.TEXT_LINE_LIMIT} 行")
        self.tail_button.clicked.connect(lambda: self._load(True))
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.accept)

        bottom = QHBoxLayout()
        bottom.addWidget(self.footer, 1)
        bottom.addWidget(self.refresh_button)
        bottom.addWidget(self.tail_button)
        bottom.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(header)
        layout.addWidget(self.viewer, 1)
        layout.addLayout(bottom)

        self._show(text, truncated, self._origin)

    def closeEvent(self, event) -> None:
        """关闭前停掉读取线程 避免线程随对象一起被销毁"""
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)
        self._worker = None
        super().closeEvent(event)

    def _load(self, tail: bool) -> None:
        """重新从服务器读取 尾部模式取文件末尾"""
        if self._worker is not None and self._worker.isRunning():
            return
        self.refresh_button.setEnabled(False)
        self.tail_button.setEnabled(False)
        self.footer.setText("正在读取…")
        worker = TextWorker(self._client, self._entry, self._kind, tail, self)
        worker.loaded.connect(self._show)
        worker.rejected.connect(self._reject)
        worker.failed.connect(self._fail)
        worker.finished.connect(lambda: self._release(worker))
        self._worker = worker
        worker.start()

    def _show(self, text: str, truncated: bool, origin: str) -> None:
        """把内容放进查看器并按来源把光标落到开头或末尾"""
        self.viewer.setPlainText(text)
        self._truncated = truncated
        self._origin = origin
        self.viewer.moveCursor(QTextCursor.MoveOperation.End if origin == "末尾" else QTextCursor.MoveOperation.Start)
        self._update_note()

    def _update_note(self) -> None:
        """更新底部的行数与位置提示"""
        note = f"共 {len(self.viewer.toPlainText().splitlines())} 行"
        if self._origin == "末尾":
            note += "　（显示文件末尾，前面还有内容）" if self._truncated else "　（显示文件末尾）"
        elif self._truncated:
            note += "　（文件较大 只显示开头部分）"
        self.footer.setText(note)

    def _release(self, worker: TextWorker) -> None:
        """读取结束后解除引用并恢复按钮"""
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()
        self.refresh_button.setEnabled(True)
        self.tail_button.setEnabled(True)

    def _reject(self, message: str) -> None:
        """内容判定为二进制"""
        self.footer.setText(message)

    def _fail(self, message: str) -> None:
        """读取失败"""
        self.footer.setText(f"读取失败：{message}")
        QMessageBox.warning(self, "读取失败", message)


class ListWorker(QThread):
    """后台建立连接并读取远端目录"""

    loaded = Signal(object, str, list)
    failed = Signal(object, str)

    def __init__(self, server: Server, path: str, client: paramiko.SSHClient | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.server = server
        self.path = path
        self.client = client
        self._stop = threading.Event()

    def stop(self) -> None:
        """请求中断 连接刚建立时会立刻关掉"""
        self._stop.set()

    def run(self) -> None:
        """连接后列出目标目录"""
        try:
            if self.client is None:
                self.client = ssh.connect(self.server)
            if self._stop.is_set():
                self.client.close()
                return
            with self.client.open_sftp() as sftp:
                target = ssh.expand_path(sftp, self.path) if self.path else sftp.normalize(".")
                entries = ssh.list_dir(sftp, target)
            self.loaded.emit(self.client, target, entries)
        except Exception as error:
            self.failed.emit(self.client, str(error))


class TextWorker(QThread):
    """后台读取远端文件内容用于文本预览"""

    loaded = Signal(str, bool, str)
    rejected = Signal(str)
    failed = Signal(str)

    def __init__(self, client: paramiko.SSHClient, entry: RemoteEntry, kind: str, tail: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.client = client
        self.entry = entry
        self.kind = kind
        self.tail = tail
        self._stop = threading.Event()

    def stop(self) -> None:
        """请求中断 结果由会话号拦掉"""
        self._stop.set()

    def run(self) -> None:
        """读取文件 开头模式对未知类型先做字节探测"""
        try:
            with self.client.open_sftp() as sftp:
                if self._stop.is_set():
                    return
                if self.tail:
                    text, more = ssh.read_tail(sftp, self.entry.path)
                else:
                    if self.kind == "unknown" and not ssh.looks_like_text(ssh.read_head(sftp, self.entry.path, PROBE_BYTES)):
                        self.rejected.emit(f"{self.entry.name} 不是文本文件，无法预览")
                        return
                    text, more = ssh.read_text(sftp, self.entry.path)
            if not self._stop.is_set():
                self.loaded.emit(text, more, "末尾" if self.tail else "开头")
        except Exception as error:
            if not self._stop.is_set():
                self.failed.emit(str(error))


class TransferWorker(QThread):
    """上传与下载的公共骨架 负责跨文件累加进度与中断"""

    progress = Signal(int, int, str)
    done = Signal(bool, str)

    def __init__(self, client: paramiko.SSHClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.client = client
        self._stop = threading.Event()
        self._transferred = 0
        self._total = 0
        self._current = ""
        self._last = 0
        self._failures: list[str] = []

    def stop(self) -> None:
        """请求中断 已经传完的文件保持原样"""
        self._stop.set()

    def cancelled(self) -> bool:
        """是否已被要求中断"""
        return self._stop.is_set()

    def on_bytes(self, name: str, done: int, total: int) -> None:
        """把单文件进度换算成总进度 换文件时重置基准"""
        if name != self._current:
            self._current = name
            self._last = 0
        self._transferred += done - self._last
        self._last = done
        self.progress.emit(self._transferred, self._total, name)

    def summary(self, text: str) -> str:
        """在结果描述后面附加失败清单"""
        if self._failures:
            text += f"  {len(self._failures)} 项失败：" + "；".join(self._failures[:3])
        return text


class FileCmdWorker(QThread):
    """在远端逐条执行文件命令 每条完成推一格进度"""

    progress = Signal(int, int, str)
    done = Signal(bool, str)

    def __init__(self, client: paramiko.SSHClient, jobs: list[tuple[str, str]], title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.client = client
        self.jobs = jobs
        self.title = title
        self._stop = threading.Event()
        self._failures: list[str] = []

    def stop(self) -> None:
        """请求中断 正在跑的那条命令会一并被抢断"""
        self._stop.set()

    def run(self) -> None:
        """逐条执行命令 单项失败不影响其余"""
        total = len(self.jobs)
        for index, (name, command) in enumerate(self.jobs):
            self.progress.emit(index, total, name)
            output: list[str] = []
            code = ssh.run_command(self.client, command, output.append, self._stop.is_set)
            if code == -1:
                self.done.emit(False, f"{self.title}已取消")
                return
            if code != 0:
                detail = " ".join("".join(output).split())[:80] or f"退出码 {code}"
                self._failures.append(f"{name}（{detail}）")
        self.progress.emit(total, total, self.title)
        message = f"{self.title}完成 共 {total} 项"
        if self._failures:
            message += f"  {len(self._failures)} 项失败：" + "；".join(self._failures[:3])
        self.done.emit(not self._failures, message)


class DownloadWorker(TransferWorker):
    """把远端条目下载到本地目录"""

    def __init__(self, client: paramiko.SSHClient, entries: list[RemoteEntry], target: Path, parent: QWidget | None = None) -> None:
        super().__init__(client, parent)
        self.entries = entries
        self.target = target

    def run(self) -> None:
        """先统计总字节再逐个下载"""
        try:
            self.progress.emit(0, 0, "正在统计…")
            with self.client.open_sftp() as sftp:
                self._total = sum(self._measure(sftp, entry) for entry in self.entries)
                for entry in self.entries:
                    if self.cancelled():
                        self.done.emit(False, "已取消")
                        return
                    self._fetch(sftp, entry)
            self.done.emit(not self._failures, self.summary(f"下载完成 共 {len(self.entries)} 项 → {self.target}"))
        except Exception as error:
            self.done.emit(False, "已取消" if self.cancelled() else f"下载失败：{error}")

    def _measure(self, sftp: paramiko.SFTPClient, entry: RemoteEntry) -> int:
        """统计条目需要的字节数"""
        return entry.size if not entry.is_dir else ssh.scan_tree(sftp, entry.path)[1]

    def _fetch(self, sftp: paramiko.SFTPClient, entry: RemoteEntry) -> None:
        """下载单个条目 单项失败不影响其余"""
        self.progress.emit(self._transferred, self._total, entry.name)
        local = self.target / entry.name
        try:
            if entry.is_dir:
                ssh.download_tree(sftp, entry.path, local, self.on_bytes)
            else:
                ssh.download_file(sftp, entry.path, local, self.on_bytes)
        except Exception as error:
            self._failures.append(f"{entry.name}（{error}）")


class UploadWorker(TransferWorker):
    """把本地文件或文件夹上传到远端目录"""

    def __init__(self, client: paramiko.SSHClient, locals: list[Path], remote_dir: str, os_type: str, parent: QWidget | None = None) -> None:
        super().__init__(client, parent)
        self.locals = locals
        self.remote_dir = remote_dir
        self.os_type = os_type

    def run(self) -> None:
        """先统计本地总字节再逐个上传"""
        try:
            self.progress.emit(0, 0, "正在统计…")
            self._total = sum(self._measure(path) for path in self.locals)
            uploaded = 0
            with self.client.open_sftp() as sftp:
                for path in self.locals:
                    if self.cancelled():
                        self.done.emit(False, "已取消")
                        return
                    uploaded += ssh.upload_entry(sftp, path, self.remote_dir, self.os_type, None, self.on_bytes)
            self.done.emit(True, self.summary(f"上传完成 共 {uploaded} 个文件"))
        except Exception as error:
            self.done.emit(False, "已取消" if self.cancelled() else f"上传失败：{error}")

    @staticmethod
    def _measure(path: Path) -> int:
        """统计本地文件或文件夹的总字节数"""
        if path.is_file():
            return path.stat().st_size
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


class BrowserPage(QWidget):
    """远端文件管理器 支持浏览 上传 下载"""

    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.server: Server | None = None
        self.remote_dir = ""
        self._client: paramiko.SSHClient | None = None
        self._entries: list[RemoteEntry] = []
        self._descending = False
        self._session = 0
        self._list_worker: ListWorker | None = None
        self._transfer: TransferWorker | None = None
        self._reader: TextWorker | None = None
        self._cmd: FileCmdWorker | None = None
        self._clip_entries: list[RemoteEntry] = []
        self._clip_cut = False
        self._last_local_dir = ""
        self._build_ui()

    def _build_ui(self) -> None:
        """搭建工具栏 状态行与图标视图"""
        self.back_button = QPushButton("← 返回")
        self.back_button.clicked.connect(self.closed.emit)
        self.up_button = QPushButton("↑ 上一级")
        self.up_button.clicked.connect(self._go_up)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self._reload)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("远端路径 回车跳转")
        self.path_edit.returnPressed.connect(self._go_typed_path)
        self.sort_combo = QComboBox()
        for label, field in SORT_FIELDS:
            self.sort_combo.addItem(label, field)
        self.sort_combo.currentIndexChanged.connect(lambda _: self._render())
        self.order_button = QPushButton()
        self.order_button.setCheckable(True)
        self.order_button.toggled.connect(self._toggle_order)
        self._update_order_text()

        self.status_label = QLabel("未连接")
        self.status_label.setStyleSheet("color:#6b7280;padding:0 12px 6px 12px;")

        self.view = FileView()
        self.view.setObjectName("cards")
        self.view.setViewMode(QListWidget.ViewMode.IconMode)
        self.view.setFlow(QListWidget.Flow.LeftToRight)
        self.view.setWrapping(True)
        self.view.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.view.setMovement(QListWidget.Movement.Static)
        self.view.setGridSize(GRID_SIZE)
        self.view.setUniformItemSizes(True)
        self.view.setWordWrap(False)
        self.view.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.view.setEditTriggers(QListWidget.EditTrigger.NoEditTriggers)
        self.view.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setFrameShape(QListWidget.Shape.NoFrame)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.setItemDelegate(FileDelegate(self.view))
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.itemDoubleClicked.connect(self._activate)
        self.view.files_dropped.connect(self._start_upload)
        self.view.setDragDropMode(QListWidget.DragDropMode.DropOnly)
        self.view.setDefaultDropAction(Qt.DropAction.CopyAction)
        # 拖放事件先投给视口 视口不接受的话资源管理器拖入会被直接丢掉
        self.view.viewport().setAcceptDrops(True)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(10, 8, 10, 4)
        toolbar.setSpacing(6)
        toolbar.addWidget(self.back_button)
        toolbar.addWidget(self.up_button)
        toolbar.addWidget(self.refresh_button)
        toolbar.addWidget(self.path_edit, 1)
        toolbar.addWidget(QLabel("排序"))
        toolbar.addWidget(self.sort_combo)
        toolbar.addWidget(self.order_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(toolbar)
        layout.addWidget(self.status_label)
        layout.addWidget(self.view, 1)

    def open_server(self, server: Server) -> None:
        """进入某台服务器的文件管理 从 home 目录开始"""
        self.close_session()
        self.server = server
        self._set_status(f"正在连接 {server.username}@{server.address} …")
        self._load("~")

    def close_session(self) -> None:
        """断开连接并清空界面 旧会话的回调会被丢弃"""
        self._session += 1
        for worker in (self._list_worker, self._transfer, self._reader, self._cmd):
            if worker is not None and worker.isRunning():
                worker.stop()
        if self._client is not None:
            self._client.close()
            self._client = None
        self.server = None
        self.remote_dir = ""
        self._entries.clear()
        self._clip_entries.clear()
        self._clip_cut = False
        self.view.clear()
        self.path_edit.clear()
        self._set_toolbar_enabled(True)
        self._set_status("未连接")

    def _load(self, path: str) -> None:
        """后台读取指定目录 空路径表示 home"""
        if self.server is None or (self._list_worker is not None and self._list_worker.isRunning()):
            return
        token = self._session
        worker = ListWorker(self.server, path, self._client, self)
        worker.loaded.connect(lambda client, target, entries: self._on_loaded(token, client, target, entries))
        worker.failed.connect(lambda client, message: self._on_failed(token, client, message))
        worker.finished.connect(lambda: self._release_list_worker(worker))
        self._list_worker = worker
        self._set_toolbar_enabled(False)
        self._set_status("正在读取目录 …")
        worker.start()

    def _on_loaded(self, token: int, client: paramiko.SSHClient, target: str, entries: list[RemoteEntry]) -> None:
        """目录读取成功 更新连接与视图"""
        if token != self._session:
            # 会话已切走 关掉这次多建出来的连接
            client.close()
            return
        self._client = client
        self.remote_dir = target
        self._entries = entries
        self.path_edit.setText(target)
        self._set_toolbar_enabled(True)
        self._render()
        self._set_status(f"{self.server.name}｜{target}｜共 {len(entries)} 项")

    def _on_failed(self, token: int, client: paramiko.SSHClient | None, message: str) -> None:
        """目录读取失败 首次连接失败时自动退回列表"""
        if token != self._session:
            if client is not None:
                client.close()
            return
        self._client = client
        self._set_toolbar_enabled(True)
        self._set_status(f"读取失败：{message}")
        QMessageBox.warning(self, "读取失败", message)
        if self._client is None:
            self.closed.emit()

    def _release_list_worker(self, worker: ListWorker) -> None:
        """读取结束后解除引用再释放 否则后面访问 isRunning 会打到已删除的对象上"""
        if self._list_worker is worker:
            self._list_worker = None
        worker.deleteLater()

    def _render(self) -> None:
        """按当前排序把条目填进视图"""
        self.view.clear()
        for entry in self._sorted_entries():
            item = QListWidgetItem()
            item.setData(ENTRY_ROLE, entry)
            item.setToolTip(entry_tooltip(entry))
            self.view.addItem(item)

    def _sorted_entries(self) -> list[RemoteEntry]:
        """目录永远排在最前 其余按所选字段排序"""
        key = self._sort_key()
        folders = sorted((entry for entry in self._entries if entry.is_dir), key=key, reverse=self._descending)
        files = sorted((entry for entry in self._entries if not entry.is_dir), key=key, reverse=self._descending)
        return folders + files

    def _sort_key(self):
        """当前排序字段对应的取值函数"""
        match self.sort_combo.currentData():
            case "size":
                return lambda entry: entry.size
            case "mtime":
                return lambda entry: entry.mtime
            case "type":
                return lambda entry: Path(entry.name).suffix.lower()
            case _:
                return lambda entry: entry.name.lower()

    def _toggle_order(self, descending: bool) -> None:
        """切换升序降序"""
        self._descending = descending
        self._update_order_text()
        self._render()

    def _update_order_text(self) -> None:
        """按当前方向更新按钮文字"""
        self.order_button.setText("↓ 降序" if self._descending else "↑ 升序")

    def _go_up(self) -> None:
        """进入上一级目录"""
        parent = posixpath.dirname(self.remote_dir.rstrip("/"))
        self._load(parent or "/")

    def _go_typed_path(self) -> None:
        """按路径栏输入跳转"""
        text = self.path_edit.text().strip()
        if text and text != self.remote_dir:
            self._load(text)

    def _reload(self) -> None:
        """重新读取当前目录"""
        if self.remote_dir:
            self._load(self.remote_dir)

    def _activate(self, item: QListWidgetItem) -> None:
        """双击文件夹进入目录 双击文件按文本预览"""
        entry: RemoteEntry | None = item.data(ENTRY_ROLE)
        if entry is None:
            return
        if entry.is_dir:
            self._load(entry.path)
            return
        self._preview(entry)

    def _preview(self, entry: RemoteEntry) -> None:
        """读取远端文件并按文本弹窗展示"""
        if self._client is None:
            QMessageBox.warning(self, "未连接", "当前没有可用的服务器连接")
            return
        if self._reader is not None and self._reader.isRunning():
            return
        kind = text_kind(entry.name)
        if kind == "binary":
            QMessageBox.information(self, "无法预览", f"{entry.name} 不是文本文件")
            return
        worker = TextWorker(self._client, entry, kind, self)
        worker.loaded.connect(lambda text, truncated, origin: self._show_text(entry, kind, text, truncated))
        worker.rejected.connect(self._show_rejected)
        worker.failed.connect(self._on_preview_failed)
        worker.finished.connect(lambda: self._release_reader(worker))
        self._reader = worker
        self._set_status(f"正在读取 {entry.name} …")
        worker.start()

    def _show_text(self, entry: RemoteEntry, kind: str, text: str, truncated: bool) -> None:
        """弹出文本预览窗口"""
        if self.server is None or self._client is None:
            return
        self._set_status(f"{entry.name}｜{entry.path}")
        TextViewDialog(self, self._client, entry, kind, text, truncated).exec()

    def _show_rejected(self, message: str) -> None:
        """内容探测判定为二进制时的提示"""
        self._set_status(message)
        QMessageBox.information(self, "无法预览", message)

    def _on_preview_failed(self, message: str) -> None:
        """预览读取失败"""
        self._set_status(f"读取失败：{message}")
        QMessageBox.warning(self, "读取失败", message)

    def _release_reader(self, worker: TextWorker) -> None:
        """读取结束后解除引用再释放 避免访问已删除的对象"""
        if self._reader is worker:
            self._reader = None
        worker.deleteLater()

    def _context_menu(self, pos) -> None:
        """按点击位置弹出菜单 命中条目与命中空白处菜单不同"""
        item = self.view.itemAt(pos)
        menu = QMenu(self)
        if item is None:
            self.view.clearSelection()
            menu.addAction("新建文件夹").setData("mkdir")
            menu.addAction("上传文件").setData("upload")
            paste = menu.addAction("粘贴")
            paste.setData("paste")
            paste.setEnabled(bool(self._clip_entries))
            menu.addSeparator()
            menu.addAction("复制当前文件夹路径").setData("copy-here")
            menu.addAction("刷新").setData("refresh")
        else:
            if not item.isSelected():
                self.view.clearSelection()
                self.view.setCurrentItem(item)
                item.setSelected(True)
            entries = self._selected_entries()
            if len(entries) == 1 and entries[0].is_dir:
                menu.addAction("打开").setData("open")
                menu.addAction("下载整个文件夹").setData("download")
                menu.addSeparator()
                menu.addAction("复制文件夹路径").setData("copy-path")
            else:
                menu.addAction("下载").setData("download")
                menu.addSeparator()
                menu.addAction("复制文件路径").setData("copy-path")
            menu.addAction("复制所在文件夹路径").setData("copy-parent")
            menu.addSeparator()
            rename = menu.addAction("重命名")
            rename.setData("rename")
            rename.setEnabled(len(entries) == 1)
            menu.addAction("删除").setData("delete")
            menu.addAction("复制").setData("copy")
            menu.addAction("剪切").setData("cut")
            paste = menu.addAction("粘贴")
            paste.setData("paste")
            paste.setEnabled(bool(self._clip_entries))

        action = menu.exec(self.view.viewport().mapToGlobal(pos))
        if action is None:
            return
        entries = self._selected_entries()
        match action.data():
            case "mkdir":
                self._make_folder()
            case "upload":
                self._pick_upload()
            case "paste":
                self._paste()
            case "refresh":
                self._reload()
            case "open" if entries:
                self._load(entries[0].path)
            case "download":
                self._download(entries)
            case "rename" if entries:
                self._rename(entries[0])
            case "delete" if entries:
                self._delete(entries)
            case "copy" if entries:
                self._set_clip(entries, False)
            case "cut" if entries:
                self._set_clip(entries, True)
            case "copy-path" if entries:
                self._copy("\n".join(entry.path for entry in entries))
            case "copy-parent" if entries:
                self._copy(posixpath.dirname(entries[0].path))
            case "copy-here":
                self._copy(self.remote_dir)

    def _make_folder(self) -> None:
        """在当前目录新建文件夹"""
        if not self._ready():
            return
        name, accepted = QInputDialog.getText(self, "新建文件夹", "文件夹名称：")
        if not accepted:
            return
        name = name.strip()
        reason = invalid_name_reason(name, self.server.os_type)
        if reason:
            QMessageBox.warning(self, "名称无效", reason)
            return
        target = posixpath.join(self.remote_dir, name)
        try:
            with self._client.open_sftp() as sftp:
                sftp.mkdir(target)
        except Exception as error:
            QMessageBox.warning(self, "新建失败", str(error))
            return
        self._set_status(f"已新建文件夹 {target}")
        self._reload()

    def _rename(self, entry: RemoteEntry) -> None:
        """给单个条目改名 目标已存在时拒绝"""
        if not self._ready():
            return
        name, accepted = QInputDialog.getText(self, "重命名", "新名称：", text=entry.name)
        if not accepted:
            return
        name = name.strip()
        if name == entry.name:
            return
        reason = invalid_name_reason(name, self.server.os_type)
        if reason:
            QMessageBox.warning(self, "名称无效", reason)
            return
        target = posixpath.join(posixpath.dirname(entry.path), name)
        try:
            with self._client.open_sftp() as sftp:
                if ssh.exists(sftp, target):
                    QMessageBox.warning(self, "重命名失败", f"{name} 已存在")
                    return
                sftp.rename(entry.path, target)
        except Exception as error:
            QMessageBox.warning(self, "重命名失败", str(error))
            return
        self._set_status(f"已重命名为 {target}")
        self._reload()

    def _delete(self, entries: list[RemoteEntry]) -> None:
        """确认后删除选中条目 走远端删除命令 没有回收站"""
        if not entries or not self._ready():
            return
        names = "、".join(entry.name for entry in entries[:3])
        if len(entries) > 3:
            names += f" 等 {len(entries)} 项"
        answer = QMessageBox.question(
            self,
            "确认删除",
            f"确定删除 {names}？删除后无法恢复",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        jobs = [(entry.name, ssh.remove_command(entry.path, entry.is_dir, self.server.os_type)) for entry in entries]
        self._run_commands("删除", jobs)

    def _set_clip(self, entries: list[RemoteEntry], cut: bool) -> None:
        """把选中条目放进远端剪贴板 粘贴只会落在远端目录"""
        self._clip_entries = list(entries)
        self._clip_cut = cut
        self._set_status(f"已{'剪切' if cut else '复制'} {len(entries)} 项 进入目标目录后粘贴")

    def _paste(self) -> None:
        """把剪贴板条目复制或移动到当前目录 同名直接覆盖"""
        if not self._clip_entries or not self._ready():
            return
        os_type = self.server.os_type
        jobs: list[tuple[str, str]] = []
        skipped: list[str] = []
        try:
            with self._client.open_sftp() as sftp:
                for entry in self._clip_entries:
                    target = posixpath.join(self.remote_dir, entry.name)
                    if not ssh.exists(sftp, entry.path):
                        skipped.append(f"{entry.name}（源已不存在）")
                    elif target == entry.path:
                        skipped.append(f"{entry.name}（已在当前目录）")
                    elif entry.is_dir and target.startswith(entry.path.rstrip("/") + "/"):
                        skipped.append(f"{entry.name}（不能放进它自己里面）")
                    else:
                        jobs.append((entry.name, ssh.paste_command(entry.path, entry.is_dir, target, os_type, self._clip_cut)))
        except Exception as error:
            QMessageBox.warning(self, "粘贴失败", str(error))
            return
        if not jobs:
            QMessageBox.information(self, "无法粘贴", "没有可粘贴的项：" + "；".join(skipped[:3]))
            return
        if self._clip_cut and not skipped:
            # 剪切的内容已全部排上队 剪贴板没必要留着
            self._clip_entries = []
            self._clip_cut = False
        self._run_commands("粘贴", jobs)

    def _run_commands(self, title: str, jobs: list[tuple[str, str]]) -> None:
        """挂上进度框 在远端逐条执行命令"""
        if not jobs or not self._ready():
            return
        dialog = QProgressDialog("准备中…", "取消", 0, len(jobs), self)
        dialog.setWindowTitle(title)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setMinimumDuration(0)
        worker = FileCmdWorker(self._client, jobs, title, self)
        dialog.canceled.connect(worker.stop)
        worker.progress.connect(lambda done, total, name: self._update_progress(dialog, done, total, f"{title} {name}"))
        worker.done.connect(lambda ok, message: self._finish_commands(dialog, worker, ok, message))
        worker.finished.connect(worker.deleteLater)
        self._cmd = worker
        dialog.show()
        worker.start()

    def _finish_commands(self, dialog: QProgressDialog, worker: FileCmdWorker, ok: bool, message: str) -> None:
        """命令收尾 关掉进度框 提示结果并重拉目录"""
        dialog.close()
        dialog.deleteLater()
        if self._cmd is worker:
            self._cmd = None
        self._set_status(message)
        if not ok:
            QMessageBox.warning(self, "操作未全部成功", message)
        self._reload()

    def _download(self, entries: list[RemoteEntry]) -> None:
        """选保存目录后把远端条目下载过去"""
        if not entries or not self._ready():
            return
        target = QFileDialog.getExistingDirectory(self, "选择保存目录", self._last_local_dir)
        if not target:
            return
        self._last_local_dir = target
        self._run_transfer(DownloadWorker(self._client, entries, Path(target), self), "下载")

    def _pick_upload(self) -> None:
        """选择本地文件上传到当前目录"""
        if not self._ready():
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "选择要上传的文件", self._last_local_dir)
        if paths:
            self._last_local_dir = str(Path(paths[0]).parent)
            self._start_upload([Path(path) for path in paths])

    def _start_upload(self, paths: list[Path]) -> None:
        """把本地路径上传到当前目录 拖入与菜单共用"""
        if not paths or self.server is None or not self._ready():
            return
        self._run_transfer(UploadWorker(self._client, paths, self.remote_dir, self.server.os_type, self), "上传")

    def _ready(self) -> bool:
        """改动远端前的连接与互斥检查"""
        if self._client is None:
            QMessageBox.warning(self, "未连接", "当前没有可用的服务器连接")
            return False
        busy = [worker for worker in (self._transfer, self._cmd) if worker is not None and worker.isRunning()]
        if busy:
            QMessageBox.information(self, "任务进行中", "请等当前任务结束或先取消")
            return False
        return True

    def _run_transfer(self, worker: TransferWorker, title: str) -> None:
        """挂上进度对话框并启动传输"""
        dialog = QProgressDialog("准备中…", "取消", 0, 100, self)
        dialog.setWindowTitle(title)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setMinimumDuration(0)
        dialog.canceled.connect(worker.stop)
        worker.progress.connect(lambda done, total, name: self._update_progress(dialog, done, total, f"{title} {name}"))
        worker.done.connect(lambda ok, message: self._finish_transfer(dialog, worker, ok, message))
        worker.finished.connect(worker.deleteLater)
        self._transfer = worker
        dialog.show()
        worker.start()

    def _update_progress(self, dialog: QProgressDialog, done: int, total: int, text: str) -> None:
        """刷新进度对话框 总数为 0 时按不确定进度显示"""
        dialog.setLabelText(text)
        dialog.setMaximum(total or 1)
        dialog.setValue(min(done, total or 1))

    def _finish_transfer(self, dialog: QProgressDialog, worker: TransferWorker, ok: bool, message: str) -> None:
        """传输收尾 关掉进度框并提示结果"""
        dialog.close()
        dialog.deleteLater()
        if self._transfer is worker:
            self._transfer = None
        self._set_status(message)
        if not ok:
            QMessageBox.warning(self, "传输未结束", message)
        if isinstance(worker, UploadWorker):
            # 只有上传会改变远端内容 下载后不必重拉目录 也避免盖掉结果提示
            self._reload()

    def _selected_entries(self) -> list[RemoteEntry]:
        """当前选中的条目"""
        return [item.data(ENTRY_ROLE) for item in self.view.selectedItems()]

    def _copy(self, text: str) -> None:
        """复制文本到剪贴板"""
        if text:
            QGuiApplication.clipboard().setText(text)
            self._set_status(f"已复制：{text}")

    def _set_status(self, text: str) -> None:
        """更新状态行文字"""
        self.status_label.setText(text)

    def _set_toolbar_enabled(self, enabled: bool) -> None:
        """读取目录期间禁用导航控件 防止请求叠加"""
        for widget in (self.back_button, self.up_button, self.refresh_button, self.path_edit, self.sort_combo, self.order_button):
            widget.setEnabled(enabled)