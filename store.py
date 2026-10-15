"""服务器与项目的数据模型 以及 .data 目录下的 JSON 持久化"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / ".data"


@dataclass(slots=True)
class Server:
    """一台可 SSH 连接的主机"""

    name: str = ""
    host: str = ""
    port: int = 22
    username: str = ""
    password: str = ""
    key_file: str = ""
    key_passphrase: str = ""
    os_type: str = "linux"
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:12]

    @property
    def ssh_command(self) -> str:
        """可直接粘贴到终端执行的 ssh 连接指令"""
        return f"ssh -p {self.port} {self.username}@{self.host}"

    @property
    def pi_command(self) -> str:
        """交给 pi 启动的指令 未配置密码时返回空串"""
        if not self.password:
            return ""
        return f"npm run start -- --ssh {self.username}@{self.host}:{self.port} --pass {self.password}"

    @property
    def address(self) -> str:
        """地址与端口的展示形式"""
        return f"{self.host}:{self.port}"


@dataclass(slots=True)
class Project:
    """一次「上传本地目录 + 执行远程命令」的配置"""

    name: str = ""
    server_id: str = ""
    local_path: str = ""
    remote_path: str = ""
    command: str = ""
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:12]


def _load(path: Path, cls: type) -> list:
    """读取 JSON 列表 忽略数据类里已不存在的字段；文件损坏时按空处理"""
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    known = {field.name for field in fields(cls)}
    return [cls(**{k: v for k, v in item.items() if k in known}) for item in raw]


def _dump(path: Path, items: list) -> None:
    """把数据类列表写入 JSON"""
    path.write_text(json.dumps([asdict(item) for item in items], ensure_ascii=False, indent=2), "utf-8")


class Store:
    """servers.json 与 projects.json 的读写入口"""

    def __init__(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.servers_file = DATA_DIR / "servers.json"
        self.projects_file = DATA_DIR / "projects.json"
        self.servers: list[Server] = _load(self.servers_file, Server)
        self.projects: list[Project] = _load(self.projects_file, Project)

    def server(self, server_id: str) -> Server | None:
        """按 id 取服务器 不存在返回 None"""
        return next((item for item in self.servers if item.id == server_id), None)

    def project(self, project_id: str) -> Project | None:
        """按 id 取项目 不存在返回 None"""
        return next((item for item in self.projects if item.id == project_id), None)

    def upsert_server(self, server: Server) -> None:
        """按 id 覆盖保存服务器 不存在的则追加"""
        self._upsert(self.servers, server)
        _dump(self.servers_file, self.servers)

    def upsert_project(self, project: Project) -> None:
        """按 id 覆盖保存项目 不存在的则追加"""
        self._upsert(self.projects, project)
        _dump(self.projects_file, self.projects)

    def remove_server(self, server_id: str) -> None:
        """删除服务器并落盘 引用它的项目保持原样"""
        self.servers = [item for item in self.servers if item.id != server_id]
        _dump(self.servers_file, self.servers)

    def remove_project(self, project_id: str) -> None:
        """删除项目并落盘"""
        self.projects = [item for item in self.projects if item.id != project_id]
        _dump(self.projects_file, self.projects)

    @staticmethod
    def _upsert(items: list, item) -> None:
        """就地替换同 id 元素 未命中则追加"""
        for index, existing in enumerate(items):
            if existing.id == item.id:
                items[index] = item
                return
        items.append(item)