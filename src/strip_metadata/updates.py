"""Check GitHub for a newer release (reads the public version number only; sends no user data)."""
from __future__ import annotations

import json
import re
import urllib.request

REPO = "Tommyweige/strip-metadata"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
CHECK_EVERY = 24 * 3600


def parse_version(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v.split("-")[0])
    return tuple(int(n) for n in nums[:3]) if nums else (0,)


def fetch_latest(timeout: float = 5.0) -> tuple[str, str] | None:
    """(version, page url) of the latest release, or None when offline / rate limited."""
    req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "StripMetadata-update-check"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            info = json.loads(r.read().decode("utf-8"))
        return info["tag_name"].lstrip("v"), info.get("html_url") or RELEASES_PAGE
    except Exception:
        return None


def newer_release(current: str, fetch=fetch_latest) -> tuple[str, str] | None:
    latest = fetch()
    if latest and parse_version(latest[0]) > parse_version(current):
        return latest
    return None
