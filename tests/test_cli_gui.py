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


def make_app(root, tmp_path_factory, **kw):
    """App with its own settings file and no network."""
    from strip_metadata.gui import App
    kw.setdefault("settings_path", tmp_path_factory.mktemp("cfg") / "settings.json")
    kw.setdefault("fetch_update", lambda: None)
    return App(root, **kw)


def pump(root, app, until, timeout=30):
    end = time.time() + timeout
    while app.state not in until:
        root.update()
        assert time.time() < end, f"timed out in state {app.state}"
        time.sleep(0.01)


def test_gui_full_run(root, media_dir, monkeypatch, tmp_path_factory):
    monkeypatch.setattr("strip_metadata.gui.log_dir", lambda: tmp_path_factory.mktemp("logs"))
    asked = []
    app = make_app(root, tmp_path_factory, confirm=lambda title, msg: asked.append(msg) or True)
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
    before = (media_dir / "photo.jpg").read_bytes()
    app = make_app(root, tmp_path_factory, confirm=lambda *_: False)
    app.start([media_dir])
    end = time.time() + 10
    while app.status.cget("text") != "已取消。":
        root.update()
        assert time.time() < end
    assert app.state == "idle"
    assert (media_dir / "photo.jpg").read_bytes() == before


def test_gui_empty_folder(root, tmp_path, tmp_path_factory):
    app = make_app(root, tmp_path_factory, confirm=lambda *_: True)
    app.start([tmp_path])
    end = time.time() + 10
    while "沒有找到" not in app.status.cget("text"):
        root.update()
        assert time.time() < end


def test_cli_copy_and_keep_workflow(media_dir, tmp_path_factory, capsys):
    log = tmp_path_factory.mktemp("log") / "run.log"
    before = (media_dir / "photo.jpg").read_bytes()
    assert cli.main([str(media_dir), "--yes", "--copy", "--keep-workflow", "--log", str(log)]) == 0
    out_dir = media_dir.with_name(media_dir.name + "_已清除")
    assert (media_dir / "photo.jpg").read_bytes() == before
    assert SECRET not in (out_dir / "photo.jpg").read_bytes()
    assert b"prompt\0" in next(out_dir.rglob("*.png")).read_bytes()


def test_gui_save_copy_and_settings(root, media_dir, monkeypatch, tmp_path_factory):
    monkeypatch.setattr("strip_metadata.gui.log_dir", lambda: tmp_path_factory.mktemp("logs"))
    from strip_metadata.settings import Settings

    cfg = tmp_path_factory.mktemp("cfg2") / "settings.json"
    asked = []
    app = make_app(root, tmp_path_factory, settings_path=cfg, confirm=lambda t, m: asked.append(m) or True)
    app.save_copy.set(True)
    assert Settings.load(cfg).save_copy is True  # remembered immediately
    before = (media_dir / "photo.jpg").read_bytes()
    app.start([media_dir])
    pump(root, app, {"done"})
    assert "原檔不會被修改" in asked[0]
    assert (media_dir / "photo.jpg").read_bytes() == before
    assert SECRET not in (media_dir.with_name(media_dir.name + "_已清除") / "photo.jpg").read_bytes()


def test_gui_retry_failed(root, media_dir, monkeypatch, tmp_path_factory):
    monkeypatch.setattr("strip_metadata.gui.log_dir", lambda: tmp_path_factory.mktemp("logs"))
    import strip_metadata.batch as gui
    real = gui.strip_file
    broken = {"on": True}

    def flaky(p, *a, **kw):
        if broken["on"] and p.name == "photo.jpg":
            raise gui.StripError("locked")
        return real(p, *a, **kw)
    monkeypatch.setattr(gui, "strip_file", flaky)
    app = make_app(root, tmp_path_factory, confirm=lambda *_: True)
    app.start([media_dir])
    pump(root, app, {"done"})
    assert app.counts["failed"] == 1 and app.btn_retry.winfo_manager()
    assert "（1）" in app.btn_retry.cget("text")
    broken["on"] = False
    app.retry_failed()
    pump(root, app, {"done"})
    assert app.counts[CLEANED] == 1 and app.counts["failed"] == 0
    assert not app.btn_retry.winfo_manager()


def test_gui_update_banner(root, tmp_path_factory):
    app = make_app(root, tmp_path_factory, fetch_update=lambda: ("99.0.0", "https://example.invalid"))
    end = time.time() + 10
    while app.update_info is None:
        root.update()
        assert time.time() < end
    assert "99.0.0" in app.update_label.cget("text")
    # a second launch the same day does not ask again
    calls = []
    make_app(root, tmp_path_factory, settings_path=app.settings_path,
             fetch_update=lambda: calls.append(1))
    time.sleep(0.2)
    assert calls == []


def test_gui_update_check_can_be_off(root, tmp_path_factory):
    from strip_metadata.settings import Settings
    calls = []
    make_app(root, tmp_path_factory, settings=Settings(check_updates=False), fetch_update=lambda: calls.append(1))
    time.sleep(0.2)
    assert calls == []


def test_optional_panels_are_not_hidden_behind_their_anchor(root, tmp_path_factory):
    """Each optional panel must be drawn above the empty frame it is packed into."""
    app = make_app(root, tmp_path_factory)
    app._show_advanced(True)
    root.update()
    order = root.tk.splitlist(root.tk.call("winfo", "children", str(app.adv.master)))  # bottom to top
    for panel, anchor in ((app.adv, app.adv_anchor), (app.copy_banner, app.copy_anchor),
                          (app.update_label, app.top_anchor)):
        assert order.index(str(panel)) > order.index(str(anchor)), f"{panel} is hidden by {anchor}"
    assert app.adv.winfo_manager() == "pack" and app.adv.winfo_reqheight() > 50


def test_opening_advanced_grows_window_to_fit_everything(root, tmp_path_factory):
    app = make_app(root, tmp_path_factory)
    app._show_advanced(False)
    app._fit_window()
    closed = root.minsize()[1]
    app._toggle_advanced()  # open
    root.update_idletasks()
    need = min(root.winfo_reqheight(), root.winfo_screenheight() - 80)
    assert root.minsize()[1] == need > closed
