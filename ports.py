"""扫描远端正在监听的端口 解析 ss netstat tasklist 的输出并取程序路径"""

from __future__ import annotations

import csv
import re

import paramiko

import ssh
from store import ScannedPort

# ss 优先 机器上没有 ss 时退回 netstat
LINUX_COMMAND = "ss -ltnp 2>/dev/null || netstat -ltnp 2>/dev/null"
WINDOWS_NETSTAT = "netstat -ano -p TCP"
WINDOWS_TASKLIST = "tasklist /FO CSV /NH"

# 程序路径单独取一次 Linux 读 /proc 的符号链接 Windows 让 PowerShell 列出进程路径
LINUX_PROGRAM_PATHS = "ls -l /proc/*/exe 2>/dev/null"
WINDOWS_PROGRAM_PATHS = (
    'powershell -NoProfile -Command "Get-Process | Select-Object Id,Path |'
    ' Format-Table -HideTableHeaders -AutoSize -Width 512"'
)

# ss 把进程信息放在行尾的 users:(("nginx",pid=1,fd=6)) 里
SS_PROGRAM = re.compile(r'users:\(\("([^"]+)",pid=(\d+)')
# ls -l 输出里的 /proc/1234/exe -> /usr/sbin/nginx
PROC_EXE = re.compile(r"/proc/(\d+)/exe -> (.*)")

# 监听行的行首取值 ss 是 LISTEN netstat 是 tcp/tcp6 两者的本地地址都在第 4 列
LISTEN_STATES = ("LISTEN", "tcp", "tcp6")


def scan(client: paramiko.SSHClient, os_type: str) -> dict[int, ScannedPort]:
    """按远端系统执行命令 返回 端口 -> 扫描结果 程序名与路径取不到时留空"""
    if os_type == "windows":
        listeners = parse_windows(_run(client, WINDOWS_NETSTAT), _run(client, WINDOWS_TASKLIST))
        paths = parse_windows_paths(_run(client, WINDOWS_PROGRAM_PATHS))
    else:
        listeners = parse_listeners(_run(client, LINUX_COMMAND))
        paths = parse_proc_paths(_run(client, LINUX_PROGRAM_PATHS))
    return {
        port: ScannedPort(port=port, program=program, path=paths.get(pid or 0, ""))
        for port, (program, pid) in listeners.items()
    }


def parse_listeners(text: str) -> dict[int, tuple[str, int | None]]:
    """解析 ss 或 netstat 的输出 得到 端口 -> (程序名, PID)"""
    listeners: dict[int, tuple[str, int | None]] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] not in LISTEN_STATES:
            continue
        port = _tail_port(parts[3])
        if port is None:
            continue
        program, pid = _ss_program(line)
        if not program:
            program, pid = _netstat_program(parts)
        listeners[port] = (program, pid)
    return listeners


def parse_windows(netstat_text: str, tasklist_text: str) -> dict[int, tuple[str, int | None]]:
    """用 tasklist 的 PID 换出 netstat 里 LISTENING 端口的进程名"""
    names = _process_names(tasklist_text)
    listeners: dict[int, tuple[str, int | None]] = {}
    for line in netstat_text.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP" or parts[3].upper() != "LISTENING":
            continue
        port = _tail_port(parts[1])
        pid = _as_int(parts[4])
        if port is not None:
            listeners[port] = (names.get(pid, ""), pid)
    return listeners


def parse_proc_paths(text: str) -> dict[int, str]:
    """解析 ls -l /proc/*/exe 得到 PID -> 可执行文件路径 已被替换的旧二进制去掉 deleted 标记"""
    paths: dict[int, str] = {}
    for line in text.splitlines():
        match = PROC_EXE.search(line)
        if match:
            paths[int(match.group(1))] = match.group(2).removesuffix(" (deleted)").strip()
    return paths


def parse_windows_paths(text: str) -> dict[int, str]:
    """解析 Get-Process 输出的 Id 与 Path 两列 取不到路径的进程只有 PID"""
    paths: dict[int, str] = {}
    for line in text.splitlines():
        pid, _, path = line.strip().partition(" ")
        if pid.isdigit() and path.strip():
            paths[int(pid)] = path.strip()
    return paths


def _run(client: paramiko.SSHClient, command: str) -> str:
    """执行命令并收集全部输出"""
    chunks: list[str] = []
    ssh.run_command(client, command, chunks.append)
    return "".join(chunks)


def _tail_port(text: str) -> int | None:
    """取地址末尾的端口号 不是数字说明这行没法用"""
    tail = text.rpartition(":")[2]
    return int(tail) if tail.isdigit() else None


def _ss_program(line: str) -> tuple[str, int | None]:
    """ss 输出里的进程名与 PID"""
    match = SS_PROGRAM.search(line)
    return (match.group(1), int(match.group(2))) if match else ("", None)


def _netstat_program(parts: list[str]) -> tuple[str, int | None]:
    """netstat 末列的 PID/程序名 斜杠前后各取一段"""
    if len(parts) > 6 and parts[5] == "LISTEN":
        pid, _, program = parts[6].partition("/")
        return program, _as_int(pid)
    return "", None


def _process_names(tasklist_text: str) -> dict[int, str]:
    """tasklist 的 CSV 输出转成 PID -> 进程名"""
    names: dict[int, str] = {}
    for row in csv.reader(tasklist_text.splitlines()):
        pid = _as_int(row[1]) if len(row) > 1 else None
        if pid is not None:
            names[pid] = row[0]
    return names


def _as_int(text: str) -> int | None:
    """纯数字才转成整数"""
    return int(text) if text.isdigit() else None