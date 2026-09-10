from __future__ import annotations

from PyQt5.QtWidgets import QHBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, PushButton


class RenameToolbar(QWidget):
    """The same compact result actions for downloads and transcripts."""
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title = title
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.count_label = CaptionLabel(title, self)
        layout.addWidget(self.count_label)
        layout.addStretch(1)
        self.undo_btn = PushButton("撤销重命名", self)
        self.undo_btn.hide()
        self.rename_btn = PushButton("批量重命名", self)
        self.rename_btn.setEnabled(False)
        layout.addWidget(self.undo_btn)
        layout.addWidget(self.rename_btn)

    def update_state(self, count: int, selected: int, *, enabled: bool, can_undo: bool, running: bool) -> None:
        self.count_label.setText(f"{self.title} · {count} 项" + (f" · 已选 {selected} 项" if selected else ""))
        self.rename_btn.setEnabled(enabled and not running)
        self.undo_btn.setVisible(can_undo)
        self.undo_btn.setEnabled(not running)
