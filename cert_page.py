"""证书页 列表展示已归档的证书及其到期状态"""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel, QListWidgetItem, QMenu, QMessageBox, QVBoxLayout, QWidget

import certs
from card_view import ROLE_BADGE, ROLE_BADGE_COLOR, ROLE_EXTRA, ROLE_ICON, ROLE_KEY, ROLE_SUBTITLE, ROLE_TITLE, make_card_view
from cert_dialog import CertDialog
from store import Certificate, Store


def cert_tooltip(cert: Certificate, days: int) -> str:
    """悬停时列出卡片上放不下的信息"""
    rows = [cert.name, f"有效期 {cert.not_before} → {cert.not_after}（{certs.status_text(days)}）"]
    if len(cert.domains) > 1:
        rows.append("全部域名 " + "，".join(cert.domains))
    rows.append(f"私钥 {cert.key_type}")
    rows.append(f"证书 {cert.cert_file}")
    rows.append(f"私钥 {cert.key_file}")
    return "\n".join(rows)


class CertPage(QWidget):
    """证书列表页 右键空白处添加证书 右键卡片可编辑 打开位置或删除"""

    def __init__(self, store: Store, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store

        hint = QLabel("右键空白处添加证书；右键某张证书可编辑、打开所在文件夹或删除")
        hint.setStyleSheet("color:#6b7280;padding:8px 12px;")

        self.view = make_card_view()
        self.view.customContextMenuRequested.connect(self._context_menu)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(hint)
        layout.addWidget(self.view, 1)

        self.refresh()

    def showEvent(self, event) -> None:
        """每次进入页面重算剩余天数 避免跨天后还显示旧数字"""
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        """重建卡片 剩余天数每次按当天重算"""
        self.view.clear()
        for cert in self.store.certificates:
            days = certs.days_left(cert.not_after)
            item = QListWidgetItem()
            item.setData(ROLE_ICON, "cert")
            item.setData(ROLE_TITLE, cert.name)
            item.setData(ROLE_SUBTITLE, f"到期 {cert.not_after} · {certs.status_text(days)}")
            item.setData(ROLE_EXTRA, cert.cert_file)
            item.setData(ROLE_BADGE, certs.status_text(days))
            item.setData(ROLE_BADGE_COLOR, certs.status_color(days))
            item.setData(ROLE_KEY, cert.id)
            item.setToolTip(cert_tooltip(cert, days))
            self.view.addItem(item)

    def _context_menu(self, pos) -> None:
        """按点击位置弹出菜单 命中卡片与命中空白处菜单不同"""
        index = self.view.indexAt(pos)
        cert = self._cert_at(index)
        menu = QMenu(self)
        if cert is None:
            self.view.clearSelection()
            menu.addAction("添加证书").setData("new")
            menu.addAction("刷新").setData("refresh")
        else:
            self.view.setCurrentIndex(index)
            menu.addAction("编辑").setData("edit")
            menu.addAction("打开所在文件夹").setData("open")
            menu.addSeparator()
            menu.addAction("删除").setData("delete")

        action = menu.exec(self.view.viewport().mapToGlobal(pos))
        if action is None:
            return
        match action.data():
            case "new":
                self._create()
            case "refresh":
                self.refresh()
            case "edit":
                self._edit(cert)
            case "open":
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.certs_dir)))
            case "delete":
                self._delete(cert)

    def _cert_at(self, index) -> Certificate | None:
        """按卡片索引取证书 空白处返回 None"""
        return self.store.certificate(index.data(ROLE_KEY)) if index.isValid() else None

    def _create(self) -> None:
        """添加证书 弹框内已完成解析与归档"""
        dialog = CertDialog(self.store, self)
        if dialog.exec() == CertDialog.DialogCode.Accepted:
            self.refresh()

    def _edit(self, cert: Certificate) -> None:
        """替换证书 复用新增弹框 域名须与原证书一致"""
        dialog = CertDialog(self.store, self, target=cert)
        if dialog.exec() == CertDialog.DialogCode.Accepted:
            self.refresh()

    def _delete(self, cert: Certificate) -> None:
        """二次确认后删除记录与归档文件"""
        answer = QMessageBox.question(
            self,
            "删除确认",
            f"确定删除证书「{cert.name}」吗？\n{cert.cert_file} 与 {cert.key_file} 会一并从证书库删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.store.remove_certificate(cert.id)
        self.refresh()
