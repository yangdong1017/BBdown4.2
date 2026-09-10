from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtWidgets import QApplication, QAbstractItemView, QDialog

from core.asr_task import ASRTaskResult
from core.models import AppConfig, DouyinVideoConfig, Toolchain
from ui.asr_page import ASRPage, DOUYIN_ASR_MODE, FILE_KIND, OUTPUT_RECORD_ROLE, URL_KIND
from ui.batch_rename_dialog import BatchRenameDialog
from ui.douyin_video_page import DouyinVideoPage
from ui.rename_toolbar import RenameToolbar


class ASRBatchRenameUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "transcripts"
        self.output.mkdir()
        self.config = AppConfig(save_dir=str(self.root), asr_output_dir=str(self.output),
                                asr_mode="音视频转文字")
        for patcher in (
            patch("ui.asr_page.RUNTIME_DIR", self.root / "runtime"),
            patch("ui.asr_page.load_app_config", side_effect=lambda: replace(self.config)),
            patch("ui.asr_page.update_app_config"),
            patch("ui.asr_page.resolve_toolchain", return_value=Toolchain()),
            patch("ui.asr_page.MessageBox.exec", return_value=QDialog.Rejected),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.page = self._new_page()

    def _new_page(self) -> ASRPage:
        page = ASRPage()
        self.addCleanup(page.deleteLater)
        return page

    def _source(self, name: str = "speech.mp3") -> Path:
        source = self.root / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(("source:" + name).encode())
        return source

    @staticmethod
    def _serial_tasks(items, *, submit_one, should_stop, **_kwargs):
        # Exercise worker result signals without mixing a synchronous run()
        # with queued signals emitted by its normal executor threads.
        for index, item in enumerate(items):
            if should_stop():
                break
            yield submit_one(index, item)

    @staticmethod
    def _write_result(*, index, output_path, path=None, url=None, **_kwargs):
        source = path if path is not None else url
        output = Path(output_path)
        if output.exists() and output.stat().st_size:
            return ASRTaskResult(index, source, "skip", "already exists", str(output))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("transcript:" + source, encoding="utf-8")
        return ASRTaskResult(index, source, "ok", "finished", str(output))

    def _run_files(self, pending=None, *, page=None, processor=None):
        page = page or self.page
        pending = pending if pending is not None else page._pending_local_files()
        with patch("ui.asr_page.ASRWorkerThread.start"):
            page._start_file_asr(pending)
        worker = page.worker
        self.assertIsNotNone(worker)
        with (
            patch("core.asr_file_worker.run_limited_tasks", side_effect=self._serial_tasks),
            patch("core.asr_file_worker.process_file_asr_task", side_effect=processor or self._write_result),
        ):
            worker.run()
        self.assertFalse(page.is_running())
        return worker

    def _run_urls(self, urls, *, page=None, processor=None):
        page = page or self.page
        with patch("ui.asr_page.UrlASRWorkerThread.start"):
            page._start_url_asr(urls)
        worker = page.url_worker
        self.assertIsNotNone(worker)
        with (
            patch("core.url_asr_worker.run_limited_tasks", side_effect=self._serial_tasks),
            patch("core.url_asr_worker.probe_audio_size", return_value=1024),
            patch("core.url_asr_worker.process_url_asr_task", side_effect=processor or self._write_result),
        ):
            worker.run()
        self.assertFalse(page.is_running())
        return worker

    def _rename(self, kind, titles, *, page=None):
        page = page or self.page
        captured = []

        def accept(dialog):
            captured.append(dialog)
            self.assertIsInstance(dialog, BatchRenameDialog)
            self.assertFalse(hasattr(dialog, "paste_btn"))
            dialog.title_edit.setPlainText("\n".join(titles))
            self.assertTrue(dialog.apply_btn.isEnabled(), dialog.count_label.text())
            dialog.apply_btn.click()
            return dialog.result()

        with patch.object(BatchRenameDialog, "exec", accept):
            page._panel(kind).rename_bar.rename_btn.click()
        self.assertEqual(len(captured), 1)
        return captured[0]

    def test_local_rename_and_undo_preserve_media_for_all_formats(self):
        sources = [self._source("speech.mp3"), self._source("video.mp4")]
        originals = {source: source.read_bytes() for source in sources}
        self.page.add_files([str(source) for source in sources])
        for export_format in ("txt", "srt", "ass"):
            with self.subTest(export_format=export_format):
                self.page.format_combo.setCurrentText(export_format)
                self._run_files()
                old_paths = [Path(self.page._row_record(FILE_KIND, row).path) for row in range(2)]
                self._rename(FILE_KIND, ["标题甲", "标题乙"])
                for row, title in enumerate(("标题甲", "标题乙")):
                    renamed = self.output / f"{title}.{export_format}"
                    self.assertTrue(renamed.exists())
                    item = self.page.local_input.table.item(row, 0)
                    self.assertEqual(item.text(), renamed.name)
                    self.assertEqual(item.data(Qt.UserRole), str(sources[row].resolve()))
                    self.assertTrue(item.data(OUTPUT_RECORD_ROLE))
                self.page.local_input.rename_bar.undo_btn.click()
                self.assertTrue(all(path.exists() for path in old_paths))
                self.assertFalse((self.output / f"标题甲.{export_format}").exists())
                for source, data in originals.items():
                    self.assertEqual(source.read_bytes(), data)
        self.assertEqual(self.page.local_input.pending_files(), [])

    def test_url_rename_and_undo_use_transcript_suffix_and_keep_url_identity(self):
        urls = ["https://example.com/music/one.mp3", "https://example.com/music/two.wav"]
        self.page.mode_combo.setCurrentText(DOUYIN_ASR_MODE)
        self.page.format_combo.setCurrentText("srt")
        self.page.url_input.edit.setPlainText("\n".join(urls))
        self._run_urls(urls)
        old_paths = [Path(self.page._row_record(URL_KIND, row).path) for row in range(2)]
        self._rename(URL_KIND, ["链接甲", "链接乙"])
        for row, title in enumerate(("链接甲", "链接乙")):
            item = self.page.url_input.table.item(row, 0)
            self.assertEqual(item.text(), title + ".srt")
            self.assertEqual(item.data(Qt.UserRole), urls[row])
            self.assertTrue((self.output / (title + ".srt")).exists())
        self.page.url_input.rename_bar.undo_btn.click()
        self.assertTrue(all(path.exists() for path in old_paths))

    def test_url_failed_subset_retry_preserves_full_order_and_pending_titles(self):
        urls = [f"https://example.com/music/{i}.mp3" for i in range(4)]
        self.page.mode_combo.setCurrentText(DOUYIN_ASR_MODE)
        self.page.url_input.edit.setPlainText("\n".join(urls))

        def first_pass(**kwargs):
            if kwargs["index"] in {1, 3}:
                return ASRTaskResult(kwargs["index"], kwargs["url"], "fail", "timeout")
            return self._write_result(**kwargs)

        self._run_urls(urls, processor=first_pass)
        self._rename(URL_KIND, ["第一", "第二", "第三", "第四"])
        self.assertEqual(self.page.url_input.text().splitlines(), [urls[1], urls[3]])
        self.assertEqual(self.page._row_record(URL_KIND, 1).pending_name, "第二.txt")
        self._run_urls([urls[3], urls[1]])
        self.assertEqual(self.page.url_row_index_map, [3, 1])
        self.assertEqual(self.page.url_input.urls, urls)
        self.assertEqual(self.page.url_input.table.rowCount(), 4)
        for row, title in enumerate(("第一", "第二", "第三", "第四")):
            self.assertEqual(self.page.url_input.table.item(row, 0).text(), title + ".txt")
            self.assertEqual((self.output / (title + ".txt")).read_text(encoding="utf-8"),
                             "transcript:" + urls[row])
            self.assertFalse(self.page._row_record(URL_KIND, row).pending_name)
        self.assertEqual([record.task_id for record in self.page.library.current(URL_KIND)], urls)

    def test_local_noncontiguous_retry_maps_results_to_original_sources(self):
        sources = [self._source(f"source{i}.mp3") for i in range(4)]
        self.page.add_files([str(source) for source in sources])

        def first_pass(**kwargs):
            if kwargs["index"] in {1, 3}:
                return ASRTaskResult(kwargs["index"], kwargs["path"], "fail", "timeout")
            return self._write_result(**kwargs)

        self._run_files(processor=first_pass)
        self._rename(FILE_KIND, ["甲", "乙", "丙", "丁"])
        self.assertEqual([row for row, _ in self.page._pending_local_files()], [1, 3])
        worker = self._run_files()
        self.assertEqual(self.page.row_index_map, [1, 3])
        self.assertEqual(worker.files, [str(sources[1].resolve()), str(sources[3].resolve())])
        for row, title in enumerate(("甲", "乙", "丙", "丁")):
            self.assertEqual((self.output / (title + ".txt")).read_text(encoding="utf-8"),
                             "transcript:" + str(sources[row].resolve()))
            self.assertEqual(self.page.local_input.table.item(row, 0).data(Qt.UserRole),
                             str(sources[row].resolve()))

    def test_selected_rows_keep_original_directory_and_output_format(self):
        sources = [self._source("one.mp3"), self._source("two.mp3")]
        self.page.add_files([str(source) for source in sources])
        self._run_files()
        self.page.config.asr_output_dir = str(self.root / "new-directory")
        self.page.format_combo.setCurrentText("ass")
        self.page.local_input.table.selectRow(1)
        dialog = self._rename(FILE_KIND, ["只改第二条"])
        self.assertEqual([record.task_id for record in dialog.records], [str(sources[1].resolve())])
        self.assertTrue((self.output / "one.txt").exists())
        self.assertTrue((self.output / "只改第二条.txt").exists())
        self.assertFalse((self.root / "new-directory").exists())
        self.assertEqual([row for row, _ in self.page._pending_local_files()], [0, 1])

    def test_mode_switch_and_restart_restore_names_and_reuse_known_outputs(self):
        source = self._source()
        url = "https://example.com/music/remote.mp3"
        self.page.add_files([str(source)])
        self._run_files()
        self._rename(FILE_KIND, ["本地文稿"])
        self.page.mode_combo.setCurrentText(DOUYIN_ASR_MODE)
        self._run_urls([url])
        self._rename(URL_KIND, ["链接文稿"])
        self.page.mode_combo.setCurrentText("音视频转文字")
        self.assertEqual(self.page.local_input.table.item(0, 0).text(), "本地文稿.txt")
        self.assertTrue(self.page.local_input.rename_bar.undo_btn.isVisibleTo(self.page.local_input))
        self.page.mode_combo.setCurrentText(DOUYIN_ASR_MODE)
        self.assertEqual(self.page.url_input.table.item(0, 0).text(), "链接文稿.txt")
        reopened = self._new_page()
        self.assertEqual(reopened.local_input.table.item(0, 0).text(), "本地文稿.txt")
        self.assertEqual(reopened.url_input.table.item(0, 0).text(), "链接文稿.txt")
        self.assertEqual(reopened.local_input.table.item(0, 0).data(Qt.UserRole), str(source.resolve()))
        self.assertEqual(reopened._pending_local_files(), [])
        file_worker = self._run_files([(0, str(source.resolve()))], page=reopened)
        self.assertEqual(file_worker.output_paths, [self.output / "本地文稿.txt"])
        url_worker = self._run_urls([url], page=reopened)
        self.assertEqual(url_worker.output_paths, [self.output / "链接文稿.txt"])
        self.assertFalse((self.output / "speech.txt").exists())
        self.assertFalse((self.output / "remote.txt").exists())

    def test_skip_status_can_be_renamed_immediately(self):
        source = self._source()
        self.page.add_files([str(source)])
        self._run_files()
        self._run_files([(0, str(source.resolve()))])
        record = self.page._row_record(FILE_KIND, 0)
        self.assertEqual(record.status, "已存在")
        self._rename(FILE_KIND, ["已有文稿"])
        self.assertTrue((self.output / "已有文稿.txt").exists())
        self.assertFalse(record.pending_name)

    def test_stopped_local_task_with_valid_existing_output_can_retry_pending_title(self):
        source = self._source()
        self.page.add_files([str(source)])
        self._run_files()
        with patch("ui.asr_page.ASRWorkerThread.start"):
            self.page._start_file_asr([(0, str(source.resolve()))])
        worker = self.page.worker
        worker.stop()
        with patch("core.asr_file_worker.run_limited_tasks", side_effect=self._serial_tasks):
            worker.run()
        record = self.page._row_record(FILE_KIND, 0)
        self.assertTrue(record.matches_file())
        self.assertEqual(record.status, "已停止")
        self._rename(FILE_KIND, ["恢复后的文稿"])
        self.assertEqual(record.pending_name, "恢复后的文稿.txt")
        self.assertEqual(self.page._pending_local_files(), [(0, str(source.resolve()))])
        self._run_files()
        self.assertTrue((self.output / "恢复后的文稿.txt").exists())
        self.assertFalse(record.pending_name)

    def test_delayed_stage_signals_do_not_replace_terminal_status_or_output_name(self):
        source = self._source()
        self.page.add_files([str(source)])
        self._run_files()
        self._rename(FILE_KIND, ["本地已完成"])
        self.page._on_file_status(0, "识别中")
        self.assertEqual(self.page.local_input.table.item(0, 2).text(), "已完成")
        self.assertEqual(self.page.local_input.table.item(0, 0).text(), "本地已完成.txt")
        url = "https://example.com/music/remote.mp3"
        self._run_urls([url])
        self._rename(URL_KIND, ["链接已完成"])
        self.page._on_url_status(0, "获取信息")
        self.page._on_url_metadata(0, "remote.mp3", 12 * 1024 * 1024)
        self.assertEqual(self.page.url_input.table.item(0, 2).text(), "已完成")
        self.assertEqual(self.page.url_input.table.item(0, 0).text(), "链接已完成.txt")

    def test_unrelated_file_created_after_preparation_is_rejected_then_preserved_on_retry(self):
        url = "https://example.com/music/remote.mp3"

        def foreign_skip(**kwargs):
            Path(kwargs["output_path"]).write_bytes(b"unrelated-existing-document")
            return self._write_result(**kwargs)

        worker = self._run_urls([url], processor=foreign_skip)
        foreign = worker.output_paths[0]
        record = self.page._row_record(URL_KIND, 0)
        self.assertEqual(record.status, "失败")
        self.assertEqual(self.page.url_input.table.item(0, 2).text(), "失败")
        self.assertEqual(self.page.url_input.text(), url)
        self.page._on_url_status(0, "识别中")
        self.assertEqual(self.page.url_input.table.item(0, 2).text(), "失败")
        self._rename(URL_KIND, ["我的文稿"])
        self.assertFalse((self.output / "我的文稿.txt").exists())
        self.assertEqual(foreign.read_bytes(), b"unrelated-existing-document")
        self._run_urls([url])
        self.assertEqual((self.output / "我的文稿.txt").read_text(encoding="utf-8"),
                         "transcript:" + url)
        self.assertEqual(foreign.read_bytes(), b"unrelated-existing-document")
        self.assertEqual(record.status, "已完成")
        self.assertFalse(record.pending_name)

    def test_unexpected_result_path_cannot_adopt_an_unrelated_transcript(self):
        source = self._source()
        unrelated = self.root / "unrelated.txt"
        unrelated.write_bytes(b"unrelated-document")
        self.page.add_files([str(source)])

        def incorrect_path(**kwargs):
            return ASRTaskResult(kwargs["index"], kwargs["path"], "ok", "finished", str(unrelated))

        self._run_files(processor=incorrect_path)
        record = self.page._row_record(FILE_KIND, 0)
        self.assertEqual(record.status, "失败")
        self.assertNotEqual(Path(record.path), unrelated)
        self.assertEqual(self.page.local_input.table.item(0, 2).text(), "失败")
        self._rename(FILE_KIND, ["不能误改"])
        self.assertEqual(unrelated.read_bytes(), b"unrelated-document")
        self.assertFalse((self.root / "不能误改.txt").exists())
        self.assertFalse((self.output / "不能误改.txt").exists())

    def test_running_disables_actions_and_guards_direct_mutations(self):
        source = self._source()
        extra = self._source("extra.mp4")
        url = "https://example.com/music/remote.mp3"
        self.page.add_files([str(source)])
        self._run_files()
        self._rename(FILE_KIND, ["已命名"])
        self._run_urls([url])
        self.page._set_task_running(True)
        self.assertTrue(self.page.is_running())
        for control in (self.page.mode_combo, self.page.engine_combo, self.page.format_combo,
                        self.page.concurrency_combo, self.page.out_dir_btn,
                        self.page.local_input.add_files_btn, self.page.local_input.clear_btn,
                        self.page.local_input.rename_bar.rename_btn,
                        self.page.local_input.rename_bar.undo_btn,
                        self.page.url_input.rename_bar.rename_btn,
                        self.page.url_input.clean_btn, self.page.url_input.clear_btn):
            self.assertFalse(control.isEnabled())
        self.assertTrue(self.page.url_input.edit.isReadOnly())
        self.page.local_input.clear()
        self.page.add_files([str(extra)])
        self.page.local_input.add_dropped_urls([QUrl.fromLocalFile(str(extra))])
        self.page.url_input.clear()
        original_mode = self.page.config.asr_mode
        self.page._on_mode_changed(DOUYIN_ASR_MODE)
        self.assertEqual(self.page.config.asr_mode, original_mode)
        with patch("ui.asr_page.BatchRenameDialog") as dialog:
            self.page._rename_transcripts(FILE_KIND)
            self.page._rename_transcripts(URL_KIND)
        dialog.assert_not_called()
        self.page._undo_transcript_rename(FILE_KIND)
        self.assertTrue((self.output / "已命名.txt").exists())
        self.assertEqual(self.page.local_input.table.rowCount(), 1)
        self.assertEqual(self.page.url_input.table.rowCount(), 1)
        self.page._set_task_running(False)
        self.assertTrue(self.page.local_input.rename_bar.rename_btn.isEnabled())

    def test_shared_toolbar_and_dialog_keep_table_read_only(self):
        source = self._source()
        self.page.add_files([str(source)])
        self._run_files()
        for panel in (self.page.local_input, self.page.url_input):
            self.assertIsInstance(panel.rename_bar, RenameToolbar)
            self.assertEqual(panel.table.editTriggers(), QAbstractItemView.NoEditTriggers)
            self.assertEqual(panel.table.columnCount(), 3)
            self.assertFalse(panel.table.isSortingEnabled())
        with (
            patch("ui.douyin_video_page.RUNTIME_DIR", self.root / "douyin-runtime"),
            patch("ui.douyin_video_page.load_douyin_video_config",
                  return_value=DouyinVideoConfig(save_dir=str(self.root))),
            patch("ui.douyin_video_page.save_douyin_video_config"),
        ):
            douyin = DouyinVideoPage()
            self.addCleanup(douyin.deleteLater)
        self.assertIsInstance(douyin.rename_bar, RenameToolbar)
        record = self.page._row_record(FILE_KIND, 0)
        dialog = BatchRenameDialog(self.page.library, [record], self.page,
                                   completion_action="转写成功")
        self.addCleanup(dialog.deleteLater)
        dialog.title_edit.setPlainText("一\n二")
        self.assertFalse(dialog.apply_btn.isEnabled())
        dialog.title_edit.setPlainText("一")
        self.assertTrue(dialog.apply_btn.isEnabled())
        dialog.cancel_btn.click()
        self.assertEqual(dialog.result(), QDialog.Rejected)
        self.assertTrue(Path(record.path).exists())


if __name__ == "__main__":
    unittest.main()
