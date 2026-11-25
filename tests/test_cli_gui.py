import time
import tkinter as tk

import pytest
from conftest import HAS_FFMPEG, SECRET

from strip_metadata import cli
from strip_metadata.core import CLEAN, CLEANED


def all_media(d):
    return [p for p in d.rglob("*") if p.suffix in (".jpg", ".png", ".webp", ".mp4")]


def test_cli_cleans_folder(media_dir, tmp_path_factory, capsys):
    log = tmp_path_factory.mktemp("log") / "run.log"
    assert cli.main([str(media_dir), "--yes", "--log", str(log)]) == 0
    out = capsys.readouterr().out
    n = 4 if HAS_FFMPEG else 3
    assert f"已清除 {n} 個" in out and "不支援的格式" in out
    for p in all_media(media_dir):
        if p.suffix != ".mp4":
            assert SECRET not in p.read_bytes()
    assert "summary" in log.read_text(encoding="utf-8")
    assert cli.main([str(media_dir), "--yes", "--log", str(log)]) == 0
    assert "已清除 0 個" in capsys.readouterr().out


def test_cli_cancel_and_missing(media_dir, monkeypatch, capsys):
    before = (media_dir / "photo.jpg").read_bytes()
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert cli.main([str(media_dir)]) == 0
    assert (media_dir / "photo.jpg").read_bytes() == before
    assert cli.main([str(media_dir / "nope")]) == 2


@pytest.fixture
def root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    r.withdraw()
    yield r
    r.destroy()


def pump(root, app, until, timeout=30):
    end = time.time() + timeout
    while app.state not in until:
        root.update()
        assert time.time() < end, f"timed out in state {app.state}"
        time.sleep(0.01)


def test_gui_full_run(root, media_dir, monkeypatch, tmp_path_factory):
    monkeypatch.setattr("strip_metadata.gui.log_dir", lambda: tmp_path_factory.mktemp("logs"))
    from strip_metadata.gui import App

    asked = []
    app = App(root, confirm=lambda title, msg: asked.append(msg) or True)
    app.start([media_dir])
    pump(root, app, {"done"})
    assert asked and "會直接覆蓋原檔" in asked[0]
    n = 4 if HAS_FFMPEG else 3
    assert app.counts[CLEANED] == n and app.counts["failed"] == 0
    assert "已清除" in app.status.cget("text")
    assert SECRET not in (media_dir / "photo.jpg").read_bytes()

    app.start([media_dir])  # second run: everything already clean
    pump(root, app, {"done"})
    assert app.counts[CLEAN] == n


def test_gui_cancel_leaves_files(root, media_dir, monkeypatch, tmp_path_factory):
    monkeypatch.setattr("strip_metadata.gui.log_dir", lambda: tmp_path_factory.mktemp("logs"))
    from strip_metadata.gui import App

    before = (media_dir / "photo.jpg").read_bytes()
    app = App(root, confirm=lambda *_: False)
    app.start([media_dir])
    end = time.time() + 10
    while app.status.cget("text") != "已取消。":
        root.update()
        assert time.time() < end
    assert app.state == "idle"
    assert (media_dir / "photo.jpg").read_bytes() == before


def test_gui_empty_folder(root, tmp_path):
    from strip_metadata.gui import App

    app = App(root, confirm=lambda *_: True)
    app.start([tmp_path])
    end = time.time() + 10
    while "沒有找到" not in app.status.cget("text"):
        root.update()
        assert time.time() < end
