from __future__ import annotations

from pathlib import Path
import logging

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QDialog, QFileDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, ComboBox, MessageBox, PrimaryPushButton, PushButton, TitleLabel
from qfluentwidgets import ProgressBar

from core.config import (
    ASR_CONCURRENCY_OPTIONS,
    ASR_ENGINE_OPTIONS,
    ASR_FORMAT_OPTIONS,
    ASR_MODE_OPTIONS,
    DOUBAO_ENGINE_NAME,
    DOUBAO_ASR_CONCURRENCY_OPTIONS,
    RUNTIME_DIR,
    load_doubao_api_key,
    load_app_config,
    update_app_config,
)
from core.asr_file_worker import ASRWorkerThread
from core.asr_task import ASRTaskResult
from core.asr_renaming import TranscriptLibrary
from core.download_renaming import DownloadFile, RenameError, path_key
from core.toolchain import resolve_toolchain
from core.url_asr_worker import UrlASRBatchResult, UrlASRWorkerThread, default_url_output_dir
from core.url_audio import extract_audio_urls, extract_douyin_share_urls
from .asr_inputs import LocalFileInput, UrlInput
from .platform_utils import open_directory
from .window_title import set_task_title
from .batch_rename_dialog import BatchRenameDialog

DOUYIN_ASR_MODE = "抖音音频链接转文字"
FILE_KIND = "asr_file"
URL_KIND = "asr_url"
OUTPUT_RECORD_ROLE = Qt.UserRole + 1


class ASRPage(QWidget):
    request_download_dir = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("asr")
        self.config = load_app_config()
        self.toolchain = resolve_toolchain()
        self.worker: ASRWorkerThread | None = None
        self.url_worker: UrlASRWorkerThread | None = None
        self.row_index_map: list[int] = []
        self.url_row_index_map: list[int] = []
        self.library = TranscriptLibrary(RUNTIME_DIR / "asr_transcripts.json")
        self.rename_undo: dict[str, list] = {FILE_KIND: [], URL_KIND: []}
        self._batch_running = False
        self._batch_kind = ""
        self._batch_records: list[DownloadFile] = []
        self._rejected_results: set[int] = set()
        self._build_ui()
        self._apply_state()
        self._restore_transcript_results()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addLayout(self._build_title_row())
        layout.addLayout(self._build_options_row())
        layout.addLayout(self._build_output_row())
        self._build_input_widgets(layout)
        layout.addLayout(self._build_progress_row())
        layout.addLayout(self._build_action_row())
        self.status_label = BodyLabel("就绪", self)
        layout.addWidget(self.status_label)
        self.setAcceptDrops(True)

    def _build_title_row(self) -> QHBoxLayout:
        title_row = QHBoxLayout()
        title_row.addWidget(TitleLabel("批量转文字", self))
        self.mode_combo = ComboBox(self)
        self.mode_combo.addItems(list(ASR_MODE_OPTIONS))
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        title_row.addWidget(self.mode_combo)
        title_row.addStretch(1)
        return title_row

    def _build_options_row(self) -> QHBoxLayout:
        options = QHBoxLayout()
        options.addWidget(BodyLabel("ASR 接口:", self))
        self.engine_combo = ComboBox(self)
        self.engine_combo.addItems(list(ASR_ENGINE_OPTIONS))
        self.engine_combo.currentTextChanged.connect(self._on_engine_changed)
        options.addWidget(self.engine_combo)
        options.addSpacing(12)

        options.addWidget(BodyLabel("输出格式:", self))
        self.format_combo = ComboBox(self)
        self.format_combo.addItems(list(ASR_FORMAT_OPTIONS))
        options.addWidget(self.format_combo)
        options.addSpacing(12)

        options.addWidget(BodyLabel("并发:", self))
        self.concurrency_combo = ComboBox(self)
        self.concurrency_combo.addItems([str(value) for value in ASR_CONCURRENCY_OPTIONS])
        options.addWidget(self.concurrency_combo)
        options.addStretch(1)
        return options

    def _build_output_row(self) -> QHBoxLayout:
        out_row = QHBoxLayout()
        self.out_dir_btn = PushButton("选择输出目录", self)
        self.out_dir_btn.clicked.connect(self._choose_out_dir)
        self.out_dir_label = BodyLabel(self)
        self.out_dir_label.setStyleSheet("color: #9a9a9a;")
        out_row.addWidget(self.out_dir_btn)
        out_row.addWidget(self.out_dir_label, 1)
        return out_row

    def _build_input_widgets(self, layout: QVBoxLayout) -> None:
        self.local_input = LocalFileInput(self.config.save_dir, self)
        self.local_input.request_download_dir.connect(self.request_download_dir.emit)
        self.local_input.cleared.connect(self._reset_progress)
        layout.addWidget(self.local_input, 1)

        self.url_input = UrlInput(self)
        self.url_input.cleared.connect(self._reset_progress)
        layout.addWidget(self.url_input, 1)
        for kind, panel in ((FILE_KIND, self.local_input), (URL_KIND, self.url_input)):
            panel.rename_bar.rename_btn.clicked.connect(lambda _checked=False, k=kind: self._rename_transcripts(k))
            panel.rename_bar.undo_btn.clicked.connect(lambda _checked=False, k=kind: self._undo_transcript_rename(k))
            panel.table.itemSelectionChanged.connect(self._refresh_rename_controls)
            panel.cleared.connect(lambda k=kind: self._clear_transcript_results(k))
        self.local_input.files_added.connect(self._refresh_rename_controls)
        self.url_input.tasks_changed.connect(self._refresh_rename_controls)

    def _build_progress_row(self) -> QHBoxLayout:
        progress_row = QHBoxLayout()
        self.progress_bar = ProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_label = BodyLabel("进度 0/0", self)
        progress_row.addWidget(self.progress_bar, 1)
        progress_row.addWidget(self.progress_label)
        return progress_row

    def _build_action_row(self) -> QHBoxLayout:
        action_row = QHBoxLayout()
        self.start_btn = PrimaryPushButton("开始转文字", self)
        self.start_btn.clicked.connect(self._start)
        self.open_out_btn = PushButton("打开输出目录", self)
        self.open_out_btn.clicked.connect(self._open_out)
        action_row.addWidget(self.start_btn)
        action_row.addWidget(self.open_out_btn)
        action_row.addStretch(1)
        return action_row

    def _apply_state(self) -> None:
        self.mode_combo.setCurrentText(self.config.asr_mode)
        self._on_mode_changed(self.mode_combo.currentText())
        if self.config.asr_engine in ASR_ENGINE_OPTIONS:
            self.engine_combo.setCurrentText(self.config.asr_engine)
        self.format_combo.setCurrentText(self.config.asr_format)
        self.concurrency_combo.setCurrentText(str(self.config.asr_concurrency))
        self._on_engine_changed(self.engine_combo.currentText())
        self._refresh_out_label()

    def _save_state(self) -> None:
        self.config.asr_mode = self.mode_combo.currentText()
        self.config.asr_engine = self.engine_combo.currentText()
        self.config.asr_format = self.format_combo.currentText()
        self.config.asr_concurrency = int(self.concurrency_combo.currentText())
        update_app_config(
            asr_mode=self.config.asr_mode,
            asr_engine=self.config.asr_engine,
            asr_format=self.config.asr_format,
            asr_concurrency=self.config.asr_concurrency,
        )

    def _is_douyin_mode(self) -> bool:
        return self.mode_combo.currentText() == DOUYIN_ASR_MODE

    def _on_mode_changed(self, mode: str) -> None:
        if self._batch_running:
            return
        is_douyin = mode == DOUYIN_ASR_MODE
        self._sync_engine_options()
        self.local_input.setVisible(not is_douyin)
        self.url_input.setVisible(is_douyin)
        self.config.asr_mode = mode
        self._refresh_out_label()
        self._refresh_rename_controls()

    def _sync_engine_options(self) -> None:
        current = self.engine_combo.currentText() or self.config.asr_engine
        self.engine_combo.blockSignals(True)
        self.engine_combo.clear()
        self.engine_combo.addItems(list(ASR_ENGINE_OPTIONS))
        self.engine_combo.setCurrentText(
            current if current in ASR_ENGINE_OPTIONS else ASR_ENGINE_OPTIONS[0]
        )
        self.engine_combo.blockSignals(False)
        self._on_engine_changed(self.engine_combo.currentText())

    def _on_engine_changed(self, engine_name: str) -> None:
        is_doubao = engine_name == DOUBAO_ENGINE_NAME
        self._sync_concurrency_options(engine_name)
        if is_doubao:
            self.format_combo.setCurrentText("txt")
        self.format_combo.setEnabled(not is_doubao)

    def _sync_concurrency_options(self, engine_name: str) -> None:
        options = DOUBAO_ASR_CONCURRENCY_OPTIONS if engine_name == DOUBAO_ENGINE_NAME else ASR_CONCURRENCY_OPTIONS
        current = self.concurrency_combo.currentText() or str(self.config.asr_concurrency)
        self.concurrency_combo.blockSignals(True)
        self.concurrency_combo.clear()
        self.concurrency_combo.addItems([str(value) for value in options])
        self.concurrency_combo.setCurrentText(current if int(current or 0) in options else str(options[0]))
        self.concurrency_combo.blockSignals(False)

    def _refresh_out_label(self) -> None:
        if self.config.asr_output_dir:
            text = self.config.asr_output_dir
        elif self._is_douyin_mode():
            text = f"默认: {default_url_output_dir()}"
        else:
            text = "默认: 与源文件同目录"
        self.out_dir_label.setText(text)

    def _choose_out_dir(self) -> None:
        if self._batch_running:
            return
        directory = QFileDialog.getExistingDirectory(
            self,
            "选择输出目录",
            self.config.asr_output_dir or self.config.save_dir or str(Path.home()),
        )
        if directory:
            self.config.asr_output_dir = directory
            update_app_config(asr_output_dir=self.config.asr_output_dir)
            self._refresh_out_label()

    def _open_out(self) -> None:
        target = self.config.asr_output_dir
        if not target and not self._is_douyin_mode():
            target = self.local_input.first_source_directory()
        if not target and self._is_douyin_mode():
            target = str(default_url_output_dir())
        if not target or not open_directory(target):
            MessageBox("提示", "输出目录还不存在，先跑一次任务或重新选择目录。", self.window()).exec()

    def add_files(self, files: list[str]) -> None:
        self.local_input.add_files(files)

    def _reset_progress(self) -> None:
        self._set_asr_progress(0, 0)

    def _start(self) -> None:
        if self.is_running():
            self.stop()
            return

        if self.engine_combo.currentText() == DOUBAO_ENGINE_NAME and not load_doubao_api_key():
            MessageBox("提示", "请先到左下角设置填写豆包 API Key。", self.window()).exec()
            return

        if self._is_douyin_mode():
            input_text = self.url_input.text()
            url_pending = extract_audio_urls(input_text)
            if not url_pending:
                share_urls = extract_douyin_share_urls(input_text)
                if share_urls:
                    MessageBox(
                        "提示",
                        "检测到的是抖音视频分享链接，不是 mp3/wav 音频直链，当前不能直接转写。请粘贴抖音音频直链后再开始。",
                        self.window(),
                    ).exec()
                else:
                    MessageBox("提示", "没有检测到可转写的抖音音频直链。", self.window()).exec()
                return
            self._start_url_asr(url_pending)
            return

        pending = self._pending_local_files()
        if not pending:
            MessageBox("提示", "没有需要处理的音视频文件。", self.window()).exec()
            return

        self._start_file_asr(pending)

    def _start_file_asr(self, pending: list[tuple[int, str]]) -> None:
        if self.is_running():
            return
        self._save_state()
        self.row_index_map = [row for row, _ in pending]
        files = [path for _, path in pending]
        self._set_asr_progress(0, len(files))
        ffmpeg_path = str(self.toolchain.ffmpeg) if self.toolchain.ffmpeg else None
        self.status_label.setText(f"正在转文字，共 {len(files)} 个文件。")
        if ffmpeg_path is None and self.config.asr_engine != DOUBAO_ENGINE_NAME:
            self.status_label.setText("未检测到 ffmpeg，需要转换的音视频文件可能无法处理。")

        self.worker = ASRWorkerThread(
            files=files,
            engine_name=self.config.asr_engine,
            export_format=self.config.asr_format,
            concurrency=self.config.asr_concurrency,
            out_dir=self.config.asr_output_dir,
            ffmpeg_path=ffmpeg_path,
        )
        if not self._prepare_transcript_batch(self.worker, files, FILE_KIND, self.row_index_map):
            self.worker = None
            return
        self.worker.progress.connect(self._on_progress)
        self.worker.file_status.connect(self._on_file_status)
        self.worker.count.connect(self._on_count)
        self.worker.finished_all.connect(self._on_finished)
        self.worker.task_result.connect(self._on_transcript_result)
        self._set_task_running(True)
        self.worker.start()
        self.start_btn.setEnabled(True)
        self.start_btn.setText("停止")

    def _start_url_asr(self, urls: list[str]) -> None:
        if self.is_running():
            return
        self._save_state()
        self.row_index_map = []
        if not self.url_input._retain_results or not set(urls).issubset(self.url_input.urls):
            self.url_input.prepare_tasks(urls)
        self.url_row_index_map = [self.url_input.urls.index(url) for url in urls]
        self._set_asr_progress(0, len(urls))
        self.status_label.setText(f"正在转文字，共 {len(urls)} 条音频链接。")

        self.url_worker = UrlASRWorkerThread(
            urls=urls,
            engine_name=self.config.asr_engine,
            export_format=self.config.asr_format,
            concurrency=self.config.asr_concurrency,
            out_dir=self.config.asr_output_dir,
        )
        if not self._prepare_transcript_batch(self.url_worker, urls, URL_KIND, self.url_row_index_map):
            self.url_worker = None
            return
        self.url_worker.progress.connect(self._on_url_progress)
        self.url_worker.count.connect(self._on_url_count)
        self.url_worker.task_metadata.connect(self._on_url_metadata)
        self.url_worker.task_status.connect(self._on_url_status)
        self.url_worker.finished_all.connect(self._on_url_finished)
        self.url_worker.task_result.connect(self._on_transcript_result)
        self._set_task_running(True)
        self.url_worker.start()
        self.start_btn.setEnabled(True)
        self.start_btn.setText("停止")

    def stop(self) -> None:
        stopped = False
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            stopped = True
        if self.url_worker and self.url_worker.isRunning():
            self.url_worker.stop()
            stopped = True
        if stopped:
            self.start_btn.setEnabled(False)
            self.start_btn.setText("正在停止")
            self.progress_label.setText(self.progress_label.text().replace("进度", "停止中", 1))
            self.status_label.setText("正在停止，不会再开始新任务。")

    def _on_progress(self, index: int, path: str, status: str, message: str) -> None:
        if index in self._rejected_results:
            message = self._batch_records[index].detail
        self.status_label.setText(message)

    def _on_file_status(self, index: int, status: str) -> None:
        if index in self._rejected_results:
            status = "失败"
        elif self._batch_kind == FILE_KIND and index < len(self._batch_records):
            record = self._batch_records[index]
            if record.status in {"已完成", "已存在", "失败", "已停止"}:
                status = record.status
        row = self.row_index_map[index] if index < len(self.row_index_map) else index
        self.local_input.set_file_status(row, status)

    def _on_count(self, ok: int, skip: int, fail: int, total: int, filename: str) -> None:
        done = ok + skip + fail
        self._set_asr_progress(done, total)
        set_task_title(self, f"转文字 {done}/{total}")

    def _on_finished(self, summary: str) -> None:
        self.status_label.setText(self._finish_transcript_batch(summary))
        self.start_btn.setEnabled(True)
        self.start_btn.setText("开始转文字")
        set_task_title(self)
        self.worker = None
        self._set_task_running(False)

    def _on_url_progress(self, index: int, url: str, status: str, message: str) -> None:
        display_status = {
            "ok": "已完成",
            "skip": "已存在",
            "fail": "失败",
            "stopped": "已停止",
        }.get(status, status)
        self._on_url_status(index, display_status)
        if index in self._rejected_results:
            message = self._batch_records[index].detail
        self.status_label.setText(message)

    def _on_url_count(self, ok: int, skip: int, fail: int, total: int, filename: str) -> None:
        done = ok + skip + fail
        self._set_asr_progress(done, total)
        set_task_title(self, f"音频转文字 {done}/{total}")

    def _set_asr_progress(self, done: int, total: int) -> None:
        percent = int(done * 100 / total) if total else 0
        self.progress_bar.setValue(percent)
        self.progress_label.setText(f"进度 {done}/{total} | {percent}%")

    def _on_url_finished(self, result: UrlASRBatchResult) -> None:
        summary = self._finish_transcript_batch(result.summary)
        self.status_label.setText(summary)
        failed = set(result.failed_urls)
        failed.update(self._batch_records[index].task_id for index in self._rejected_results)
        if failed:
            retry_status = "已停止" if result.stopped else "失败"
            self.url_input.set_failed_urls([url for url in self.url_input.urls if url in failed], retry_status)
            self.status_label.setText(f"{summary}；失败链接已留在输入框，可直接重试。")
        else:
            self.url_input.set_failed_urls([])
        self.start_btn.setEnabled(True)
        self.start_btn.setText("开始转文字")
        set_task_title(self)
        self.url_worker = None
        self._set_task_running(False)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if not self._batch_running and event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        if self._batch_running:
            return
        self.local_input.add_dropped_urls(event.mimeData().urls())

    def is_running(self) -> bool:
        return bool(
            self._batch_running or (self.worker and self.worker.isRunning())
            or (self.url_worker and self.url_worker.isRunning())
        )

    def _panel(self, kind: str):
        return self.local_input if kind == FILE_KIND else self.url_input

    def _row_record(self, kind: str, row: int) -> DownloadFile | None:
        item = self._panel(kind).table.item(row, 0)
        return self.library.records.get(item.data(OUTPUT_RECORD_ROLE)) if item else None

    def _pending_local_files(self) -> list[tuple[int, str]]:
        pending = dict(self.local_input.pending_files())
        for row in range(self.local_input.table.rowCount()):
            item = self.local_input.table.item(row, 0)
            record = self._row_record(FILE_KIND, row)
            if not record or not item:
                continue
            source = item.data(Qt.UserRole)
            directory = self.config.asr_output_dir or str(Path(source).parent)
            if (Path(record.path).suffix != "." + self.format_combo.currentText()
                    or path_key(record.directory) != path_key(directory)
                    or not record.matches_file()):
                pending[row] = source
        return sorted(pending.items())

    def _prepare_transcript_batch(self, worker, sources: list[str], kind: str, rows: list[int]) -> bool:
        try:
            records = self.library.prepare(sources, kind, worker.output_paths)
            worker.output_paths = [Path(record.path) for record in records]
            self._batch_records = records
            self._rejected_results = set()
            self._batch_kind = kind
            self.rename_undo[kind] = []
            for row, record in zip(rows, records):
                record.status, record.detail = "等待中", ""
                self._panel(kind).table.item(row, 0).setData(OUTPUT_RECORD_ROLE, record.key)
            # A retry may contain only failures; retain the full visible order.
            self.library.latest[kind] = [record.key for row in range(self._panel(kind).table.rowCount())
                                         if (record := self._row_record(kind, row)) is not None]
            self.library.save()
            self._refresh_transcript_names(kind)
            return True
        except (OSError, RenameError) as exc:
            logging.getLogger("bbdown").exception("转写记录准备失败")
            message = str(exc) if isinstance(exc, RenameError) else "转写记录无法保存，请检查软件目录权限。"
            MessageBox("无法开始转写", message, self.window()).exec()
            return False

    def _on_transcript_result(self, result: ASRTaskResult) -> None:
        if not 0 <= result.index < len(self._batch_records):
            return
        record = self._batch_records[result.index]
        if result.source != record.task_id:
            return
        record.status = {"ok": "已完成", "skip": "已存在", "fail": "失败", "stopped": "已停止"}.get(result.status, "失败")
        record.detail = result.message if result.status == "fail" else ""
        if result.status in {"ok", "skip"}:
            # Keep the local input's UserRole pointing to the original media.
            if (not result.output_path
                    or Path(result.output_path).suffix.lower() not in {".txt", ".srt", ".ass"}
                    or path_key(result.output_path) != path_key(record.path)
                    or (result.status == "skip" and not record.matches_file())):
                record.status, record.detail = "失败", "文稿路径或内容发生变化，请核对后重试。"
                self._rejected_results.add(result.index)
                return
            record.path = str(Path(result.output_path).absolute())
            try:
                record.remember_file()
            except OSError:
                record.size = record.mtime_ns = 0
        self._refresh_transcript_names(self._batch_kind)

    def _on_url_metadata(self, index: int, filename: str, size: int) -> None:
        if index < len(self.url_row_index_map):
            row = self.url_row_index_map[index]
            self.url_input.set_task_metadata(row, filename, size)
            self._refresh_transcript_names(URL_KIND)

    def _on_url_status(self, index: int, status: str) -> None:
        if index in self._rejected_results:
            status = "失败"
        elif self._batch_kind == URL_KIND and index < len(self._batch_records):
            record = self._batch_records[index]
            if record.status in {"已完成", "已存在", "失败", "已停止"}:
                status = record.status
        row = self.url_row_index_map[index] if index < len(self.url_row_index_map) else index
        self.url_input.set_task_status(row, status)

    def _finish_transcript_batch(self, summary: str) -> str:
        if self._rejected_results:
            summary += f" 其中 {len(self._rejected_results)} 项文稿发生变化，已标记失败，请核对后重试。"
        for record in self._batch_records:
            if record.status == "等待中":
                record.status = "已停止"
        ready = [r for r in self._batch_records if r.pending_name and r.status in {"已完成", "已存在"}]
        try:
            if ready:
                result = self.library.apply(self.library.plan(ready, [r.pending_name for r in ready]))
                summary += " " + result.summary().replace("下载成功", "转写成功")
                if result.errors:
                    logging.getLogger("bbdown").warning("自动文稿重命名失败：%s", result.errors)
            self.library.save()
        except (OSError, RenameError):
            logging.getLogger("bbdown").exception("转写记录保存或自动改名失败")
            summary += " 文稿名称或记录暂未更新，请检查目录权限。"
        if self._batch_kind:
            self._refresh_transcript_names(self._batch_kind)
        return summary

    def _set_task_running(self, running: bool) -> None:
        self._batch_running = running
        for widget in (self.mode_combo, self.engine_combo, self.format_combo,
                       self.concurrency_combo, self.out_dir_btn):
            widget.setEnabled(not running)
        if not running:
            self.format_combo.setEnabled(self.engine_combo.currentText() != DOUBAO_ENGINE_NAME)
            self._refresh_transcript_names(FILE_KIND)
            self._refresh_transcript_names(URL_KIND)
        self.local_input.set_running(running)
        self.url_input.set_running(running)
        self.setAcceptDrops(not running)
        self._refresh_rename_controls()

    def _refresh_transcript_names(self, kind: str) -> None:
        panel = self._panel(kind)
        for row in range(panel.table.rowCount()):
            record = self._row_record(kind, row)
            if record is None:
                continue
            item = panel.table.item(row, 0)
            item.setText(Path(record.path).name)
            tooltip = f"文稿：{record.path}\n来源：{record.task_id}"
            if record.pending_name:
                tooltip += f"\n待应用标题：{record.pending_name}"
            item.setToolTip(tooltip)
            if record.status in {"已完成", "已存在"}:
                panel.table.item(row, 1).setText(f"{record.size / 1024:.1f} KB")
            if not self._batch_running:
                if kind == FILE_KIND:
                    panel.set_file_status(row, record.status)
                else:
                    panel.set_task_status(row, record.status)

    def _refresh_rename_controls(self, *_args) -> None:
        if not hasattr(self, "url_input"):
            return
        for kind in (FILE_KIND, URL_KIND):
            panel = self._panel(kind)
            rows = [index.row() for index in panel.table.selectionModel().selectedRows()]
            targets = rows or list(range(panel.table.rowCount()))
            enabled = bool(targets) and all(self._row_record(kind, row) is not None for row in targets)
            panel.rename_bar.update_state(panel.table.rowCount(), len(rows), enabled=enabled,
                                          can_undo=bool(self.rename_undo[kind]), running=self._batch_running)

    def _rename_transcripts(self, kind: str) -> None:
        if self.is_running():
            return
        panel = self._panel(kind)
        rows = sorted(index.row() for index in panel.table.selectionModel().selectedRows())
        rows = rows or list(range(panel.table.rowCount()))
        records = [self._row_record(kind, row) for row in rows]
        if not records or any(record is None for record in records):
            return
        dialog = BatchRenameDialog(self.library, records, self.window(), completion_action="转写成功")
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            result = self.library.apply(self.library.plan(records, dialog.titles))
            self.rename_undo[kind] = result.undo
            self.status_label.setText(result.summary().replace("下载成功", "转写成功"))
            if result.errors:
                MessageBox("部分文稿未改名", "\n".join(result.errors[:5]), self.window()).exec()
        except (OSError, RenameError) as exc:
            message = str(exc) if isinstance(exc, RenameError) else "转写记录无法保存，请检查软件目录权限。"
            MessageBox("重命名未完成", message, self.window()).exec()
        self._refresh_transcript_names(kind)
        self._refresh_rename_controls()

    def _undo_transcript_rename(self, kind: str) -> None:
        if self.is_running() or not self.rename_undo[kind]:
            return
        try:
            result = self.library.undo(self.rename_undo[kind])
            self.rename_undo[kind] = result.undo
            self.status_label.setText("已撤销上次重命名。" if not result.errors else "部分文稿未能恢复原名。")
            if result.errors:
                MessageBox("部分文稿未恢复", "\n".join(result.errors[:5]), self.window()).exec()
        except OSError:
            MessageBox("撤销未完成", "转写记录无法保存，请检查软件目录权限。", self.window()).exec()
        self._refresh_transcript_names(kind)
        self._refresh_rename_controls()

    def _clear_transcript_results(self, kind: str) -> None:
        if self._batch_running:
            return
        self.library.latest[kind] = []
        self.rename_undo[kind] = []
        try:
            self.library.save()
        except OSError:
            logging.getLogger("bbdown").exception("清空转写列表后保存记录失败")
        self._refresh_rename_controls()

    def _restore_transcript_results(self) -> None:
        for kind in (FILE_KIND, URL_KIND):
            records = self.library.current(kind)
            if not records:
                continue
            panel = self._panel(kind)
            if kind == FILE_KIND:
                panel.restore_sources([r.task_id for r in records])
            else:
                panel.prepare_tasks([r.task_id for r in records])
                panel.set_failed_urls([r.task_id for r in records if r.status not in {"已完成", "已存在"}])
            for row, record in enumerate(records):
                panel.table.item(row, 0).setData(OUTPUT_RECORD_ROLE, record.key)
            self._refresh_transcript_names(kind)
        self._refresh_rename_controls()
