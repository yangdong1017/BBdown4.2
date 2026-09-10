from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QDialog, QAbstractItemView
from core.douyin_video_downloader import DouyinMediaDownloadResult
from core.douyin_video_urls import DouyinVideoLink
from core.models import DouyinVideoConfig, DouyinVideoBatchResult
from ui.batch_rename_dialog import BatchRenameDialog
from ui.douyin_video_page import DouyinVideoPage


class BatchRenameUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = DouyinVideoConfig(save_dir=str(self.root), concurrency=5)
        for patcher in [
            patch("ui.douyin_video_page.RUNTIME_DIR", self.root / "runtime"),
            patch("ui.douyin_video_page.load_douyin_video_config", return_value=self.config),
            patch("ui.douyin_video_page.save_douyin_video_config"),
        ]:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.page = DouyinVideoPage()
        self.addCleanup(self.page.deleteLater)
        self.links = [DouyinVideoLink(f"video{i}", f"https://example.com/{i}") for i in range(3)]
        self.page._populate_tasks(self.links)
        for index, (record, link) in enumerate(zip(self.page.download_files, self.links)):
            Path(record.path).write_bytes(f"content-{index}".encode())
            self.page._on_task_result(DouyinMediaDownloadResult(link, "completed", record.path))
            self.page._on_task_status(link.task_id, "已完成", "")
        self.page._set_running_state(False)

    def test_one_click_dialog_applies_to_real_files_without_inline_editing(self):
        captured = []
        def accept(dialog):
            captured.append(dialog)
            dialog.title_edit.setPlainText("标题一\n标题二\n标题三")
            dialog.apply_btn.click()
            return dialog.result()
        with patch.object(BatchRenameDialog, "exec", accept):
            self.page.rename_btn.click()
        self.assertEqual(len(captured), 1)
        self.assertFalse(hasattr(captured[0], "paste_btn"))
        self.assertEqual(self.page.table.columnCount(), 3)
        self.assertEqual(self.page.table.editTriggers(), QAbstractItemView.NoEditTriggers)
        self.assertEqual(self.page.table.item(0, 0).text(), "标题一.mp4")
        self.assertEqual((self.root / "标题一.mp4").read_bytes(), b"content-0")
        self.assertTrue(self.page.undo_rename_btn.isVisibleTo(self.page))
        self.page.undo_rename_btn.click()
        self.assertEqual(self.page.table.item(0, 0).text(), "video0.mp4")
        self.assertFalse((self.root / "标题一.mp4").exists())

    def test_count_mismatch_disables_apply_and_cancel_does_not_rename(self):
        dialog = BatchRenameDialog(self.page.library, self.page.download_files, self.page)
        dialog.title_edit.setPlainText("仅一行")
        self.assertFalse(dialog.apply_btn.isEnabled())
        self.assertIn("需要 3 行", dialog.count_label.text())
        dialog.title_edit.setPlainText("甲\n乙\n丙\n")
        self.assertTrue(dialog.apply_btn.isEnabled())
        dialog.cancel_btn.click()
        self.assertEqual(dialog.result(), QDialog.Rejected)
        self.assertTrue((self.root / "video0.mp4").exists())
        dialog.deleteLater()

    def test_selected_rows_and_original_directory_are_used(self):
        self.page.table.selectRow(1)
        self.page.config.save_dir = str(self.root / "another-directory")
        def accept(dialog):
            self.assertEqual([r.task_id for r in dialog.records], ["video1"])
            dialog.title_edit.setPlainText("仅修改选中")
            dialog.apply_btn.click()
            return dialog.result()
        with patch.object(BatchRenameDialog, "exec", accept):
            self.page.rename_btn.click()
        self.assertTrue((self.root / "仅修改选中.mp4").exists())
        self.assertTrue((self.root / "video0.mp4").exists())
        self.assertTrue((self.root / "video2.mp4").exists())

    def test_results_survive_mode_switch_and_restart(self):
        self.page.library.apply(self.page.library.plan(self.page.download_files, ["甲", "乙", "丙"]))
        self.page._on_download_type_changed("audio")
        self.assertEqual(self.page.table.rowCount(), 0)
        self.page._on_download_type_changed("video")
        self.assertEqual(self.page.table.rowCount(), 3)
        self.assertEqual(self.page.table.item(0,0).text(), "甲.mp4")
        another = DouyinVideoPage()
        self.assertEqual(another.table.item(0,0).text(), "甲.mp4")
        another.deleteLater()

    def test_pending_title_is_applied_when_retry_finishes(self):
        record = self.page.download_files[1]
        Path(record.path).unlink()
        self.page._on_task_status(record.task_id, "失败", "超时")
        self.page.library.apply(self.page.library.plan(self.page.download_files, ["甲", "乙", "丙"]))
        self.page._populate_tasks(self.links)
        record = self.page.download_files[1]
        Path(record.path).write_bytes(b"retry-content")
        for link, item in zip(self.links, self.page.download_files):
            self.page._on_task_result(DouyinMediaDownloadResult(link,"completed",item.path))
            self.page._on_task_status(link.task_id,"已完成","")
        self.page._on_finished(DouyinVideoBatchResult(False,total=3,completed=1,skipped=2))
        self.assertEqual((self.root / "乙.mp4").read_bytes(), b"retry-content")
        self.assertEqual(self.page.table.item(1,0).text(), "乙.mp4")
        self.assertFalse(record.pending_name)

    def test_running_disables_rename_and_mode_switch(self):
        self.page._set_running_state(True)
        self.assertFalse(self.page.rename_btn.isEnabled())
        with patch("ui.douyin_video_page.BatchRenameDialog") as dialog:
            self.page._rename_downloads()
        dialog.assert_not_called()
        self.page._on_download_type_changed("audio")
        self.assertEqual(self.page.current_download_type, "video")
        self.page._set_running_state(False)
        self.assertTrue(self.page.rename_btn.isEnabled())
