"""添加或替换证书的弹框 拖入一对证书与私钥后解析展示"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import certs
from store import Certificate, Store

DROP_HINT = "把证书和私钥文件拖到这里\n或点下面的「浏览…」选择"


def _dropped_paths(event) -> list[Path]:
    """拖放事件里带的本机文件路径"""
    return [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]


class CertDropArea(QLabel):
    """接收拖拽文件的投放区 一次只认一对文件"""

    def __init__(self, on_files, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on_files = on_files
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(110)
        self.setWordWrap(True)
        self.setStyleSheet(
            "border:2px dashed #c3cbd8;border-radius:8px;color:#6b7280;background:#fafbfd;"
        )

    def dragEnterEvent(self, event) -> None:
        """只接本机文件路径"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        """把所有拖入项交给弹框统一归类"""
        paths = _dropped_paths(event)
        if paths:
            self._on_files(paths)
        event.acceptProposedAction()


class CertDialog(QDialog):
    """添加或替换证书 拖入或选取文件后自动识别 替换时校验域名与原证书一致"""

    def __init__(self, store: Store, parent: QWidget | None = None, target: Certificate | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("替换证书" if target else "添加证书")
        self.setMinimumWidth(560)
        self.setAcceptDrops(True)
        self._store = store
        self._target = target
        self._cert_path: Path | None = None
        self._key_path: Path | None = None
        self._cert: certs.CertInfo | None = None
        self._key: certs.KeyInfo | None = None

        self._drop_hint = (
            f"拖入新的证书和私钥替换「{target.name}」\n域名必须与原证书相同"
            if target
            else DROP_HINT
        )
        self.drop_area = CertDropArea(self._accept_files)
        self.drop_area.setText(self._drop_hint)

        browse = QPushButton("浏览…")
        browse.clicked.connect(self._browse)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#6b7280;")

        self.domains_label = self._value_label()
        self.period_label = self._value_label()
        self.left_label = self._value_label()
        self.key_label = self._value_label()
        self.files_label = self._value_label()
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.addRow("域名", self.domains_label)
        form.addRow("有效期", self.period_label)
        form.addRow("剩余", self.left_label)
        form.addRow("私钥", self.key_label)
        form.addRow("文件", self.files_label)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

        layout = QVBoxLayout(self)
        layout.addWidget(self.drop_area)
        layout.addWidget(browse, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.hint)
        layout.addLayout(form)
        layout.addWidget(self.buttons)

    def dragEnterEvent(self, event) -> None:
        """拖到弹框空白处也接"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        """拖到空白处与拖到投放区等价"""
        paths = _dropped_paths(event)
        if paths:
            self._accept_files(paths)
        event.acceptProposedAction()

    def _accept_files(self, paths: list[Path]) -> None:
        """按内容归类 证书与私钥各取一个 多出来的直接拒绝"""
        found_cert, found_key = [], []
        for path in paths:
            if not path.is_file():
                continue
            cert, key = certs.inspect(path)
            if cert:
                found_cert.append((path, cert))
            if key:
                found_key.append((path, key))
        if len(found_cert) != 1 or len(found_key) != 1:
            self._fail(
                "没有识别出恰好一份证书和一份私钥，请只拖入这两个文件"
                f"（当前识别到 {len(found_cert)} 份证书、{len(found_key)} 份私钥）"
            )
            return
        (cert_path, cert), (key_path, key) = found_cert[0], found_key[0]
        if not certs.is_pair(cert, key):
            self._fail("私钥与证书不匹配，请确认拖入的是同一对文件")
            return
        if self._target and set(cert.domains) != set(self._target.domains):
            self._fail(
                "域名与原证书不一致，替换要求域名完全相同\n"
                f"原：{'，'.join(self._target.domains)}\n新：{'，'.join(cert.domains)}"
            )
            return
        self._cert_path, self._cert = cert_path, cert
        self._key_path, self._key = key_path, key
        self._show()

    def _show(self) -> None:
        """把解析结果填进表单并放行保存"""
        cert, key = self._cert, self._key
        left = certs.days_left(cert.not_after.isoformat())
        self.domains_label.setText("\n".join(cert.domains))
        self.period_label.setText(f"{cert.not_before} → {cert.not_after}")
        self.left_label.setText(certs.status_text(left))
        self.left_label.setStyleSheet(f"color:{certs.status_color(left) or '#1f2937'};")
        self.key_label.setText(key.kind)
        self.files_label.setText(f"{self._cert_path.name} ／ {self._key_path.name}")
        self.drop_area.setText(f"已识别 {cert.name}")
        self.hint.setText(
            "确认无误后点「保存」，两个文件会覆盖原证书"
            if self._target
            else "确认无误后点「保存」，两个文件会复制到证书库"
        )
        self.hint.setStyleSheet("color:#6b7280;")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)

    def _fail(self, reason: str) -> None:
        """清空上次的结果并提示原因"""
        self._cert_path = self._key_path = None
        self._cert = self._key = None
        for label in (self.domains_label, self.period_label, self.left_label, self.key_label, self.files_label):
            label.clear()
        self.drop_area.setText(self._drop_hint)
        self.hint.setText(reason)
        self.hint.setStyleSheet("color:#d9534f;")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

    def _browse(self) -> None:
        """多选文件后走与拖拽相同的识别流程"""
        paths, _ = QFileDialog.getOpenFileNames(self, "选择证书与私钥文件")
        if paths:
            self._accept_files([Path(path) for path in paths])

    def _save(self) -> None:
        """新增按主域名与短 id 归档 替换沿用原 id 与文件名覆盖归档"""
        cert = Certificate(
            name=self._cert.name,
            domains=self._cert.domains,
            not_before=self._cert.not_before.isoformat(),
            not_after=self._cert.not_after.isoformat(),
            key_type=self._key.kind,
        )
        try:
            if self._target:
                cert.id = self._target.id
                cert.cert_file, cert.key_file = self._target.cert_file, self._target.key_file
                self._store.replace_certificate(cert, self._cert_path, self._key_path)
            else:
                self._store.add_certificate(cert, self._cert_path, self._key_path)
        except OSError as error:
            QMessageBox.warning(self, "保存失败", f"复制证书文件失败：{error}")
            return
        self.accept()

    @staticmethod
    def _value_label() -> QLabel:
        """结果展示用的只读标签 支持选中复制"""
        label = QLabel()
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label
