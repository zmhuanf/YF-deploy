"""paramiko 封装 提供连接 目录读写 文件传输与命令执行能力"""

from __future__ import annotations

import posixpath
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import paramiko

from store import Server

# 非阻塞读取的轮询间隔 越小命令输出越跟手
POLL_INTERVAL = 0.05

# 需要补可执行位的后缀 无后缀的脚本文件同样补
EXECUTABLE_SUFFIXES = ("", ".sh")

# 单文件传输进度回调 参数为 文件名 已传字节 总字节
ProgressHook = Callable[[str, int, int], None]


@dataclass(slots=True)
class RemoteEntry:
    """远端目录里的一项"""

    name: str
    path: str
    is_dir: bool
    size: int
    mtime: float
    mode: int


def connect(server: Server) -> paramiko.SSHClient:
    """按服务器配置建立 SSH 连接 配了密钥则优先用密钥"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    options = {
        "hostname": server.host,
        "port": server.port,
        "username": server.username,
        "timeout": 15,
        "banner_timeout": 15,
        "auth_timeout": 15,
    }
    if server.key_file:
        options["key_filename"] = server.key_file
        options["passphrase"] = server.key_passphrase or None
    else:
        options["password"] = server.password
    client.connect(**options)
    return client


def expand_path(sftp: paramiko.SFTPClient, path: str) -> str:
    """把 ~ 前缀换成远端 home SFTP 接口本身不认 ~"""
    if path in ("", "~"):
        return sftp.normalize(".") if path else ""
    if path.startswith("~/"):
        return posixpath.join(sftp.normalize("."), path[2:])
    return path


def list_dir(sftp: paramiko.SFTPClient, path: str) -> list[RemoteEntry]:
    """列出远端目录内容 符号链接按它指向的目标判断是否为目录"""
    root = expand_path(sftp, path)
    entries: list[RemoteEntry] = []
    for attr in sftp.listdir_attr(root):
        target = posixpath.join(root, attr.filename)
        is_dir = stat.S_ISDIR(attr.st_mode)
        if stat.S_ISLNK(attr.st_mode):
            try:
                is_dir = stat.S_ISDIR(sftp.stat(target).st_mode)
            except OSError:
                is_dir = False
        entries.append(RemoteEntry(attr.filename, target, is_dir, attr.st_size or 0, attr.st_mtime or 0, attr.st_mode or 0))
    return entries


def scan_tree(sftp: paramiko.SFTPClient, remote: str) -> tuple[int, int]:
    """统计远端目录的文件数与总字节数 用于下载进度 符号链接不计入"""
    files = 0
    total = 0
    for attr in sftp.listdir_attr(remote):
        path = posixpath.join(remote, attr.filename)
        if stat.S_ISLNK(attr.st_mode):
            continue
        if stat.S_ISDIR(attr.st_mode):
            sub_files, sub_total = scan_tree(sftp, path)
            files += sub_files
            total += sub_total
            continue
        files += 1
        total += attr.st_size or 0
    return files, total


def download_file(sftp: paramiko.SFTPClient, remote: str, local: Path, on_bytes: ProgressHook | None = None) -> None:
    """下载单个文件到本地 父目录自动创建"""
    local.parent.mkdir(parents=True, exist_ok=True)
    callback = (lambda done, total: on_bytes(posixpath.basename(remote), done, total)) if on_bytes else None
    sftp.get(remote, str(local), callback=callback, prefetch=False)


def download_tree(sftp: paramiko.SFTPClient, remote: str, local: Path, on_bytes: ProgressHook | None = None) -> int:
    """递归下载远端目录到本地 符号链接不跟随 返回下载的文件数"""
    local.mkdir(parents=True, exist_ok=True)
    count = 0
    for attr in sftp.listdir_attr(remote):
        if stat.S_ISLNK(attr.st_mode):
            continue
        path = posixpath.join(remote, attr.filename)
        target = local / attr.filename
        if stat.S_ISDIR(attr.st_mode):
            count += download_tree(sftp, path, target, on_bytes)
            continue
        download_file(sftp, path, target, on_bytes)
        count += 1
    return count


def upload_tree(
    sftp: paramiko.SFTPClient,
    local: Path,
    remote: str,
    os_type: str = "linux",
    on_file: Callable[[Path, str], None] | None = None,
    on_bytes: ProgressHook | None = None,
) -> int:
    """把本地目录的内容递归上传到远程目录 同名文件直接覆盖 返回上传的文件数"""
    remote = expand_path(sftp, remote)
    _make_dirs(sftp, remote)
    count = 0
    for entry in sorted(local.iterdir()):
        target = posixpath.join(remote, entry.name)
        if entry.is_dir():
            count += upload_tree(sftp, entry, target, os_type, on_file, on_bytes)
            continue
        if not entry.is_file():
            continue
        _put_file(sftp, entry, target, os_type, on_bytes)
        count += 1
        if on_file:
            on_file(entry, target)
    return count


def upload_entry(
    sftp: paramiko.SFTPClient,
    local: Path,
    remote_dir: str,
    os_type: str = "linux",
    on_file: Callable[[Path, str], None] | None = None,
    on_bytes: ProgressHook | None = None,
) -> int:
    """把本地文件或文件夹放进远程目录 文件夹保留自身的名字层级 返回上传的文件数"""
    if local.is_dir():
        return upload_tree(sftp, local, posixpath.join(expand_path(sftp, remote_dir), local.name), os_type, on_file, on_bytes)
    target_dir = expand_path(sftp, remote_dir)
    _make_dirs(sftp, target_dir)
    target = posixpath.join(target_dir, local.name)
    _put_file(sftp, local, target, os_type, on_bytes)
    if on_file:
        on_file(local, target)
    return 1


def mark_executable(sftp: paramiko.SFTPClient, path: str) -> None:
    """给远程文件追加可执行位"""
    mode = stat.S_IMODE(sftp.stat(path).st_mode)
    sftp.chmod(path, mode | 0o111)


def run_command(
    client: paramiko.SSHClient,
    command: str,
    emit: Callable[[str], None],
    should_stop: Callable[[], bool] = lambda: False,
) -> int:
    """执行远程命令并把输出实时交给 emit 返回退出码 被中断时返回 -1"""
    channel = client.get_transport().open_session()
    try:
        channel.exec_command(command)
        while True:
            if should_stop():
                return -1
            idle = True
            while channel.recv_ready():
                emit(channel.recv(65536).decode("utf-8", "replace"))
                idle = False
            while channel.recv_stderr_ready():
                emit(channel.recv_stderr(65536).decode("utf-8", "replace"))
                idle = False
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                return channel.recv_exit_status()
            if idle:
                time.sleep(POLL_INTERVAL)
    finally:
        channel.close()


def _put_file(sftp: paramiko.SFTPClient, local: Path, remote: str, os_type: str, on_bytes: ProgressHook | None = None) -> None:
    """上传单个文件 按目标系统类型决定是否补可执行位"""
    callback = (lambda done, total: on_bytes(local.name, done, total)) if on_bytes else None
    sftp.put(str(local), remote, callback=callback, confirm=False)
    if os_type == "linux" and local.suffix in EXECUTABLE_SUFFIXES:
        mark_executable(sftp, remote)


def _make_dirs(sftp: paramiko.SFTPClient, path: str) -> None:
    """逐级创建远程目录 已存在的层级自动跳过"""
    if path in ("", "/"):
        return
    current = "/" if path.startswith("/") else ""
    for part in (p for p in path.split("/") if p):
        current = posixpath.join(current, part) if current else part
        try:
            sftp.stat(current)
        except FileNotFoundError:
            try:
                sftp.mkdir(current)
            except OSError:
                # Windows 远端的盘符层级无法创建 交给后续 put 报错
                pass