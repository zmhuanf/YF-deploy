"""paramiko 封装 提供连接 目录读写 文件传输与命令执行能力"""

from __future__ import annotations

import posixpath
import re
import stat
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import paramiko

from store import Server

# 非阻塞读取的轮询间隔 越小命令输出越跟手
POLL_INTERVAL = 0.05

# 需要补可执行位的后缀 无后缀的脚本文件同样补
EXECUTABLE_SUFFIXES = ("", ".sh")

# 上传中转文件的后缀 内核禁止覆盖正在运行的可执行文件 只能先写中转名再改名
TEMP_SUFFIX = ".yf-tmp"

# 文本预览最多回传的字节数与行数
TEXT_BYTE_LIMIT = 1024 * 1024
TEXT_LINE_LIMIT = 5000

# 单文件传输进度回调 参数为 文件名 已传字节 总字节
ProgressHook = Callable[[str, int, int], None]

# Windows 的 SFTP 路径形如 /C:/Users/yf 要还原成盘符写法才好交给 cmd
WINDOWS_DRIVE = re.compile(r"^/[A-Za-z]:")


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


def read_head(sftp: paramiko.SFTPClient, remote: str, size: int) -> bytes:
    """读取远端文件开头的若干字节 用来判断是不是文本"""
    with sftp.open(remote, "rb") as handle:
        return handle.read(size)


def looks_like_text(head: bytes) -> bool:
    """按开头字节判断是否为文本 出现 NUL 或控制字符过多就当成二进制"""
    if not head:
        return True
    if b"\0" in head:
        return False
    allowed = {9, 10, 12, 13}
    control = sum(1 for byte in head if byte < 32 and byte not in allowed)
    return control / len(head) < 0.05


def read_text(sftp: paramiko.SFTPClient, remote: str, byte_limit: int = TEXT_BYTE_LIMIT, line_limit: int = TEXT_LINE_LIMIT) -> tuple[str, bool]:
    """读取远端文件开头部分并解码 返回文本与是否被截断"""
    with sftp.open(remote, "rb") as handle:
        raw = handle.read(byte_limit + 1)
    truncated = len(raw) > byte_limit
    lines = decode_text(raw[:byte_limit]).splitlines()
    if len(lines) > line_limit:
        return "\n".join(lines[:line_limit]), True
    return "\n".join(lines), truncated


def decode_text(raw: bytes) -> str:
    """按 UTF-8 GB18030 的顺序尝试解码 被截断的尾部残缺字节直接丢掉"""
    for trim in (0, 1, 2, 3):
        try:
            return raw[: len(raw) - trim].decode("utf-8")
        except UnicodeDecodeError:
            continue
    try:
        return raw.decode("gb18030")
    except UnicodeDecodeError:
        return raw.decode("utf-8", "replace")


def read_tail(sftp: paramiko.SFTPClient, remote: str, byte_limit: int = TEXT_BYTE_LIMIT, line_limit: int = TEXT_LINE_LIMIT) -> tuple[str, bool]:
    """读取远端文件末尾部分并解码 返回文本与前面是否还有内容"""
    offset = max(0, sftp.stat(remote).st_size - byte_limit)
    with sftp.open(remote, "rb") as handle:
        if offset:
            handle.seek(offset)
        raw = handle.read(byte_limit)
    if offset:
        # 从中间切进去的首行往往不完整 丢掉
        first_break = raw.find(b"\n")
        raw = raw[first_break + 1 :] if first_break >= 0 else raw
    lines = decode_text(raw).splitlines()
    if len(lines) > line_limit:
        return "\n".join(lines[-line_limit:]), True
    return "\n".join(lines), offset > 0


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


def exists(sftp: paramiko.SFTPClient, path: str) -> bool:
    """远端路径是否存在"""
    try:
        sftp.stat(path)
    except FileNotFoundError:
        return False
    return True


def native_path(path: str, os_type: str) -> str:
    """把 SFTP 路径换成远端系统认得的写法"""
    if os_type != "windows":
        return path
    trimmed = path[1:] if WINDOWS_DRIVE.match(path) else path
    return trimmed.replace("/", "\\")


def quote(path: str, os_type: str) -> str:
    """按远端 shell 的规则加引号 防止空格等字符把路径拆开"""
    if os_type == "windows":
        return f'"{path}"'
    return "'" + path.replace("'", "'\\''") + "'"


def remove_command(path: str, is_dir: bool, os_type: str) -> str:
    """删除远端文件或目录的命令 目标本来就不存在时同样按成功处理"""
    target = quote(native_path(path, os_type), os_type)
    if os_type == "windows":
        # rmdir 找不到目录会返回非零 先探一次存在性
        return f"if exist {target} rmdir /S /Q {target}" if is_dir else f"if exist {target} del /F /Q {target}"
    return f"rm -rf -- {target}"


def paste_command(src: str, is_dir: bool, dst: str, os_type: str, cut: bool) -> str:
    """把远端条目复制或移动到完整目标路径 同名目标先删掉实现覆盖"""
    source = quote(native_path(src, os_type), os_type)
    target = quote(native_path(dst, os_type), os_type)
    if os_type == "windows":
        # 目标可能是文件也可能是目录 两种删法都试一遍
        clear = f"(rd /S /Q {target} 2>nul || del /F /Q {target} 2>nul) & "
        if cut:
            return f"{clear}move /Y {source} {target}"
        if is_dir:
            # robocopy 退出码小于 8 都算成功 重新给出干净的退出码
            return f"{clear}robocopy {source} {target} /E /IS /IT /NFL /NDL /NJH /NJS /NP & if errorlevel 8 (exit 1) else (exit 0)"
        return f"{clear}copy /Y {source} {target}"
    clear = f"rm -rf -- {target} && "
    return f"{clear}{'mv' if cut else 'cp -a'} -- {source} {target}"


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
    """上传单个文件到中转名再改名顶替目标 直接覆盖运行中的可执行文件会被内核拒绝"""
    temp = f"{remote}{TEMP_SUFFIX}"
    callback = (lambda done, total: on_bytes(local.name, done, total)) if on_bytes else None
    try:
        sftp.put(str(local), temp, callback=callback, confirm=False)
        if os_type == "linux" and local.suffix in EXECUTABLE_SUFFIXES:
            mark_executable(sftp, temp)
        _replace_file(sftp, temp, remote)
    except Exception:
        # 失败时清掉中转文件 避免远端留下垃圾
        with suppress(OSError):
            sftp.remove(temp)
        raise


def _replace_file(sftp: paramiko.SFTPClient, temp: str, remote: str) -> None:
    """用中转文件顶替目标 老服务端不支持 posix 改名时退回先删后改名"""
    try:
        sftp.posix_rename(temp, remote)
    except (OSError, paramiko.SSHException):
        if exists(sftp, remote):
            sftp.remove(remote)
        sftp.rename(temp, remote)


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