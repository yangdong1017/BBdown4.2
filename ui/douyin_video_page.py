from __future__ import annotations

from pathlib import Path
import logging

from PyQt5.QtCore import QTimer, QUrl, Qt
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    MessageBox,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    SegmentedWidget,
    TextEdit,
    TitleLabel,
)

from core.config import (
    DOUYIN_AUDIO_DOWNLOAD,
    DOUYIN_VIDEO_DOWNLOAD,
    DOUYIN_VIDEO_CONCURRENCY_OPTIONS,
    RUNTIME_DIR,
    load_douyin_video_config,
    save_douyin_video_config,
)
from core.douyin_audio_urls import DOUYIN_STANDARD_AUDIO_LINK, extract_douyin_audio_links
from core.douyin_media import DouyinMediaLink
from core.douyin_video_urls import extract_douyin_video_links
from core.douyin_video_worker import DouyinMediaWorkerThread
from core.douyin_video_downloader import DouyinMediaDownloadResult
from core.download_renaming import DownloadLibrary, DownloadFile, RenameError
from core.models import DouyinVideoBatchResult
from .collapsible_panel import CollapsibleRightPanel
from .platform_utils import open_directory
from .task_table import TaskTable
from .window_title import set_task_title
from .widgets import CardFrame, TEXT_EDIT_STYLE
from .batch_rename_dialog import BatchRenameDialog
from .rename_toolbar import RenameToolbar


DOUYIN_STANDARD_VIDEO_LINK = (
    "https://aweme.snssdk.com/aweme/v1/play/"
    "?video_id=v0200fg10000d2t5cmnog65rqiip1p90"
)


class DouyinVideoPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("douyinVideoDownload")
        self.config = load_douyin_video_config()
        self.worker: DouyinMediaWorkerThread | None = None
        self.row_by_media_id: dict[str, int] = {}
        self.current_download_type = self.config.download_type
        self.library = DownloadLibrary(RUNTIME_DIR / "douyin_downloads.json")
        self.download_files: list[DownloadFile] = []
        self.rename_undo: list[tuple[str, str, str, str]] = []
        self._batch_running = False
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(350)
        self.save_timer.timeout.connect(self._save_config)
        self._build_ui()
        self._apply_state()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)
        root.addLayout(self._build_title_row())
        root.addLayout(self._build_download_options_row())

        split = QHBoxLayout()
        split.setSpacing(6)
        root.addLayout(split, 1)

        self.left_panel = QWidget(self)
        left = QVBoxLayout(self.left_panel)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(14)
        self.input_card = self._build_input_card()
        left.addWidget(self.input_card)
        left.addWidget(self._build_task_table(), 1)
        split.addWidget(self.left_panel, 1)

        self.right_panel = CollapsibleRightPanel(self)
        self.download_settings_card = self._build_download_settings_card()
        self.right_panel.content_layout.addWidget(self.download_settings_card)
        self.right_panel.content_layout.addStretch(1)
        split.addWidget(self.right_panel)

        self.status_label = CaptionLabel("就绪", self)
        root.addWidget(self.status_label)

    def _build_title_row(self) -> QHBoxLayout:
        title_row = QHBoxLayout()
        title_row.addWidget(TitleLabel("抖音下载", self))
        title_row.addStretch(1)
        self.count_label = CaptionLabel("检测到 0 条可下载链接", self)
        title_row.addWidget(self.count_label)
        return title_row

    def _build_download_options_row(self) -> QHBoxLayout:
        options_row = QHBoxLayout()
        options_row.setSpacing(10)

        self.download_type_segment = SegmentedWidget(self)
        self.download_type_segment.addItem(
            DOUYIN_VIDEO_DOWNLOAD,
            "下载视频",
            lambda: self._on_download_type_changed(DOUYIN_VIDEO_DOWNLOAD),
        )
        self.download_type_segment.addItem(
            DOUYIN_AUDIO_DOWNLOAD,
            "下载音频",
            lambda: self._on_download_type_changed(DOUYIN_AUDIO_DOWNLOAD),
        )
        self.download_type_segment.setCurrentItem(self.current_download_type)
        self.download_type_segment.setFixedWidth(200)
        audio_item = self.download_type_segment.items[DOUYIN_AUDIO_DOWNLOAD]
        audio_item.setToolTip("下载抖音音频直链")
        options_row.addWidget(self.download_type_segment)
        options_row.addStretch(1)
        return options_row

    def _build_input_card(self) -> CardFrame:
        input_card = CardFrame(self)
        input_layout = QVBoxLayout(input_card)
        input_layout.setContentsMargins(18, 9, 18, 18)
        input_layout.setSpacing(5)

        video_link_row = QHBoxLayout()
        video_link_row.setSpacing(10)
        self.standard_link_label = CaptionLabel(
            f"标准视频链接：{DOUYIN_STANDARD_VIDEO_LINK}",
            input_card,
        )
        self.standard_link_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.standard_link_label.setToolTip(DOUYIN_STANDARD_VIDEO_LINK)
        self.standard_link_label.setWordWrap(True)
        self.standard_link_actions = SegmentedWidget(input_card)
        self.standard_link_actions.addItem("copy", "复制", self._copy_standard_link)
        self.standard_link_actions.addItem("open", "打开", self._open_standard_link)
        self.standard_link_actions.setFixedWidth(150)
        video_link_row.addWidget(self.standard_link_label, 1)
        video_link_row.addWidget(self.standard_link_actions)
        input_layout.addLayout(video_link_row)

        self.url_edit = TextEdit(input_card)
        self.url_edit.setMinimumHeight(145)
        self.url_edit.setMaximumHeight(210)
        self.url_edit.setStyleSheet(TEXT_EDIT_STYLE)
        self.url_edit.textChanged.connect(self._on_text_changed)
        input_layout.addWidget(self.url_edit)
        input_layout.addLayout(self._build_progress_row(input_card))
        input_layout.addLayout(self._build_action_row(input_card))
        return input_card

    def _build_download_settings_card(self) -> CardFrame:
        settings_card = CardFrame(self)
        settings_layout = QVBoxLayout(settings_card)
        settings_layout.setContentsMargins(18, 18, 18, 18)
        settings_layout.setSpacing(12)
        settings_layout.addWidget(BodyLabel("下载设置", settings_card))

        dir_row = QHBoxLayout()
        self.choose_dir_btn = PushButton("选择保存目录", settings_card)
        self.choose_dir_btn.clicked.connect(self._choose_dir)
        self.open_dir_btn = PushButton("打开目录", settings_card)
        self.open_dir_btn.clicked.connect(self._open_save_dir)
        dir_row.addWidget(self.choose_dir_btn)
        dir_row.addWidget(self.open_dir_btn)
        settings_layout.addLayout(dir_row)

        self.dir_label = CaptionLabel(settings_card)
        self.dir_label.setWordWrap(True)
        settings_layout.addWidget(self.dir_label)

        concurrency_row = QHBoxLayout()
        concurrency_row.addWidget(BodyLabel("并发", settings_card))
        self.concurrency_combo = ComboBox(settings_card)
        self.concurrency_combo.addItems([str(value) for value in DOUYIN_VIDEO_CONCURRENCY_OPTIONS])
        self.concurrency_combo.currentTextChanged.connect(self._on_concurrency_changed)
        concurrency_row.addWidget(self.concurrency_combo)
        concurrency_row.addStretch(1)
        settings_layout.addLayout(concurrency_row)
        return settings_card

    def _build_progress_row(self, parent: QWidget) -> QHBoxLayout:
        progress_row = QHBoxLayout()
        self.progress_bar = ProgressBar(parent)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_label = CaptionLabel("进度 0/0 | 进行中 0", parent)
        progress_row.addWidget(self.progress_bar, 1)
        progress_row.addWidget(self.progress_label)
        return progress_row

    def _build_action_row(self, parent: QWidget) -> QHBoxLayout:
        action_row = QHBoxLayout()
        self.start_btn = PrimaryPushButton("开始下载", parent)
        self.start_btn.clicked.connect(self._start_download)
        self.stop_btn = PushButton("停止任务", parent)
        self.stop_btn.clicked.connect(self.stop)
        self.clear_btn = PushButton("清空链接", parent)
        self.clear_btn.clicked.connect(self._clear_links)
        action_row.addWidget(self.start_btn)
        action_row.addWidget(self.stop_btn)
        action_row.addWidget(self.clear_btn)
        action_row.addStretch(1)
        return action_row

    def _build_task_table(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.rename_bar = RenameToolbar("下载结果", panel)
        self.result_count_label = self.rename_bar.count_label
        self.undo_rename_btn = self.rename_bar.undo_btn
        self.undo_rename_btn.clicked.connect(self._undo_rename)
        self.rename_btn = self.rename_bar.rename_btn
        self.rename_btn.clicked.connect(self._rename_downloads)
        layout.addWidget(self.rename_bar)
        self.table = TaskTable(panel, id_header="文件名")
        self.table.setSortingEnabled(False)
        self.table.itemSelectionChanged.connect(self._refresh_rename_controls)
        layout.addWidget(self.table, 1)
        return panel

    def _apply_state(self) -> None:
        self.download_type_segment.setCurrentItem(self.current_download_type)
        self.url_edit.setPlainText(self._active_urls_text())
        self.concurrency_combo.setCurrentText(str(self.config.concurrency))
        self._refresh_dir_label()
        self._refresh_download_type_ui()
        self._set_running_state(False)
        self._refresh_link_count()
        self._restore_download_results()

    def _on_download_type_changed(self, download_type: str) -> None:
        if self._batch_running:
            return
        self._store_active_urls()
        self.current_download_type = download_type
        self.config.download_type = download_type
        self.download_type_segment.setCurrentItem(download_type)
        self.url_edit.blockSignals(True)
        self.url_edit.setPlainText(self._active_urls_text())
        self.url_edit.blockSignals(False)
        self.rename_undo = []
        self._restore_download_results()
        self._on_batch_progress(0, 0, 0)
        self._refresh_download_type_ui()
        self._refresh_link_count()
        self.status_label.setText("就绪")
        self.save_timer.start()

    def _refresh_download_type_ui(self) -> None:
        is_video = self.current_download_type == DOUYIN_VIDEO_DOWNLOAD
        if is_video:
            self.standard_link_label.setText(f"标准视频链接：{DOUYIN_STANDARD_VIDEO_LINK}")
            self.standard_link_label.setToolTip(DOUYIN_STANDARD_VIDEO_LINK)
            self.start_btn.setText("开始下载视频")
        else:
            self.standard_link_label.setText(f"标准音频链接：{DOUYIN_STANDARD_AUDIO_LINK}")
            self.standard_link_label.setToolTip(DOUYIN_STANDARD_AUDIO_LINK)
            self.start_btn.setText("开始下载音频")
        self.standard_link_actions.setVisible(True)
        if hasattr(self, "table"):
            self.table.set_id_header("文件名")
        if not self.is_running():
            self.start_btn.setEnabled(True)

    def _on_text_changed(self) -> None:
        self._refresh_link_count()
        self.save_timer.start()

    def _refresh_link_count(self) -> None:
        count = len(self._current_links())
        media_name = "视频" if self.current_download_type == DOUYIN_VIDEO_DOWNLOAD else "音频"
        self.count_label.setText(f"检测到 {count} 条可下载{media_name}链接")

    def _on_concurrency_changed(self, value: str) -> None:
        try:
            self.config.concurrency = int(value)
        except ValueError:
            return
        self.save_timer.start()

    def _save_config(self) -> None:
        self.save_timer.stop()
        self._store_active_urls()
        self.config.download_type = self.current_download_type
        save_douyin_video_config(self.config)

    def flush_pending_save(self) -> None:
        """Persist a debounced edit right away, e.g. before closing the window."""
        if self.save_timer.isActive():
            self._save_config()

    def _store_active_urls(self) -> None:
        text = self.url_edit.toPlainText().strip()
        if self.current_download_type == DOUYIN_AUDIO_DOWNLOAD:
            self.config.audio_urls = text
        else:
            self.config.urls = text

    def _active_urls_text(self) -> str:
        if self.current_download_type == DOUYIN_AUDIO_DOWNLOAD:
            return self.config.audio_urls
        return self.config.urls

    def _current_links(self) -> list[DouyinMediaLink]:
        text = self.url_edit.toPlainText()
        if self.current_download_type == DOUYIN_AUDIO_DOWNLOAD:
            return list(extract_douyin_audio_links(text))
        return list(extract_douyin_video_links(text))

    def _current_standard_link(self) -> str:
        if self.current_download_type == DOUYIN_AUDIO_DOWNLOAD:
            return DOUYIN_STANDARD_AUDIO_LINK
        return DOUYIN_STANDARD_VIDEO_LINK

    def _choose_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "选择保存目录",
            self.config.save_dir or str(Path.home() / "Downloads"),
        )
        if not directory:
            return
        self.config.save_dir = directory
        self._refresh_dir_label()
        self._save_config()

    def _refresh_dir_label(self) -> None:
        self.dir_label.setText(f"保存到：{self.config.save_dir}")

    def _open_save_dir(self) -> None:
        path = Path(self.config.save_dir)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            MessageBox("提示", "保存目录不可用，请重新选择。", self.window()).exec()
            return
        if not open_directory(path):
            MessageBox("提示", "保存目录不可用，请重新选择。", self.window()).exec()

    def _copy_standard_link(self) -> None:
        QApplication.clipboard().setText(self._current_standard_link())
        self.status_label.setText("标准链接已复制，可以粘贴使用。")

    def _open_standard_link(self) -> None:
        if QDesktopServices.openUrl(QUrl(self._current_standard_link())):
            return
        MessageBox("提示", "无法打开链接，请检查系统默认浏览器设置。", self.window()).exec()

    def _clear_links(self) -> None:
        if self.is_running():
            MessageBox("提示", "任务正在运行，请先停止任务。", self.window()).exec()
            return
        self.url_edit.clear()
        self.table.setRowCount(0)
        self.row_by_media_id.clear()
        self.download_files = []
        self.library.latest[self.current_download_type] = []
        self.rename_undo = []
        self._save_download_history()
        self._refresh_rename_controls()
        self._on_batch_progress(0, 0, 0)
        self.status_label.setText("就绪")

    def _start_download(self) -> None:
        if self.is_running():
            return
        links = self._current_links()
        if not links:
            media_name = "视频" if self.current_download_type == DOUYIN_VIDEO_DOWNLOAD else "音频"
            MessageBox("提示", f"没有检测到可下载的抖音{media_name}直链。", self.window()).exec()
            return

        save_dir = Path(self.config.save_dir)
        try:
            save_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            MessageBox("提示", "保存目录不可用，请重新选择。", self.window()).exec()
            return

        self._populate_tasks(links)
        self.rename_undo = []
        self._save_download_history()
        self._save_config()
        self._on_batch_progress(0, len(links), 0)

        self.worker = DouyinMediaWorkerThread(
            links=links,
            save_dir=str(save_dir),
            concurrency=self.config.concurrency,
            parent=self,
            existing_files={record.task_id: (record.path, record.size, record.mtime_ns) for record in self.download_files
                            if record.matches_file()},
        )
        self.worker.status.connect(self.status_label.setText)
        self.worker.task_progress.connect(self._on_task_progress_batch)
        self.worker.task_status.connect(self._on_task_status)
        self.worker.task_result.connect(self._on_task_result)
        self.worker.batch_progress.connect(self._on_batch_progress)
        self.worker.finished_all.connect(self._on_finished)
        self._set_running_state(True)
        self.worker.start()

    def _populate_tasks(self, links: list[DouyinMediaLink]) -> None:
        self.download_files = self.library.register(links, self.current_download_type, Path(self.config.save_dir))
        for record in self.download_files:
            record.status, record.detail = "等待中", ""
        self.row_by_media_id = {link.task_id: row for row, link in enumerate(links)}
        self.table.populate([Path(record.path).name for record in self.download_files])
        self._refresh_result_names()
        self._refresh_rename_controls()

    def _on_task_progress_batch(self, updates: dict[str, tuple[int, int]]) -> None:
        for task_id, (downloaded, total) in updates.items():
            self._on_task_progress(task_id, downloaded, total)

    def _on_task_progress(self, task_id: str, downloaded: int, total: int) -> None:
        row = self.row_by_media_id.get(task_id)
        if row is None:
            return
        if total > 0:
            self.table.set_percent(row, int(downloaded * 100 / total))
        else:
            # Some CDN responses have no Content-Length, so show what arrived.
            self.table.set_progress_text(row, f"{downloaded / 1024 / 1024:.1f} MB")

    def _on_task_status(self, task_id: str, status: str, detail: str) -> None:
        row = self.row_by_media_id.get(task_id)
        if row is None:
            return
        if row < len(self.download_files):
            self.download_files[row].status = status
            self.download_files[row].detail = detail if status == "失败" else ""
        self.table.set_status(row, status, detail)

    def _on_task_result(self, result: DouyinMediaDownloadResult) -> None:
        row = self.row_by_media_id.get(result.link.task_id)
        if row is None or result.status not in {"completed", "exists"}:
            return
        record = self.download_files[row]
        record.path = str(Path(result.output_path).absolute())
        try:
            record.remember_file()
        except OSError:
            record.size, record.mtime_ns = 0, 0
        self._refresh_result_names()

    def _on_batch_progress(self, processed: int, total: int, active: int) -> None:
        percent = int(processed * 100 / total) if total else 0
        self.progress_bar.setValue(percent)
        self.progress_label.setText(f"进度 {processed}/{total} | {percent}% | 进行中 {active}")
        if total:
            set_task_title(self, f"抖音下载 {processed}/{total}")

    def _on_finished(self, result: object) -> None:
        assert isinstance(result, DouyinVideoBatchResult)
        self.worker = None
        set_task_title(self)
        if result.stopped:
            summary = (
                f"任务已停止：完成 {result.completed}，已存在 {result.skipped}，"
                f"失败 {result.failed}，停止 {result.stopped_count}。"
            )
        else:
            summary = f"下载结束：成功 {result.completed}，已存在 {result.skipped}，失败 {result.failed}。"
        ready = [record for record in self.download_files
                 if record.pending_name and record.status in {"已完成", "已存在"}]
        if ready:
            try:
                renamed = self.library.apply(self.library.plan(ready, [record.pending_name for record in ready]))
                summary += " " + renamed.summary()
                if renamed.errors:
                    logging.getLogger("bbdown").warning("自动重命名失败：%s", renamed.errors)
            except (OSError, RenameError):
                summary += " 标题暂未应用，请使用批量重命名重试。"
        self._refresh_result_names()
        if not self._save_download_history():
            summary += " 下载记录保存失败，请检查软件目录权限。"
        self.status_label.setText(summary)
        self._set_running_state(False)

    def _set_running_state(self, running: bool) -> None:
        self._batch_running = running
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.stop_btn.setText("停止任务")
        self.url_edit.setReadOnly(running)
        self.choose_dir_btn.setEnabled(not running)
        self.concurrency_combo.setEnabled(not running)
        self.download_type_segment.setEnabled(not running)
        self.clear_btn.setEnabled(not running)
        self._refresh_rename_controls()

    def _save_download_history(self) -> bool:
        try:
            self.library.save()
            return True
        except OSError:
            logging.getLogger("bbdown").exception("下载记录保存失败")
            return False

    def _restore_download_results(self) -> None:
        self.download_files = self.library.current(self.current_download_type)
        self.row_by_media_id = {record.task_id: row for row, record in enumerate(self.download_files)}
        self.table.populate([Path(record.path).name for record in self.download_files])
        for row, record in enumerate(self.download_files):
            self.table.set_status(row, record.status, record.detail)
        self._refresh_result_names()
        self._refresh_rename_controls()

    def _refresh_result_names(self) -> None:
        for row, record in enumerate(self.download_files):
            item = self.table.item(row, 0)
            if item is None:
                continue
            item.setText(Path(record.path).name)
            tooltip = f"{record.path}\nID：{record.task_id}"
            if record.pending_name:
                tooltip += f"\n待应用标题：{record.pending_name}"
            item.setToolTip(tooltip)
            item.setData(Qt.UserRole, record.path)

    def _refresh_rename_controls(self) -> None:
        if not hasattr(self, "table"):
            return
        selected = len(self.table.selectionModel().selectedRows())
        self.rename_bar.update_state(len(self.download_files), selected, enabled=bool(self.download_files),
                                     can_undo=bool(self.rename_undo), running=self._batch_running)

    def _rename_downloads(self) -> None:
        if self._batch_running or not self.download_files:
            return
        rows = sorted(index.row() for index in self.table.selectionModel().selectedRows())
        records = [self.download_files[row] for row in rows] if rows else list(self.download_files)
        dialog = BatchRenameDialog(self.library, records, self.window())
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            result = self.library.apply(self.library.plan(records, dialog.titles))
            self.rename_undo = result.undo
            self.status_label.setText(result.summary())
            if result.errors:
                MessageBox("部分文件未改名", "\n".join(result.errors[:5]), self.window()).exec()
        except (OSError, RenameError) as exc:
            logging.getLogger("bbdown").exception("批量重命名失败")
            message = str(exc) if isinstance(exc, RenameError) else "下载记录保存失败，请检查软件目录权限。"
            MessageBox("重命名未完成", message, self.window()).exec()
        self._refresh_result_names()
        self._refresh_rename_controls()

    def _undo_rename(self) -> None:
        if self._batch_running or not self.rename_undo:
            return
        try:
            result = self.library.undo(self.rename_undo)
            self.rename_undo = result.undo
            self.status_label.setText("已撤销上次重命名。" if not result.errors else "部分文件未能恢复原名。")
            if result.errors:
                MessageBox("部分文件未恢复", "\n".join(result.errors[:5]), self.window()).exec()
        except OSError:
            MessageBox("撤销未完成", "下载记录保存失败，请检查软件目录权限。", self.window()).exec()
        self._refresh_result_names()
        self._refresh_rename_controls()

    def stop(self) -> None:
        if not self.is_running() or self.worker is None:
            return
        self.stop_btn.setEnabled(False)
        self.stop_btn.setText("正在停止")
        self.worker.stop()

    def is_running(self) -> bool:
        return bool(self.worker and self.worker.isRunning())
