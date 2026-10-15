"""paramiko 封装 只提供连接 目录上传 命令执行三个能力"""

from __future__ import annotations

import posixpath
import stat
import time
from collections.abc import Callable
from pathlib import Path

import paramiko

from store import Server

# 非阻塞读取的轮询间隔 越小命令输出越跟手
POLL_INTERVAL = 0.05

# 需要补可执行位的后缀 无后缀的脚本文件同样补
EXECUTABLE_SUFFIXES = ("", ".sh")


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


def upload_tree(
    sftp: paramiko.SFTPClient,
    local: Path,
    remote: str,
    os_type: str = "linux",
    on_file: Callable[[Path, str], None] | None = None,
) -> int:
    """把本地目录的内容递归上传到远程目录 同名文件直接覆盖 返回上传的文件数"""
    remote = expand_path(sftp, remote)
    _make_dirs(sftp, remote)
    count = 0
    for entry in sorted(local.iterdir()):
        target = posixpath.join(remote, entry.name)
        if entry.is_dir():
            count += upload_tree(sftp, entry, target, os_type, on_file)
            continue
        if not entry.is_file():
            continue
        sftp.put(str(entry), target)
        if os_type == "linux" and entry.suffix in EXECUTABLE_SUFFIXES:
            mark_executable(sftp, target)
        count += 1
        if on_file:
            on_file(entry, target)
    return count


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