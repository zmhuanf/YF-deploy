"""证书与私钥文件的识别 只读内容不改文件"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed448, ed25519, rsa
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.x509.oid import NameOID

# 一个文件里可能串着多张证书 取第一张作叶子证书
CERT_BLOCK = re.compile(rb"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.S)
KEY_PEM = re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")

# 证书与私钥不会超过这个体积 更大的文件直接当无关文件
MAX_SIZE = 4 * 1024 * 1024
# 私钥抽象类到展示用的算法名
KEY_NAMES = (
    (rsa.RSAPrivateKey, "RSA"),
    (ec.EllipticCurvePrivateKey, "EC"),
    (ed25519.Ed25519PrivateKey, "Ed25519"),
    (ed448.Ed448PrivateKey, "Ed448"),
    (dsa.DSAPrivateKey, "DSA"),
)


@dataclass(slots=True)
class CertInfo:
    """一张证书里与运维相关的信息"""

    domains: list[str] = field(default_factory=list)
    not_before: date = date.min
    not_after: date = date.min
    public_key: bytes = b""

    @property
    def name(self) -> str:
        """主域名 优先 CN 再退到首个 SAN 都没有时给个占位"""
        return self.domains[0] if self.domains else "unnamed"


@dataclass(slots=True)
class KeyInfo:
    """私钥的类型与公钥 加密或格式不支持时 public_key 为空 无法校验配对"""

    kind: str = ""
    public_key: bytes | None = None


def inspect(path: Path) -> tuple[CertInfo | None, KeyInfo | None]:
    """读一次文件 按内容判断是证书还是私钥 两者都可能同时出现"""
    try:
        if path.stat().st_size > MAX_SIZE:
            return None, None
        data = path.read_bytes()
    except OSError:
        return None, None
    return parse_certificate(data), parse_private_key(data)


def parse_certificate(data: bytes) -> CertInfo | None:
    """先按 PEM 再按 DER 解析证书 失败返回 None"""
    cert = _load_certificate(data)
    if cert is None:
        return None
    return CertInfo(_domains(cert), cert.not_valid_before_utc.date(), cert.not_valid_after_utc.date(), _spki(cert.public_key()))


def parse_private_key(data: bytes) -> KeyInfo | None:
    """识别私钥 能解析的出算法与公钥 加密或不支持的格式只给类型名"""
    key = _load_private_key(data)
    if key is not None:
        size = getattr(key, "key_size", 0)
        kind = next((name for cls, name in KEY_NAMES if isinstance(key, cls)), "私钥")
        return KeyInfo(f"{kind} {size}".strip(), _spki(key.public_key()))
    if KEY_PEM.search(data):
        return KeyInfo("加密私钥" if b"ENCRYPTED" in data else "未支持的私钥格式", None)
    return None


def is_pair(cert: CertInfo, key: KeyInfo) -> bool:
    """比对公钥确认证书与私钥配对 无法解析的私钥按通过处理"""
    return key.public_key is None or cert.public_key == key.public_key


def days_left(not_after: str) -> int:
    """到期日距今天的天数 负数表示已过期 日期损坏时按 0 处理"""
    try:
        return (date.fromisoformat(not_after) - date.today()).days
    except ValueError:
        return 0


def status_text(days: int) -> str:
    """角标上的到期状态"""
    return "已过期" if days < 0 else f"剩 {days} 天"


def status_color(days: int) -> str:
    """已过期红色 30 天内到期橙色 其余返回空串用默认色"""
    if days < 0:
        return "#d9534f"
    return "#e08b1a" if days <= 30 else ""


def _load_certificate(data: bytes) -> x509.Certificate | None:
    """PEM 取首个证书块 DER 直接解析"""
    block = CERT_BLOCK.search(data)
    if block:
        try:
            return x509.load_pem_x509_certificate(block.group(0))
        except ValueError:
            return None
    try:
        return x509.load_der_x509_certificate(data)
    except ValueError:
        return None


def _load_private_key(data: bytes):
    """依次按 PEM 与 DER 尝试解析未加密私钥"""
    for loader in (serialization.load_pem_private_key, serialization.load_der_private_key):
        try:
            return loader(data, password=None)
        except (ValueError, TypeError, UnsupportedAlgorithm):
            continue
    return None


def _spki(public_key) -> bytes:
    """公钥的统一编码 用于比对证书与私钥是否配对"""
    return public_key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def _domains(cert: x509.Certificate) -> list[str]:
    """CN 排最前 SAN 依次跟在后面 去重保序"""
    names = [str(attr.value).strip() for attr in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)]
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        san = None
    if san is not None:
        names += [str(name).strip() for name in san.get_values_for_type(x509.DNSName)]
    return list(dict.fromkeys(name for name in names if name))
