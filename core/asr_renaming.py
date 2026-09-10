"""Persist transcript identities without ever renaming source audio or video."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from core.download_renaming import DownloadFile, DownloadLibrary, RenameError, RenameItem, path_key


class TranscriptLibrary(DownloadLibrary):
    ALLOWED_KINDS = {"asr_file", "asr_url"}
    RETRY_ACTION = "重新转写"
    RECORD_LABEL = "转写记录"
    ALLOWED_SUFFIXES = {".txt", ".srt", ".ass"}

    def __init__(self, path: Path | None) -> None:
        super().__init__(path)
        # A corrupt history must not turn the rename dialog into a media-file
        # renamer, or let two independent tasks claim the same transcript.
        claimed: set[str] = set()
        for key, record in list(self.records.items()):
            output = Path(record.path)
            valid = (
                output.suffix.lower() in self.ALLOWED_SUFFIXES
                and path_key(output.parent) == path_key(record.directory)
                and (not record.pending_name
                     or Path(record.pending_name).suffix.lower() == output.suffix.lower())
                and (record.kind != "asr_file" or path_key(output) != path_key(record.task_id))
                and path_key(output) not in claimed
            )
            if not valid:
                del self.records[key]
                continue
            claimed.add(path_key(output))
            if record.status in {"转换中", "识别中", "获取信息"}:
                record.status = "已停止"
        self.latest = {
            kind: [key for key in keys if key in self.records]
            for kind, keys in self.latest.items() if kind in self.ALLOWED_KINDS
        }

    def prepare(
        self,
        sources: list[str],
        kind: str,
        preferred_paths: list[Path],
    ) -> list[DownloadFile]:
        """Resolve a batch in input order, preserving names across retries.

        Identity uses source, output directory and format, rather than an
        allocated filename (which can change with order or collisions).
        Untracked or externally changed files are never adopted as outputs.
        """
        if kind not in self.ALLOWED_KINDS:
            raise ValueError("Unsupported transcript kind")
        if len(sources) != len(preferred_paths):
            raise ValueError("sources and preferred_paths must have the same length")

        requests: list[tuple[str, str, Path]] = []
        request_keys: set[str] = set()
        for source, preferred in zip(sources, preferred_paths):
            preferred = Path(preferred).absolute()
            if preferred.suffix.lower() not in self.ALLOWED_SUFFIXES:
                raise RenameError("只能为 TXT、SRT 或 ASS 文稿设置文件名。")
            identity_source = path_key(source) if kind == "asr_file" else source
            key = hashlib.sha256(json.dumps(
                [kind, identity_source, path_key(preferred.parent), preferred.suffix.lower()],
                ensure_ascii=False,
            ).encode("utf-8")).hexdigest()
            if key in request_keys:
                raise RenameError("列表中有重复的转写来源，请去重后再开始。")
            request_keys.add(key)
            requests.append((key, source, preferred))

        # Include inactive batches and pending titles; these destinations may
        # not exist yet, but remain owned by another task until changed.
        owners: dict[str, set[str]] = {}
        for record in self.records.values():
            owners.setdefault(path_key(record.path), set()).add(record.key)
            if record.pending_name:
                owners.setdefault(path_key(Path(record.directory) / record.pending_name), set()).add(record.key)
        source_paths = {path_key(source) for source in sources} if kind == "asr_file" else set()
        result = []
        for key, source, preferred in requests:
            record = self.records.get(key)
            target = Path(record.path) if record else preferred
            original = target
            number = 2
            while (
                path_key(target) in source_paths
                or bool(owners.get(path_key(target), set()) - {key})
                or (target.exists() and not (
                    record and path_key(target) == path_key(record.path) and record.matches_file()
                ))
                or target.is_symlink()
            ):
                target = original.with_name(f"{original.stem} ({number}){original.suffix}")
                number += 1

            if record is None:
                record = DownloadFile(key, source, kind, str(target.parent), str(target))
                self.records[key] = record
            elif path_key(target) != path_key(record.path):
                record.path = str(target)
                record.size = record.mtime_ns = 0
                record.status = "等待中"
                record.detail = ""
            record.task_id = source
            owners.setdefault(path_key(target), set()).add(key)
            result.append(record)
        self.latest[kind] = [record.key for record in result]
        self.save()
        return result

    def plan(self, records: list[DownloadFile], titles: list[str]) -> list[RenameItem]:
        items = super().plan(records, titles)
        # Waiting/failed tasks own their future output paths too. A successful
        # transcript must not take one just because no file exists there yet.
        owners: dict[str, set[str]] = {}
        for record in self.records.values():
            owners.setdefault(path_key(record.path), set()).add(record.key)
        reserved: set[str] = set()
        selected = {item.record.key for item in items}
        for record in self.records.values():
            if record.pending_name and record.key not in selected:
                reserved.add(path_key(Path(record.directory) / record.pending_name))
        for item in items:
            target = item.target
            number = 2
            while (
                path_key(target) in reserved
                or bool(owners.get(path_key(target), set()) - {item.record.key})
                or (target.exists() and path_key(target) != path_key(item.record.path))
            ):
                target = item.target.with_name(f"{item.target.stem} ({number}){item.target.suffix}")
                number += 1
            item.target = target
            reserved.add(path_key(target))
        return items
