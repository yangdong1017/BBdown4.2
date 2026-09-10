from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from core.asr_file_worker import ASRWorkerThread
from core.asr_renaming import TranscriptLibrary
from core.asr_task import ASRTaskResult
from core.download_renaming import DownloadLibrary, RenameError, path_key
from core.url_asr_worker import UrlASRWorkerThread


class TranscriptRenamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "文稿"
        self.output.mkdir()
        self.library = TranscriptLibrary(self.root / "transcripts.json")

    def records(self, count=3, suffix=".txt", kind="asr_file"):
        sources = []
        for index in range(count):
            if kind == "asr_file":
                source = self.root / f"原音频{index}.mp3"
                source.write_bytes(f"audio-{index}".encode())
                sources.append(str(source))
            else:
                sources.append(f"https://example.com/audio{index}.mp3")
        paths = [self.output / f"原音频{index}{suffix}" for index in range(count)]
        records = self.library.prepare(sources, kind, paths)
        for index, record in enumerate(records):
            Path(record.path).write_text(f"文稿内容-{index}", encoding="utf-8")
            record.status = "已完成"
            record.remember_file()
        return sources, paths, records

    def test_100_transcripts_rename_undo_without_touching_source_media(self):
        sources, _paths, records = self.records(100)
        original_paths = [record.path for record in records]
        result = self.library.apply(self.library.plan(records, [f"标题{i:03d}" for i in range(100)]))
        self.assertEqual((result.renamed, result.errors), (100, []))
        for index, record in enumerate(records):
            self.assertEqual(Path(record.path).name, f"标题{index:03d}.txt")
            self.assertEqual(Path(record.path).read_text(encoding="utf-8"), f"文稿内容-{index}")
            self.assertEqual(Path(sources[index]).read_bytes(), f"audio-{index}".encode())
        restored = self.library.undo(result.undo)
        self.assertEqual((restored.renamed, restored.errors), (100, []))
        self.assertEqual([record.path for record in records], original_paths)

    def test_same_source_has_independent_txt_srt_and_ass_names(self):
        source = str(self.root / "视频.mp4")
        records = []
        for suffix in (".txt", ".srt", ".ass"):
            record = self.library.prepare([source], "asr_file", [self.output / f"视频{suffix}"])[0]
            Path(record.path).write_text(f"content-{suffix}", encoding="utf-8")
            record.status = "已完成"
            record.remember_file()
            records.append(record)
        self.assertEqual(len({record.key for record in records}), 3)
        result = self.library.apply(self.library.plan(records, ["新标题"] * 3))
        self.assertEqual((result.renamed, result.errors), (3, []))
        for suffix in (".txt", ".srt", ".ass"):
            record = TranscriptLibrary(self.library.path).prepare(
                [source], "asr_file", [self.output / f"视频{suffix}"])[0]
            self.assertEqual(Path(record.path).name, f"新标题{suffix}")
            self.assertTrue(record.matches_file())

    def test_same_filename_different_sources_keep_mapping_after_reorder(self):
        sources = [str(self.root / "甲" / "音频.mp3"), str(self.root / "乙" / "音频.mp3")]
        paths = [self.output / "音频.txt"] * 2
        records = self.library.prepare(sources, "asr_file", paths)
        self.assertEqual([Path(record.path).name for record in records], ["音频.txt", "音频 (2).txt"])
        for index, record in enumerate(records):
            Path(record.path).write_text(f"source-{index}", encoding="utf-8")
            record.status = "已完成"
            record.remember_file()
        self.library.apply(self.library.plan(records, ["甲标题", "乙标题"]))
        restored = TranscriptLibrary(self.library.path)
        reordered = restored.prepare(list(reversed(sources)), "asr_file", paths)
        self.assertEqual([Path(record.path).name for record in reordered], ["乙标题.txt", "甲标题.txt"])
        self.assertEqual([record.task_id for record in reordered], list(reversed(sources)))

    def test_same_source_in_different_output_directories_is_independent(self):
        source = "https://example.com/audio.mp3"
        first = self.library.prepare([source], "asr_url", [self.output / "音频.txt"])[0]
        other_dir = self.root / "另一目录"
        other_dir.mkdir()
        second = self.library.prepare([source], "asr_url", [other_dir / "音频.txt"])[0]
        self.assertNotEqual(first.key, second.key)
        self.library.apply(self.library.plan([first], ["仅第一目录的新标题"]))
        self.assertEqual(second.pending_name, "")
        restored = TranscriptLibrary(self.library.path)
        self.assertEqual([record.key for record in restored.current("asr_url")], [second.key])
        self.assertEqual(restored.prepare([source], "asr_url", [self.output / "音频.txt"])[0].pending_name,
                         "仅第一目录的新标题.txt")

    def test_failed_url_title_survives_restart_retry_and_rename(self):
        sources, paths, records = self.records(kind="asr_url")
        failed = records[1]
        Path(failed.path).unlink()
        failed.status = "失败"
        result = self.library.apply(self.library.plan(records, ["甲", "乙", "丙"]))
        self.assertEqual((result.renamed, result.pending, result.errors), (2, 1, []))
        restored = TranscriptLibrary(self.library.path)
        failed = restored.prepare([sources[1]], "asr_url", [paths[1]])[0]
        self.assertEqual(failed.pending_name, "乙.txt")
        Path(failed.path).write_text("重试成功", encoding="utf-8")
        failed.status = "已完成"
        failed.remember_file()
        completed = restored.apply(restored.plan([failed], [failed.pending_name]))
        self.assertEqual((completed.renamed, completed.errors), (1, []))
        self.assertEqual(Path(failed.path).name, "乙.txt")
        self.assertEqual(failed.pending_name, "")
        self.assertEqual(Path(failed.path).read_text(encoding="utf-8"), "重试成功")

    def test_renamed_transcript_is_reused_without_asr_after_restart(self):
        for kind in ("asr_file", "asr_url"):
            with self.subTest(kind=kind):
                sources, paths, records = self.records(1, kind=kind)
                self.library.apply(self.library.plan(records, [f"已命名-{kind}"]))
                records = TranscriptLibrary(self.library.path).prepare(sources, kind, paths)
                if kind == "asr_file":
                    worker = ASRWorkerThread(sources, "必剪", "txt", 5, str(self.output), None,
                                             output_paths=[Path(record.path) for record in records])
                else:
                    worker = UrlASRWorkerThread(sources, "必剪", "txt", 5, str(self.output),
                                                output_paths=[Path(record.path) for record in records])
                results = []
                worker.task_result.connect(results.append)
                with (
                    patch("core.url_asr_worker.probe_audio_size", return_value=100),
                    patch("core.asr_task.transcribe_audio", side_effect=AssertionError("no ASR allowed")),
                    patch("core.asr_task.fetch_audio_bytes", side_effect=AssertionError("no download allowed")),
                ):
                    worker._run_batch()
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0].status, "skip")
                self.assertEqual(path_key(results[0].output_path), path_key(records[0].path))

    def test_preexisting_untracked_file_and_changed_mapping_are_not_adopted(self):
        source = "https://example.com/audio.mp3"
        preferred = self.output / "音频.txt"
        preferred.write_text("用户原文件", encoding="utf-8")
        record = self.library.prepare([source], "asr_url", [preferred])[0]
        self.assertEqual(Path(record.path).name, "音频 (2).txt")
        Path(record.path).write_text("生成文稿", encoding="utf-8")
        record.status = "已完成"
        record.remember_file()
        self.library.apply(self.library.plan([record], ["改后标题"]))
        old_path = Path(record.path)
        old_path.write_text("外部替换的新内容", encoding="utf-8")
        restored = TranscriptLibrary(self.library.path)
        record = restored.prepare([source], "asr_url", [preferred])[0]
        self.assertEqual(Path(record.path).name, "改后标题 (2).txt")
        self.assertEqual(record.size, 0)
        self.assertEqual(preferred.read_text(encoding="utf-8"), "用户原文件")
        self.assertEqual(old_path.read_text(encoding="utf-8"), "外部替换的新内容")

    def test_waiting_output_and_pending_titles_reserve_names_across_batches(self):
        sources, paths, records = self.records(2)
        Path(records[1].path).unlink()
        records[1].status = "失败"
        result = self.library.apply(self.library.plan([records[0]], [Path(records[1].path).stem]))
        self.assertEqual(result.renamed, 1)
        self.assertNotEqual(path_key(records[0].path), path_key(records[1].path))
        self.library.apply(self.library.plan([records[1]], ["预留标题"]))
        other = self.library.prepare(["https://example.com/other.mp3"], "asr_url", [self.output / "预留标题.txt"])[0]
        self.assertEqual(Path(other.path).name, "预留标题 (2).txt")
        restored = TranscriptLibrary(self.library.path)
        self.assertEqual(restored.records[records[1].key].pending_name, "预留标题.txt")

    def test_late_collision_and_undo_never_overwrite_other_files(self):
        _sources, _paths, records = self.records(1)
        plan = self.library.plan(records, ["新标题"])
        plan[0].target.write_text("后来出现的文件", encoding="utf-8")
        result = self.library.apply(plan)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(plan[0].target.read_text(encoding="utf-8"), "后来出现的文件")
        original = Path(records[0].path)
        renamed = self.library.apply(self.library.plan(records, ["另一标题"]))
        original.write_text("占用了旧文件名", encoding="utf-8")
        undone = self.library.undo(renamed.undo)
        self.assertEqual(len(undone.errors), 1)
        self.assertEqual(original.read_text(encoding="utf-8"), "占用了旧文件名")
        self.assertEqual(Path(records[0].path).read_text(encoding="utf-8"), "文稿内容-0")

    def test_invalid_output_or_duplicate_source_is_rejected(self):
        source = str(self.root / "音频.mp3")
        with self.assertRaises(RenameError):
            self.library.prepare([source], "asr_file", [Path(source)])
        with self.assertRaises(ValueError):
            self.library.prepare([source], "audio", [self.output / "音频.txt"])
        with self.assertRaises(ValueError):
            self.library.prepare([source], "asr_file", [])
        with self.assertRaises(RenameError):
            self.library.prepare([source, source], "asr_file", [self.output / "音频.txt"] * 2)
        self.assertFalse(self.library.records)

    def test_history_is_separate_from_downloads_and_only_accepts_transcripts(self):
        _sources, _paths, records = self.records(1)
        self.library.save()
        self.assertFalse(DownloadLibrary(self.library.path).records)
        records[0].path = str(self.root / "原音频0.mp3")
        self.library.save()
        restored = TranscriptLibrary(self.library.path)
        self.assertFalse(restored.records)
        self.assertEqual(restored.current("asr_file"), [])


class TranscriptWorkerResultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def workers(self, directory, sources, *, output_paths=None):
        return (
            ASRWorkerThread(sources, "必剪", "txt", 5, directory, None, output_paths=output_paths),
            UrlASRWorkerThread(sources, "必剪", "txt", 5, directory, output_paths=output_paths),
        )

    def test_custom_paths_are_used_and_length_is_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            source = str(Path(directory) / "source.mp3")
            output = Path(directory) / "改好的标题.txt"
            for worker in self.workers(directory, [source], output_paths=[output]):
                target = "core.asr_file_worker.process_file_asr_task" if isinstance(worker, ASRWorkerThread) else "core.url_asr_worker.process_url_asr_task"
                with patch(target, return_value=ASRTaskResult(0, source, "ok", "完成", str(output))) as process, patch("core.url_asr_worker.probe_audio_size", return_value=100):
                    worker._process_one(0, source)
                self.assertEqual(process.call_args.kwargs["output_path"], output)
            with self.assertRaises(ValueError):
                ASRWorkerThread([source], "必剪", "txt", 5, directory, None, output_paths=[])
            with self.assertRaises(ValueError):
                UrlASRWorkerThread([source], "必剪", "txt", 5, directory, output_paths=[])

    def test_each_result_including_fail_skip_and_stopped_is_emitted_once(self):
        with tempfile.TemporaryDirectory() as directory:
            sources = [f"source-{i}" for i in range(4)]
            statuses = ["ok", "skip", "fail", "stopped"]
            for worker in self.workers(directory, sources):
                results = []
                progress = []
                worker.task_result.connect(results.append)
                worker.progress.connect(lambda *args: progress.append(args))
                def process(index, source):
                    return ASRTaskResult(index, source, statuses[index], "result", str(Path(directory) / f"{index}.txt"))
                with patch.object(worker, "_process_one", side_effect=process):
                    worker._run_batch()
                self.assertEqual(sorted((result.index, result.status) for result in results), list(enumerate(statuses)))
                self.assertEqual(len(progress), 4)

    def test_stop_before_batch_emits_all_unstarted_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            sources = [f"source-{i}" for i in range(7)]
            for worker in self.workers(directory, sources):
                results = []
                worker.task_result.connect(results.append)
                worker.stop()
                with patch.object(worker, "_process_one", side_effect=AssertionError("should not run")):
                    worker._run_batch()
                self.assertEqual([result.index for result in results], list(range(7)))
                self.assertTrue(all(result.status == "stopped" for result in results))


if __name__ == "__main__":
    unittest.main()
