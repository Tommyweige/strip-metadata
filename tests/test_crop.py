import tkinter as tk

import pytest
from conftest import HAS_FFMPEG, make_mp4

from strip_metadata.cache import CleanCache
from strip_metadata.cli import main as cli_main
from strip_metadata.core import (
    CLEAN,
    CLEANED,
    Options,
    crop_box,
    strip_file,
    video_display_size,
    video_has_metadata,
)
from strip_metadata.settings import Settings

needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")


@pytest.mark.parametrize("args, expected", [
    ((1920, 1080, "9:16"), (608, 1080, 656, 0)),
    ((1080, 1920, "16:9"), (1080, 608, 0, 656)),
    ((1080, 1920, "9:16"), None),
    ((64, 48, "16:9"), (64, 36, 0, 6)),
    ((100, 100, ""), None),
])
def test_crop_box(args, expected):
    assert crop_box(*args) == expected


@needs_ffmpeg
def test_video_display_size_rotated(tmp_path):
    assert video_display_size(make_mp4(tmp_path / "rot.mp4")) == (48, 64)


@needs_ffmpeg
def test_video_display_size_flat(tmp_path):
    assert video_display_size(make_mp4(tmp_path / "flat.mp4", rotation=0)) == (64, 48)


@needs_ffmpeg
def test_crop_16_9_removes_metadata_and_is_idempotent(tmp_path):
    p = make_mp4(tmp_path / "flat.mp4", rotation=0)
    assert strip_file(p, Options(crop="16:9")) == CLEANED
    assert video_display_size(p) == (64, 36)
    assert video_has_metadata(p) is False
    assert strip_file(p, Options(crop="16:9")) == CLEAN


@needs_ffmpeg
def test_crop_9_16_uses_display_orientation(tmp_path):
    p = make_mp4(tmp_path / "rot.mp4")
    assert strip_file(p, Options(crop="9:16")) == CLEANED
    assert video_display_size(p) == (36, 64)


@needs_ffmpeg
def test_crop_dry_run_leaves_bytes(tmp_path):
    p = make_mp4(tmp_path / "dry.mp4")
    before = p.read_bytes()
    assert strip_file(p, Options(crop="9:16"), dry_run=True) == CLEANED
    assert p.read_bytes() == before


@needs_ffmpeg
def test_no_crop_keeps_rotation(tmp_path):
    p = make_mp4(tmp_path / "keep.mp4")
    assert strip_file(p) == CLEANED
    assert video_display_size(p) == (48, 64)


def test_settings_crop_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    Settings(crop_video="9:16").save(path)
    assert Settings.load(path).crop_video == "9:16"


def test_settings_invalid_crop_resets(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"crop_video": "4:3"}', encoding="utf-8")
    assert Settings.load(path).crop_video == ""


def test_cache_fingerprint_includes_crop():
    assert CleanCache.fingerprint(Options(crop="9:16")) != CleanCache.fingerprint(Options())


@needs_ffmpeg
def test_cli_crop_folder(tmp_path):
    make_mp4(tmp_path / "clip.mp4")
    assert cli_main([str(tmp_path), "--yes", "--crop", "9:16"]) == 0
    assert video_display_size(tmp_path / "clip.mp4") == (36, 64)


@pytest.fixture
def root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    r.withdraw()
    yield r
    r.destroy()


def make_app(root, tmp_path_factory, **kw):
    from strip_metadata.gui import App
    kw.setdefault("settings_path", tmp_path_factory.mktemp("cfg") / "settings.json")
    kw.setdefault("fetch_update", lambda: None)
    return App(root, **kw)


def test_gui_crop_combo(root, tmp_path_factory):
    app = make_app(root, tmp_path_factory)
    app.crop_combo.set("直式 9:16")
    app.crop_combo.event_generate("<<ComboboxSelected>>")
    root.update()
    assert Settings.load(app.settings_path).crop_video == "9:16"
    assert app.options().crop == "9:16"
