from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from .links import AUDIO_SUFFIXES, dedupe, is_douyin_cdn_host, iter_urls


DOUYIN_STANDARD_AUDIO_LINK = (
    "https://lf9-music-east.douyinstatic.com/obj/ies-music-hj/"
    "7546439142222302011.mp3"
)


@dataclass(frozen=True, slots=True)
class DouyinAudioLink:
    audio_id: str
    url: str
    suffix: str

    @property
    def task_id(self) -> str:
        return self.audio_id

    @property
    def file_suffix(self) -> str:
        return self.suffix

    @property
    def content_prefix(self) -> str:
        return "audio/"

    @property
    def media_label(self) -> str:
        return "音频"


def extract_douyin_audio_links(text: str) -> list[DouyinAudioLink]:
    """Extract supported Douyin CDN audio URLs and deduplicate by audio ID.

    The download page only accepts links from the Douyin CDN, because it names
    the saved file after the audio ID in the path.
    """
    links: list[DouyinAudioLink] = []
    for url in iter_urls(text):
        parsed = urlparse(url)
        if not is_douyin_cdn_host(parsed.hostname or ""):
            continue
        path = Path(unquote(parsed.path))
        suffix = path.suffix.lower()
        if suffix not in AUDIO_SUFFIXES:
            continue
        audio_id = _safe_id(path.stem)
        if not audio_id:
            continue
        links.append(DouyinAudioLink(audio_id=audio_id, url=url, suffix=suffix))
    return dedupe(links, key=lambda link: link.audio_id)


def _safe_id(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip())
    return value[:120].strip("_-")
