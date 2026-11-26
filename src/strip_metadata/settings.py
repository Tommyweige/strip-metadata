"""Per-user settings, remembered between launches (%LOCALAPPDATA%\\StripMetadata\\settings.json)."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path


def app_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / "StripMetadata"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class Settings:
    keep_orientation: bool = True
    keep_workflow: bool = False
    save_copy: bool = False
    keep_dates: bool = True
    check_updates: bool = True
    remember_clean: bool = True
    advanced_open: bool = False
    last_update_check: float = 0.0

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        path = path or app_dir() / "settings.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        s = cls()
        for f in fields(cls):
            v = raw.get(f.name)
            if isinstance(v, type(getattr(s, f.name))) or (f.type == "float" and isinstance(v, int)):
                setattr(s, f.name, v)
        return s

    def save(self, path: Path | None = None) -> None:
        path = path or app_dir() / "settings.json"
        try:
            path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        except OSError:
            pass  # settings are a convenience; never block the app
