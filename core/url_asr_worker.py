from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

from core.asr_service import format_task_error
from core.asr_task import ASRTaskResult, process_url_asr_task
from core.config import MIN_CONCURRENCY
from core.output_paths import OutputPathAllocator
from core.task_scheduler import run_limited_tasks
from core.url_audio import audio_filename_from_url, audio_name_from_url, probe_audio_size


@dataclass(slots=True)
class UrlASRBatchResult:
    summary: str
    output_dir: str
    stopped: bool = False
    failed_urls: list[str] = field(default_factory=list)
    ok: int = 0
    skip: int = 0
    fail: int = 0
    stopped_count: int = 0
    total: int = 0


def default_url_output_dir() -> Path:
    return Path.home() / "Downloads" / "BBDownTranscripts"


class UrlASRWorkerThread(QThread):
    progress = pyqtSignal(int, str, str, str)
    count = pyqtSignal(int, int, int, int, str)
    task_metadata = pyqtSignal(int, str, int)
    task_status = pyqtSignal(int, str)
    task_result = pyqtSignal(object)
    finished_all = pyqtSignal(object)

    def __init__(
        self,
        urls: list[str],
        engine_name: str,
        export_format: str,
        concurrency: int,
        out_dir: str,
        parent=None,
        *,
        output_paths: list[Path] | None = None,
    ) -> None:
        super().__init__(parent)
        self.urls = urls
        self.engine_name = engine_name
        self.export_format = export_format.lower()
        self.concurrency = max(MIN_CONCURRENCY, int(concurrency))
        self.out_dir = Path(out_dir) if out_dir else default_url_output_dir()
        self.stop_flag = threading.Event()
        if output_paths is not None:
            if len(output_paths) != len(self.urls):
                raise ValueError("output_paths and urls must have the same length")
            self.output_paths = [Path(path) for path in output_paths]
        else:
            allocator = OutputPathAllocator()
            self.output_paths = [
                allocator.reserve(
                    self.out_dir / f"{audio_name_from_url(url, index)}.{self.export_format}",
                    allow_existing=True,
                )
                for index, url in enumerate(self.urls)
            ]

    def stop(self) -> None:
        self.stop_flag.set()

    def _process_one(self, index: int, url: str) -> ASRTaskResult:
        self.task_status.emit(index, "获取信息")
        size = -1
        try:
            size = probe_audio_size(url, stopped=self.stop_flag.is_set) or -1
        except Exception:
            pass
        self.task_metadata.emit(index, audio_filename_from_url(url, index), size)
        if self.stop_flag.is_set():
            return ASRTaskResult(index, url, "stopped", "已停止")
        self.task_status.emit(index, "识别中")
        return process_url_asr_task(
            index=index,
            url=url,
            engine_name=self.engine_name,
            export_format=self.export_format,
            output_path=self.output_paths[index],
            stopped=self.stop_flag.is_set,
        )

    @staticmethod
    def _task_error(index: int, url: str, exc: Exception) -> ASRTaskResult:
        name = audio_name_from_url(url, index)
        return ASRTaskResult(index, url, "fail", f"{name}: {format_task_error(exc)}")

    def run(self) -> None:
        try:
            result = self._run_batch()
        except Exception as exc:
            result = UrlASRBatchResult(
                summary=f"任务异常: {format_task_error(exc)}",
                output_dir=str(self.out_dir),
                stopped=self.stop_flag.is_set(),
                failed_urls=list(self.urls),
                fail=len(self.urls),
                total=len(self.urls),
            )
        self.finished_all.emit(result)

    def _run_batch(self) -> UrlASRBatchResult:
        total = len(self.urls)
        ok = skip = fail = stopped_count = 0
        failed_urls: list[str] = []
        processed_indices: set[int] = set()
        started = time.time()

        for result in run_limited_tasks(
            self.urls,
            max_workers=min(self.concurrency, max(total, 1)),
            submit_one=self._process_one,
            should_stop=self.stop_flag.is_set,
            on_error=self._task_error,
        ):
            processed_indices.add(result.index)
            name = audio_name_from_url(result.source, result.index)
            if result.status == "ok":
                ok += 1
            elif result.status == "skip":
                skip += 1
            elif result.status == "stopped":
                stopped_count += 1
                failed_urls.append(result.source)
            else:
                fail += 1
                failed_urls.append(result.source)
            self.progress.emit(result.index, result.source, result.status, result.message)
            self.task_result.emit(result)
            self.count.emit(ok, skip, fail, total, name)

        if self.stop_flag.is_set():
            for index, url in enumerate(self.urls):
                if index not in processed_indices:
                    stopped_count += 1
                    failed_urls.append(url)
                    self.task_status.emit(index, "已停止")
                    self.task_result.emit(ASRTaskResult(index, url, "stopped", "已停止"))

        minutes, seconds = divmod(int(time.time() - started), 60)
        stopped = self.stop_flag.is_set()
        elapsed = f"耗时 {minutes:02d}:{seconds:02d}"
        if stopped:
            # Without this the numbers do not add up to the total and the user
            # thinks links went missing.
            summary = f"已停止: 成功 {ok} 跳过 {skip} 失败 {fail} 已停止 {stopped_count} | {elapsed}"
        else:
            summary = f"完成: 成功 {ok} 跳过 {skip} 失败 {fail} | {elapsed}"
        return UrlASRBatchResult(
            summary=summary,
            output_dir=str(self.out_dir),
            stopped=stopped,
            failed_urls=failed_urls,
            ok=ok,
            skip=skip,
            fail=fail,
            stopped_count=stopped_count,
            total=total,
        )
