"""Keep download identities stable when users rename their local files."""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config_store import atomic_write_json, read_json
from .errors import UserFacingError


class RenameError(UserFacingError):
    pass


@dataclass
class DownloadFile:
    key: str
    task_id: str
    kind: str
    directory: str
    path: str
    status: str = "等待中"
    detail: str = ""
    pending_name: str = ""
    size: int = 0
    mtime_ns: int = 0

    def matches_file(self) -> bool:
        try:
            path = Path(self.path)
            stat = path.stat()
            return path.is_file() and not path.is_symlink() and stat.st_size > 0 and (
                stat.st_size, stat.st_mtime_ns
            ) == (self.size, self.mtime_ns)
        except OSError:
            return False

    def remember_file(self) -> None:
        stat = Path(self.path).stat()
        self.size, self.mtime_ns = stat.st_size, stat.st_mtime_ns


@dataclass
class RenameItem:
    record: DownloadFile
    target: Path
    pending: bool


@dataclass
class RenameResult:
    renamed: int = 0
    pending: int = 0
    errors: list[str] = field(default_factory=list)
    # key, previous path, previous pending name, path after rename
    undo: list[tuple[str, str, str, str]] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"已重命名 {self.renamed} 个文件"]
        if self.pending:
            parts.append(f"{self.pending} 个标题等待下载成功后应用")
        if self.errors:
            parts.append(f"{len(self.errors)} 项未完成")
        return "，".join(parts) + "。"


def parse_titles(text: str, expected: int) -> list[str]:
    if not text:
        return []
    titles = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while len(titles) > expected and titles[-1] == "":
        titles.pop()
    if any("\t" in title for title in titles):
        raise RenameError("请只粘贴标题这一列，一行一个。")
    return titles


def clean_title(title: str, suffix: str) -> str:
    title = title.strip()
    if title.lower().endswith(suffix.lower()):
        title = title[:-len(suffix)]
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", title)
    title = re.sub(r"\s+", " ", title).strip().rstrip(". ")
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", title, re.I):
        title = "_" + title
    title = title[:100].rstrip(". ")
    if not title:
        raise RenameError("标题中没有可用的文件名字符，请修改后再试。")
    return title + suffix


def path_key(path: str | Path) -> str:
    return str(Path(path).absolute()).casefold()


def move_without_overwrite(source: Path, target: Path) -> None:
    if target.exists():
        raise FileExistsError(str(target))
    if os.name == "nt":
        # Windows rename is atomic and fails if a target appears in the meantime.
        source.rename(target)
    else:
        os.link(source, target)
        try:
            source.unlink()
        except OSError:
            target.unlink()
            raise


class DownloadLibrary:
    ALLOWED_KINDS = {"audio", "video"}
    RETRY_ACTION = "重新下载"
    RECORD_LABEL = "下载记录"

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.records: dict[str, DownloadFile] = {}
        self.latest: dict[str, list[str]] = {}
        payload = read_json(path) if path else {}
        raw_records = payload.get("records", {})
        for key, value in (raw_records.items() if isinstance(raw_records, dict) else []):
            try:
                record = DownloadFile(**value)
                if record.key != key or not all(isinstance(value, str) for value in (
                    record.task_id, record.kind, record.directory, record.path,
                    record.status, record.detail, record.pending_name,
                )):
                    continue
                if record.kind not in self.ALLOWED_KINDS or not Path(record.path).is_absolute():
                    continue
                if not Path(record.directory).is_absolute():
                    continue
                if record.pending_name and Path(record.pending_name).name != record.pending_name:
                    continue
                if record.status in {"等待中", "下载中"}:
                    record.status = "已停止"
                self.records[key] = record
            except (TypeError, ValueError, KeyError):
                continue
        latest = payload.get("latest", {})
        if isinstance(latest, dict):
            self.latest = {kind: list(dict.fromkeys(key for key in keys
                           if isinstance(key, str) and key in self.records and self.records[key].kind == kind))
                           for kind, keys in latest.items() if isinstance(keys, list)}

    def save(self) -> None:
        if self.path:
            atomic_write_json(self.path, {
                "records": {key: asdict(value) for key, value in self.records.items()},
                "latest": self.latest,
            })

    def register(self, links, kind: str, directory: Path) -> list[DownloadFile]:
        directory = directory.resolve()
        result = []
        for link in links:
            key = hashlib.sha256(f"{path_key(directory)}|{kind}|{link.task_id}".encode()).hexdigest()
            record = self.records.get(key)
            if record is None:
                record = DownloadFile(key, link.task_id, kind, str(directory),
                                      str(directory / f"{link.task_id}{link.file_suffix}"))
                self.records[key] = record
            result.append(record)
        self.latest[kind] = [record.key for record in result]
        return result

    def current(self, kind: str) -> list[DownloadFile]:
        return [self.records[key] for key in self.latest.get(kind, [])]

    def plan(self, records: list[DownloadFile], titles: list[str]) -> list[RenameItem]:
        if len(records) != len(titles):
            raise RenameError(f"需要 {len(records)} 行标题，当前是 {len(titles)} 行，请核对后再应用。")
        # Retain all existing paths and other pending names. Never overwrite even
        # when a requested title happens to match another source in this batch.
        selected_keys = {record.key for record, title in zip(records, titles) if title.strip()}
        reserved = {path_key(Path(record.directory) / record.pending_name)
                    for record in self.records.values()
                    if record.pending_name and record.key not in selected_keys}
        result = []
        for index, (record, title) in enumerate(zip(records, titles), 1):
            if not title.strip():
                continue
            try:
                filename = clean_title(title, Path(record.path).suffix)
            except RenameError as exc:
                raise RenameError(f"第 {index} 行：{exc}") from exc
            source = Path(record.path)
            target = source.with_name(filename)
            number = 2
            while path_key(target) in reserved or (target.exists() and path_key(target) != path_key(source)):
                target = source.with_name(f"{Path(filename).stem} ({number}){source.suffix}")
                number += 1
            reserved.add(path_key(target))
            pending = record.status not in {"已完成", "已存在"}
            if pending or path_key(source) != path_key(target) or record.pending_name:
                result.append(RenameItem(record, target, pending))
        return result

    def apply(self, items: list[RenameItem]) -> RenameResult:
        result = RenameResult()
        # Fail before touching files if the mapping cannot be saved at all.
        self.save()
        for item in items:
            record, target = item.record, item.target
            old_path, old_pending = record.path, record.pending_name
            try:
                if item.pending:
                    record.pending_name = target.name
                    result.pending += 1
                else:
                    if not record.matches_file():
                        raise RenameError(f"原文件已移动、删除或发生变化，请{self.RETRY_ACTION}后再改名。")
                    if path_key(record.path) != path_key(target):
                        move_without_overwrite(Path(record.path), target)
                        result.renamed += 1
                    record.path = str(target)
                    record.pending_name = ""
                    record.remember_file()
                result.undo.append((record.key, old_path, old_pending, record.path))
            except (OSError, RenameError) as exc:
                message = str(exc) if isinstance(exc, RenameError) else "文件被占用、同名文件已存在或目录不可写。"
                result.errors.append(f"{Path(old_path).name}：{message}")
        self._save_result(result)
        return result

    def _save_result(self, result: RenameResult) -> None:
        try:
            self.save()
        except OSError:
            result.errors.append(f"文件操作已完成，但{self.RECORD_LABEL}保存失败。请检查软件目录权限后再关闭软件。")

    def undo(self, actions: list[tuple[str, str, str, str]]) -> RenameResult:
        result = RenameResult()
        self.save()
        for key, previous, previous_pending, after in reversed(actions):
            record = self.records[key]
            try:
                if record.path != after:
                    raise RenameError("文件状态已变化，无法撤销。")
                if path_key(previous) != path_key(after):
                    if not record.matches_file():
                        raise RenameError("文件已移动、删除或变化，无法撤销。")
                    move_without_overwrite(Path(after), Path(previous))
                    record.path = previous
                    record.remember_file()
                    result.renamed += 1
                record.pending_name = previous_pending
            except (OSError, RenameError):
                result.errors.append(f"{Path(after).name}：无法恢复，文件已变化、被占用或原名称已存在。")
                result.undo.append((key, previous, previous_pending, after))
        self._save_result(result)
        return result
