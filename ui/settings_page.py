from __future__ import annotations

from PyQt5.QtCore import QThread, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QHBoxLayout, QLineEdit, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, LineEdit, PrimaryPushButton, PushButton, TitleLabel

from core.config import load_doubao_api_key, save_doubao_api_key
from core.doubao_asr import test_doubao_api_key
from .widgets import CardFrame


VOLCENGINE_URL = "https://www.volcengine.com/"
API_KEY_TUTORIAL_URL = "https://rcnyou54a8x8.feishu.cn/docx/RjwndtvZXoZ5Orx2LiRcfISfnMf"


class DoubaoApiTestThread(QThread):
    checked = pyqtSignal(bool)

    def __init__(self, api_key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api_key = api_key

    def run(self) -> None:
        self.checked.emit(test_doubao_api_key(self.api_key))


class SettingsPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settings")
        self.test_thread: DoubaoApiTestThread | None = None
        self._build_ui()
        self._load_state()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        layout.addWidget(TitleLabel("设置", self))

        card = CardFrame(self)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(12)

        card_layout.addWidget(BodyLabel("豆包转文字 API", card))

        self.api_key_edit = LineEdit(card)
        self.api_key_edit.setPlaceholderText("粘贴你的火山引擎 API Key")
        self.api_key_edit.setClearButtonEnabled(True)
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        card_layout.addWidget(self.api_key_edit)

        link_row = QHBoxLayout()
        link_row.addWidget(CaptionLabel(f"API Key 获取地址：{VOLCENGINE_URL}", card), 1)
        self.open_volc_btn = PushButton("打开", card)
        self.open_volc_btn.clicked.connect(self._open_volcengine)
        link_row.addWidget(self.open_volc_btn)
        card_layout.addLayout(link_row)

        tutorial_row = QHBoxLayout()
        tutorial_row.addWidget(CaptionLabel(f"API Key 获取教程：{API_KEY_TUTORIAL_URL}", card), 1)
        self.open_tutorial_btn = PushButton("打开", card)
        self.open_tutorial_btn.clicked.connect(self._open_api_key_tutorial)
        tutorial_row.addWidget(self.open_tutorial_btn)
        card_layout.addLayout(tutorial_row)

        action_row = QHBoxLayout()
        self.save_btn = PrimaryPushButton("保存", card)
        self.save_btn.clicked.connect(self._save)
        self.test_btn = PushButton("测试连接", card)
        self.test_btn.clicked.connect(self._test_connection)
        self.clear_btn = PushButton("清空", card)
        self.clear_btn.clicked.connect(self._clear)
        action_row.addWidget(self.save_btn)
        action_row.addWidget(self.test_btn)
        action_row.addWidget(self.clear_btn)
        action_row.addStretch(1)
        card_layout.addLayout(action_row)

        self.status_label = CaptionLabel("", card)
        self.status_label.setStyleSheet("color: #a7a7a7;")
        card_layout.addWidget(self.status_label)

        layout.addWidget(card)
        layout.addStretch(1)

    def _load_state(self) -> None:
        api_key = load_doubao_api_key()
        if api_key:
            self.api_key_edit.setText(api_key)
            self._set_status("已保存", ok=True)
        else:
            self._set_status("未填写", ok=False)

    def _save(self) -> None:
        api_key = self.api_key_edit.text().strip()
        save_doubao_api_key(api_key)
        self._set_status("保存成功" if api_key else "已清空", ok=bool(api_key))

    def _test_connection(self) -> None:
        api_key = self.api_key_edit.text().strip()
        if not api_key:
            self._set_status("测试失败", ok=False)
            return

        self._set_busy(True)
        self._set_status("测试中", ok=True)
        self.test_thread = DoubaoApiTestThread(api_key, self)
        self.test_thread.checked.connect(self._on_test_finished)
        self.test_thread.finished.connect(self.test_thread.deleteLater)
        self.test_thread.start()

    def _on_test_finished(self, ok: bool) -> None:
        self._set_busy(False)
        if ok:
            save_doubao_api_key(self.api_key_edit.text().strip())
            self._set_status("测试成功", ok=True)
        else:
            self._set_status("测试失败", ok=False)
        self.test_thread = None

    def _clear(self) -> None:
        self.api_key_edit.clear()
        save_doubao_api_key("")
        self._set_status("已清空", ok=False)

    def _open_volcengine(self) -> None:
        QDesktopServices.openUrl(QUrl(VOLCENGINE_URL))

    def _open_api_key_tutorial(self) -> None:
        QDesktopServices.openUrl(QUrl(API_KEY_TUTORIAL_URL))

    def _set_busy(self, busy: bool) -> None:
        self.api_key_edit.setEnabled(not busy)
        self.save_btn.setEnabled(not busy)
        self.test_btn.setEnabled(not busy)
        self.clear_btn.setEnabled(not busy)
        self.open_volc_btn.setEnabled(not busy)
        self.open_tutorial_btn.setEnabled(not busy)

    def _set_status(self, text: str, *, ok: bool) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {'#7fd26f' if ok else '#ff6a5c'};")
