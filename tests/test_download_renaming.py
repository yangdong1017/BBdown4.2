from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.download_renaming import DownloadLibrary, RenameError, clean_title, parse_titles
from core.douyin_video_urls import DouyinVideoLink
from core.douyin_audio_urls import DouyinAudioLink
from core.douyin_video_worker import DouyinMediaWorkerThread


class DownloadRenamingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = DownloadLibrary(self.root / "history.json")

    def records(self, count=3, kind="video"):
        links = [DouyinVideoLink(f"id{i}", f"https://example.com/{i}") if kind == "video"
                 else DouyinAudioLink(f"id{i}", f"https://example.com/{i}.mp3", ".mp3")
                 for i in range(count)]
        records = self.library.register(links, kind, self.root)
        for index, record in enumerate(records):
            Path(record.path).write_bytes(f"media-content-{index}".encode())
            record.status = "已完成"
            record.remember_file()
        return links, records

    def test_100_files_keep_content_order_and_can_be_undone(self):
        _links, records = self.records(100)
        originals = [record.path for record in records]
        result = self.library.apply(self.library.plan(records, [f"标题{i:03d}" for i in range(100)]))
        self.assertEqual((result.renamed, result.errors), (100, []))
        for index, record in enumerate(records):
            self.assertEqual(Path(record.path).name, f"标题{index:03d}.mp4")
            self.assertEqual(Path(record.path).read_bytes(), f"media-content-{index}".encode())
            self.assertFalse(Path(originals[index]).exists())
        undo = self.library.undo(result.undo)
        self.assertEqual((undo.renamed, undo.errors), (100, []))
        self.assertEqual([record.path for record in records], originals)

    def test_audio_extension_preserved_and_duplicate_names_do_not_overwrite(self):
        _links, records = self.records(kind="audio")
        existing = self.root / "我的标题.mp3"
        existing.write_bytes(b"unrelated")
        result = self.library.apply(self.library.plan(records, ["我的标题", "我的标题.mp3", '专题/一:节?']))
        self.assertFalse(result.errors)
        self.assertEqual(existing.read_bytes(), b"unrelated")
        self.assertEqual([Path(r.path).name for r in records],
                         ["我的标题 (2).mp3", "我的标题 (3).mp3", "专题 一 节.mp3"])

    def test_blank_row_and_excel_trailing_newline_preserve_positions(self):
        _links, records = self.records()
        titles = parse_titles("第一\r\n\r\n第三\r\n", 3)
        self.assertEqual(titles, ["第一", "", "第三"])
        self.assertEqual(parse_titles("第一\n\n", 3), ["第一", "", ""])
        result = self.library.apply(self.library.plan(records, titles))
        self.assertEqual(result.renamed, 2)
        self.assertEqual(Path(records[1].path).name, "id1.mp4")
        with self.assertRaises(RenameError):
            self.library.plan(records, ["少一行", "标题"])
        with self.assertRaises(RenameError):
            parse_titles("链接\t标题", 1)
        with self.assertRaises(RenameError):
            clean_title("<>/", ".mp4")
        self.assertEqual(clean_title("CON", ".mp4"), "_CON.mp4")

    def test_missing_and_modified_files_fail_without_touching_other_files(self):
        _links, records = self.records()
        Path(records[0].path).unlink()
        Path(records[1].path).write_bytes(b"replaced by another file")
        result = self.library.apply(self.library.plan(records, ["甲", "乙", "丙"]))
        self.assertEqual((result.renamed, len(result.errors)), (1, 2))
        self.assertEqual(Path(records[1].path).read_bytes(), b"replaced by another file")
        self.assertTrue((self.root / "丙.mp4").exists())

    def test_failure_keeps_its_title_by_id_after_reorder_and_restart(self):
        links, records = self.records()
        Path(records[1].path).unlink()
        records[1].status = "失败"
        result = self.library.apply(self.library.plan(records, ["甲", "乙", "丙"]))
        self.assertEqual((result.renamed, result.pending), (2, 1))
        restored = DownloadLibrary(self.library.path)
        reordered = restored.register([links[2], links[1], links[0]], "video", self.root)
        self.assertEqual([r.pending_name for r in reordered], ["", "乙.mp4", ""])
        self.assertEqual(Path(reordered[0].path).name, "丙.mp4")
        failed = reordered[1]
        Path(failed.path).write_bytes(b"retried-media")
        failed.status = "已完成"
        failed.remember_file()
        completed = restored.apply(restored.plan([failed], [failed.pending_name]))
        self.assertEqual(completed.renamed, 1)
        self.assertEqual(Path(failed.path).read_bytes(), b"retried-media")
        self.assertEqual(Path(failed.path).name, "乙.mp4")
        self.assertEqual(failed.pending_name, "")

    def test_persisted_renamed_files_are_reused_without_network_download(self):
        links, records = self.records()
        self.library.apply(self.library.plan(records, ["甲", "乙", "丙"]))
        restored = DownloadLibrary(self.library.path)
        worker = DouyinMediaWorkerThread(links, str(self.root), 5,
            existing_files={r.task_id:(r.path,r.size,r.mtime_ns) for r in restored.current("video") if r.matches_file()})
        with patch.object(worker.downloader, "download", side_effect=AssertionError("duplicate download")):
            result = worker._run_batch()
        worker.downloader.close()
        self.assertEqual(result.skipped, 3)
        self.assertEqual(result.failed, 0)

    def test_late_collision_and_failed_undo_never_overwrite(self):
        _links, records = self.records(1)
        plan = self.library.plan(records, ["标题"])
        plan[0].target.write_bytes(b"created after preview")
        result = self.library.apply(plan)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(plan[0].target.read_bytes(), b"created after preview")
        original = Path(records[0].path)
        renamed = self.library.apply(self.library.plan(records, ["另一个标题"]))
        original.write_bytes(b"another file uses original name")
        undone = self.library.undo(renamed.undo)
        self.assertEqual(len(undone.errors), 1)
        self.assertEqual(original.read_bytes(), b"another file uses original name")
        self.assertTrue(Path(records[0].path).exists())
        self.assertEqual(len(undone.undo), 1)

    def test_pending_names_reserve_targets_for_other_batches(self):
        _links, records = self.records(2)
        Path(records[0].path).unlink()
        records[0].status = "失败"
        self.library.apply(self.library.plan([records[0]], ["共享标题"]))
        plan = self.library.plan([records[1]], ["共享标题"])
        self.assertEqual(plan[0].target.name, "共享标题 (2).mp4")

    def test_locked_file_does_not_abort_the_rest(self):
        _links, records = self.records(2)
        from core.download_renaming import move_without_overwrite
        def move(source, target):
            if source.name == "id0.mp4":
                raise PermissionError("locked")
            move_without_overwrite(source, target)
        with patch("core.download_renaming.move_without_overwrite", side_effect=move):
            result = self.library.apply(self.library.plan(records, ["甲", "乙"]))
        self.assertEqual((result.renamed, len(result.errors)), (1, 1))
        self.assertTrue((self.root / "id0.mp4").exists())
        self.assertTrue((self.root / "乙.mp4").exists())

    def test_save_failure_before_apply_leaves_files_untouched(self):
        _links, records = self.records(1)
        with patch.object(self.library, "save", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                self.library.apply(self.library.plan(records, ["新标题"]))
        self.assertTrue((self.root / "id0.mp4").exists())

    def test_save_failure_after_apply_returns_actual_changes_for_undo(self):
        _links, records = self.records(1)
        with patch.object(self.library, "save", side_effect=[None, PermissionError()]):
            result = self.library.apply(self.library.plan(records, ["新标题"]))
        self.assertEqual(result.renamed, 1)
        self.assertEqual(len(result.undo), 1)
        self.assertTrue(result.errors)
        self.assertTrue((self.root / "新标题.mp4").exists())

    def test_download_does_not_adopt_a_changed_renamed_file(self):
        links, records = self.records(1)
        record = records[0]
        worker = DouyinMediaWorkerThread(links, str(self.root), 5,
            existing_files={record.task_id:(record.path,record.size,record.mtime_ns)})
        Path(record.path).write_bytes(b"externally replaced")
        with patch.object(worker.downloader, "download", side_effect=AssertionError("unexpected network")):
            result = worker._run_batch()
        self.assertEqual(result.failed, 1)
        self.assertEqual(Path(record.path).read_bytes(), b"externally replaced")

    def test_invalid_history_entries_are_ignored(self):
        from core.config_store import atomic_write_json
        _links, records = self.records(1)
        self.library.save()
        import json
        payload = json.loads(self.library.path.read_text(encoding="utf-8"))
        payload["latest"]["video"].extend([{}, None, records[0].key])
        payload["records"]["invalid"] = {"path": 123}
        atomic_write_json(self.library.path, payload)
        restored = DownloadLibrary(self.library.path)
        self.assertEqual(len(restored.current("video")), 1)

    def test_pending_title_equal_to_download_filename_is_cleared(self):
        _links, records = self.records(1)
        record = records[0]
        record.status = "失败"
        self.library.apply(self.library.plan(records, ["id0"]))
        record.status = "已完成"
        result = self.library.apply(self.library.plan(records, [record.pending_name]))
        self.assertEqual(result.errors, [])
        self.assertFalse(record.pending_name)
        self.assertTrue((self.root / "id0.mp4").exists())
