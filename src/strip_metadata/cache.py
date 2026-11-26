"""Remember files already known to be clean, so repeat runs skip them without opening them.

An entry matches only while the file keeps the same size and modification time, and only
for the same cleaning options and rules version. Any edit to the file makes it be checked again.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

RULES_VERSION = 3  # bump whenever what counts as "clean" changes, to invalidate old entries
MAX_ENTRIES = 200_000


def _key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


class CleanCache:
    def __init__(self, file: Path | None = None):
        self.file = file
        self.lock = threading.Lock()
        self.entries: dict[str, list] = {}
        self.hits = 0
        self.dirty = False
        if file:
            try:
                raw = json.loads(Path(file).read_text(encoding="utf-8"))
                if isinstance(raw, dict) and raw.get("rules") == RULES_VERSION and isinstance(raw.get("files"), dict):
                    self.entries = raw["files"]
            except (OSError, ValueError):
                pass

    @staticmethod
    def fingerprint(options) -> str:
        return f"r{RULES_VERSION}o{int(options.keep_orientation)}w{int(options.keep_workflow)}c{options.crop}"

    def is_clean(self, path: Path, st: os.stat_result, fp: str) -> bool:
        with self.lock:
            e = self.entries.get(_key(path))
            ok = e == [st.st_size, st.st_mtime_ns, fp]
            if ok:
                self.hits += 1
            return ok

    def mark_clean(self, path: Path, fp: str) -> None:
        try:
            st = Path(path).stat()
        except OSError:
            return
        with self.lock:
            k = _key(path)
            self.entries.pop(k, None)  # re-insert so the newest entries survive trimming
            self.entries[k] = [st.st_size, st.st_mtime_ns, fp]
            self.dirty = True

    def save(self) -> None:
        if not self.file or not self.dirty:
            return
        with self.lock:
            if len(self.entries) > MAX_ENTRIES:
                keep = list(self.entries.items())[-MAX_ENTRIES * 3 // 4:]
                self.entries = dict(keep)
            data = json.dumps({"rules": RULES_VERSION, "files": self.entries}, ensure_ascii=False)
            self.dirty = False
        tmp = Path(self.file).with_suffix(".tmp")
        try:
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, self.file)
        except OSError:
            pass  # a cache is a convenience; never fail the run
