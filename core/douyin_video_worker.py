from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

from .config import MIN_CONCURRENCY
from .douyin_media import DouyinMediaLink
from .douyin_video_downloader import DouyinMediaDownloadResult, DouyinMediaDownloader
from .models import DouyinVideoBatchResult
from .task_scheduler import run_limited_tasks


# Each task reports progress every 0.25s, so 50 downloads would hit the UI
# thread 200 times a second. Collect the updates and send them in one batch.
PROGRESS_FLUSH_INTERVAL = 0.2


@dataclass(slots=True)
class _BatchState:
    completed_files: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    failed_urls: list[str] = field(default_factory=list)
    processed_ids: set[str] = field(default_factory=set)
    stopped_count: int = 0


class DouyinMediaWorkerThread(QThread):
    status = pyqtSignal(str)
    # {task_id: (downloaded_bytes, total_bytes)}
    task_progress = pyqtSignal(object)
    task_status = pyqtSignal(str, str, str)
    task_result = pyqtSignal(object)
    batch_progress = pyqtSignal(int, int, int)
    finished_all = pyqtSignal(object)

    def __init__(
        self,
        links: list[DouyinMediaLink],
        save_dir: str,
        concurrency: int,
        parent=None,
        *,
        existing_files: dict[str, tuple[str, int, int]] | None = None,
    ) -> None:
        super().__init__(parent)
        self.links = links
        self.save_dir = Path(save_dir)
        self.concurrency = max(MIN_CONCURRENCY, int(concurrency))
        self.existing_files = dict(existing_files or {})
        self.stop_event = threading.Event()
        self._counter_lock = threading.Lock()
        self._processed = 0
        self._active = 0
        self._progress_lock = threading.Lock()
        self._pending_progress: dict[str, tuple[int, int]] = {}
        self._last_progress_flush = 0.0
        self.downloader = DouyinMediaDownloader(
            self.stop_event,
            self._emit_task_progress,
            pool_size=self.concurrency,
        )

    def stop(self) -> None:
        self.status.emit("正在停止任务，不再开始新任务...")
        self.downloader.stop()

    def run(self) -> None:
        try:
            try:
                result = self._run_batch()
            finally:
                self._flush_task_progress()
                self.downloader.close()
        except Exception:
            stopped = self.stop_event.is_set()
            task_state = "已停止" if stopped else "失败"
            task_detail = "" if stopped else "批量任务异常"
            for link in self.links:
                self.task_status.emit(link.task_id, task_state, task_detail)
            result = DouyinVideoBatchResult(
                stopped=stopped,
                total=len(self.links),
                failed=0 if stopped else len(self.links),
                stopped_count=len(self.links) if stopped else 0,
                failed_urls=[] if stopped else [link.url for link in self.links],
            )
        self.finished_all.emit(result)

    def _run_batch(self) -> DouyinVideoBatchResult:
        total = len(self.links)
        state = _BatchState()

        self._emit_batch_progress(total)

        for result in run_limited_tasks(
            self.links,
            max_workers=min(self.concurrency, max(total, 1)),
            submit_one=lambda index, link: self._download_one(total, index, link),
            should_stop=self.stop_event.is_set,
            on_error=self._task_error,
            start_index=1,
        ):
            self._record_result(result, state)
            self._emit_batch_progress(total)

        self._mark_unstarted(state)

        return DouyinVideoBatchResult(
            stopped=self.stop_event.is_set(),
            total=total,
            completed=len(state.completed_files),
            skipped=len(state.skipped_files),
            failed=len(state.failed_urls),
            stopped_count=state.stopped_count,
            completed_files=state.completed_files,
            skipped_files=state.skipped_files,
            failed_urls=state.failed_urls,
        )

    def _download_one(
        self,
        total: int,
        _index: int,
        link: DouyinMediaLink,
    ) -> DouyinMediaDownloadResult:
        if self.stop_event.is_set():
            return DouyinMediaDownloadResult(link=link, status="stopped", message="已停止")
        existing = self.existing_files.get(link.task_id)
        if existing:
            path, size, mtime_ns = existing
            try:
                stat = Path(path).stat()
                if Path(path).is_file() and not Path(path).is_symlink() and size > 0 and (
                    stat.st_size, stat.st_mtime_ns
                ) == (size, mtime_ns):
                    return DouyinMediaDownloadResult(link=link, status="exists", output_path=path)
            except OSError:
                pass
            return DouyinMediaDownloadResult(link=link, status="failed", message="原文件已变化，请核对后重试。")
        with self._counter_lock:
            self._active += 1
        self.task_status.emit(link.task_id, "下载中", "")
        self._emit_batch_progress(total)
        try:
            return self.downloader.download(link, self.save_dir)
        finally:
            with self._counter_lock:
                self._active = max(0, self._active - 1)
            self._flush_task_progress()
            self._emit_batch_progress(total)

    def _emit_task_progress(self, task_id: str, downloaded: int, total: int) -> None:
        with self._progress_lock:
            self._pending_progress[task_id] = (downloaded, total)
            now = time.monotonic()
            if now - self._last_progress_flush < PROGRESS_FLUSH_INTERVAL:
                return
            self._last_progress_flush = now
            batch = self._pending_progress
            self._pending_progress = {}
        self.task_progress.emit(batch)

    def _flush_task_progress(self) -> None:
        """Send whatever is still buffered, so a finished task never stays stale."""
        with self._progress_lock:
            if not self._pending_progress:
                return
            batch = self._pending_progress
            self._pending_progress = {}
            self._last_progress_flush = time.monotonic()
        self.task_progress.emit(batch)

    def _task_error(
        self,
        _index: int,
        link: DouyinMediaLink,
        exc: Exception,
    ) -> DouyinMediaDownloadResult:
        return DouyinMediaDownloadResult(link=link, status="failed", message="下载任务异常")

    def _record_result(self, result: DouyinMediaDownloadResult, state: _BatchState) -> None:
        self.task_result.emit(result)
        state.processed_ids.add(result.link.task_id)
        with self._counter_lock:
            self._processed += 1

        output_name = Path(result.output_path).name
        if result.status == "completed":
            state.completed_files.append(result.output_path)
            self.task_status.emit(result.link.task_id, "已完成", output_name)
        elif result.status == "exists":
            state.skipped_files.append(result.output_path)
            self.task_status.emit(result.link.task_id, "已存在", output_name)
        elif result.status == "failed":
            state.failed_urls.append(result.link.url)
            self.task_status.emit(result.link.task_id, "失败", result.message)
        else:
            state.stopped_count += 1
            self.task_status.emit(result.link.task_id, "已停止", "")

    def _mark_unstarted(self, state: _BatchState) -> None:
        if not self.stop_event.is_set():
            return
        for link in self.links:
            if link.task_id in state.processed_ids:
                continue
            state.stopped_count += 1
            self.task_status.emit(link.task_id, "已停止", "")

    def _emit_batch_progress(self, total: int) -> None:
        with self._counter_lock:
            processed = self._processed
            active = self._active
        self.batch_progress.emit(processed, total, active)
        if self.stop_event.is_set():
            self.status.emit(f"正在停止 | 已处理 {processed}/{total} | 进行中 {active}")
        else:
            self.status.emit(f"下载中 {processed}/{total} | 进行中 {active} | 并发 {self.concurrency}")
