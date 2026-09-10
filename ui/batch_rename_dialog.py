from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout
from qfluentwidgets import CaptionLabel, PrimaryPushButton, PushButton, TextEdit, TitleLabel

from core.download_renaming import DownloadFile, DownloadLibrary, RenameError, parse_titles
from .widgets import TEXT_EDIT_STYLE


class BatchRenameDialog(QDialog):
    """One paste box, one apply action; no per-row editing or demo data."""
    def __init__(self, library: DownloadLibrary, records: list[DownloadFile], parent=None,
                 *, completion_action: str = "下载成功") -> None:
        super().__init__(parent)
        self.library = library
        self.records = records
        self.completion_action = completion_action
        self.titles: list[str] = []
        self.setWindowTitle("批量重命名")
        self.setWindowModality(Qt.WindowModal)
        self.resize(580, 440)
        self.setMinimumSize(500, 380)
        self.setStyleSheet("QDialog { background: #202020; color: #f5f7fa; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(TitleLabel("批量重命名", self))
        description = CaptionLabel(
            f"为 {len(records)} 项粘贴标题，按列表顺序对应。失败项保留位置，空白行保留原名。", self)
        description.setWordWrap(True)
        layout.addWidget(description)
        self.title_edit = TextEdit(self)
        self.title_edit.setAcceptRichText(False)
        self.title_edit.setPlaceholderText("把 Excel 中的标题列粘贴到这里，一行一个。")
        self.title_edit.setStyleSheet(TEXT_EDIT_STYLE)
        layout.addWidget(self.title_edit, 1)
        self.count_label = CaptionLabel(self)
        self.count_label.setWordWrap(True)
        layout.addWidget(self.count_label)
        self.summary_label = CaptionLabel(self)
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_btn = PushButton("取消", self)
        self.cancel_btn.clicked.connect(self.reject)
        self.apply_btn = PrimaryPushButton("应用重命名", self)
        self.apply_btn.clicked.connect(self._accept_titles)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.apply_btn)
        layout.addLayout(buttons)
        self.title_edit.textChanged.connect(self._refresh)
        self._refresh()

    def _refresh(self) -> None:
        self.apply_btn.setEnabled(False)
        self.summary_label.clear()
        self.count_label.setStyleSheet("color: #a7a7a7;")
        try:
            self.titles = parse_titles(self.title_edit.toPlainText(), len(self.records))
            self.count_label.setText(f"{len(self.titles)} / {len(self.records)} 行")
            if not self.titles:
                return
            items = self.library.plan(self.records, self.titles)
            pending = sum(item.pending for item in items)
            summary = f"将重命名 {len(items) - pending} 个文件"
            if pending:
                summary += f"，{pending} 个标题等待{self.completion_action}后应用"
            adjusted = []
            for item in items:
                original = self.titles[self.records.index(item.record)].strip()
                if item.target.name not in {original, original + item.target.suffix}:
                    adjusted.append(item.target.name)
            if adjusted:
                summary += "\n已处理重名或非法字符：" + "；".join(adjusted[:2])
                if len(adjusted) > 2:
                    summary += f" 等 {len(adjusted)} 项"
            self.summary_label.setText(summary)
            self.apply_btn.setEnabled(bool(items))
        except RenameError as exc:
            self.count_label.setText(str(exc))
            self.count_label.setStyleSheet("color: #ff6a5c;")

    def _accept_titles(self) -> None:
        self._refresh()
        if self.apply_btn.isEnabled():
            self.accept()
