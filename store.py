"""服务器与项目的数据模型 以及 .data 目录下的 JSON 持久化"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / ".data"

# 数据目录可用环境变量改写 测试时指向临时目录 避免动到真实配置
DATA_DIR_ENV = "YF_DEPLOY_DATA_DIR"

# 全局 pi 启动指令模板的出厂值 用户可在右键菜单里改写
DEFAULT_PI_TEMPLATE = "npm run start -- --ssh {username}@{host}:{port} --pass {password}"

# 模板里支持替换的占位符
PI_PLACEHOLDERS = ("name", "username", "host", "port", "password")


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


def render_pi_command(template: str, server: Server) -> str:
    """把服务器信息套进模板 认不出的占位符原样保留"""
    values = {
        "name": server.name,
        "username": server.username,
        "host": server.host,
        "port": str(server.port),
        "password": server.password,
    }
    return re.sub(r"\{(\w+)\}", lambda match: values.get(match.group(1), match.group(0)), template)


def _valid_template(raw) -> str:
    """设置文件里读到的模板不可用时回落到出厂值"""
    return raw.strip() if isinstance(raw, str) and raw.strip() else DEFAULT_PI_TEMPLATE


def _load_settings(path: Path) -> dict:
    """读取设置文件 文件缺失或损坏时按空处理"""
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


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
    """servers.json projects.json settings.json 的读写入口"""

    def __init__(self, data_dir: Path | None = None) -> None:
        root = Path(data_dir or os.environ.get(DATA_DIR_ENV) or DATA_DIR)
        root.mkdir(parents=True, exist_ok=True)
        self.servers_file = root / "servers.json"
        self.projects_file = root / "projects.json"
        self.settings_file = root / "settings.json"
        self.servers: list[Server] = _load(self.servers_file, Server)
        self.projects: list[Project] = _load(self.projects_file, Project)
        self.pi_template: str = _valid_template(_load_settings(self.settings_file).get("pi_template"))

    def server(self, server_id: str) -> Server | None:
        """按 id 取服务器 不存在返回 None"""
        return next((item for item in self.servers if item.id == server_id), None)

    def project(self, project_id: str) -> Project | None:
        """按 id 取项目 不存在返回 None"""
        return next((item for item in self.projects if item.id == project_id), None)

    def pi_command(self, server: Server) -> str:
        """按全局模板生成这台服务器的 pi 启动指令"""
        return render_pi_command(self.pi_template, server)

    def save_pi_template(self, template: str) -> None:
        """保存全局模板并落盘 空模板回落到出厂值"""
        self.pi_template = template.strip() or DEFAULT_PI_TEMPLATE
        self.settings_file.write_text(json.dumps({"pi_template": self.pi_template}, ensure_ascii=False, indent=2), "utf-8")

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