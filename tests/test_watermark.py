"""Optional visible AI watermark removal (the `watermark` extra)."""
import struct

import pytest
from conftest import SECRET, png_chunk

from strip_metadata import cli, watermark
from strip_metadata.cache import CleanCache
from strip_metadata.core import CLEAN, CLEANED, WATERMARK, Options, StripError, strip_file

pytestmark = pytest.mark.skipif(not watermark.available(), reason="watermark extra not installed")

SIZE = 1024
ON = Options(remove_watermark=True)


def _background():
    import numpy as np

    rng = np.random.default_rng(0)
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    bg = np.stack([60 + 80 * np.sin(xx / 90) + 40 * np.cos(yy / 70),
                   90 + 60 * np.cos(xx / 50),
                   120 + 50 * np.sin((xx + yy) / 80)], -1)
    return np.clip(bg + rng.normal(0, 6, bg.shape), 0, 255).astype(np.float32)


def _sparkle_box():
    from remove_ai_watermarks.gemini_engine import get_watermark_config

    cfg = get_watermark_config(SIZE, SIZE)
    x, y = cfg.get_position(SIZE, SIZE)
    return x, y, cfg.logo_size


def _with_sparkle(bg):
    """Composite the white Gemini sparkle where Gemini puts it."""
    from remove_ai_watermarks.gemini_engine import GeminiEngine, WatermarkSize

    a = GeminiEngine().get_alpha_map(WatermarkSize.SMALL).astype("float32")[..., None]
    x, y, s = _sparkle_box()
    out = bg.copy()
    out[y:y + s, x:x + s] = a * 255 + (1 - a) * out[y:y + s, x:x + s]
    return out.round().astype("uint8")


def _write(path, img):
    import cv2

    ok, buf = cv2.imencode(path.suffix, img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert ok
    data = buf.tobytes()
    if path.suffix == ".png":  # add metadata that must disappear too
        data = data[:33] + png_chunk(b"tEXt", b"Comment\0SECRET") + data[33:]
    path.write_bytes(data)
    return path


def _pixels(path):
    import cv2
    import numpy as np

    return cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR).astype("int16")


@pytest.fixture(scope="module")
def marked_png(tmp_path_factory):
    return _write(tmp_path_factory.mktemp("wm") / "gemini.png", _with_sparkle(_background()))


def test_removes_gemini_sparkle_and_metadata(marked_png, tmp_path):
    f = tmp_path / "a.png"
    f.write_bytes(marked_png.read_bytes())
    before = _pixels(f)
    assert strip_file(f, ON) == WATERMARK
    after = _pixels(f)
    assert SECRET not in f.read_bytes()
    assert after.shape == before.shape
    x, y, s = _sparkle_box()
    # the white sparkle is gone: its area is no longer much brighter than the picture around it
    assert after[y:y + s, x:x + s].mean() < before[y:y + s, x:x + s].mean() - 15
    # everything away from the mark is untouched (PNG is lossless)
    assert (after[: y - 32] == before[: y - 32]).all()
    # running again finds nothing more and leaves the file alone
    data = f.read_bytes()
    assert strip_file(f, ON) == CLEAN
    assert f.read_bytes() == data


def test_jpeg_keeps_orientation(tmp_path):
    from conftest import exif_app1

    f = _write(tmp_path / "a.jpg", _with_sparkle(_background()))
    d = f.read_bytes()
    app0_end = 2 + 2 + struct.unpack(">H", d[4:6])[0] if d[2:4] == b"\xff\xe0" else 2
    f.write_bytes(d[:app0_end] + exif_app1(6) + d[app0_end:])
    assert strip_file(f, ON) == WATERMARK
    out = f.read_bytes()
    assert SECRET not in out
    assert b"Exif\0\0" in out  # orientation-only EXIF kept so the photo is not turned


def test_clean_photo_is_not_touched(tmp_path):
    f = _write(tmp_path / "plain.png", _background().round().astype("uint8"))
    f.write_bytes(f.read_bytes().replace(b"SECRET", b"public"))  # no metadata worth removing either
    assert strip_file(f, Options()) == CLEANED  # the tEXt comment is still metadata
    data = f.read_bytes()
    assert strip_file(f, ON) == CLEAN
    assert f.read_bytes() == data


def test_dry_run_and_option_off_keep_pixels(marked_png, tmp_path):
    f = tmp_path / "a.png"
    f.write_bytes(marked_png.read_bytes())
    data = f.read_bytes()
    assert strip_file(f, ON, dry_run=True) == WATERMARK
    assert f.read_bytes() == data
    assert strip_file(f, Options()) == CLEANED  # metadata only: the sparkle stays
    assert (_pixels(f) == _pixels(marked_png)).all()


def test_save_copy_leaves_original(marked_png, tmp_path):
    f = tmp_path / "a.png"
    f.write_bytes(marked_png.read_bytes())
    dest = tmp_path / "out" / "a.png"
    assert strip_file(f, ON, dest=dest) == WATERMARK
    assert f.read_bytes() == marked_png.read_bytes()
    assert dest.exists() and SECRET not in dest.read_bytes()


def test_failure_leaves_original(monkeypatch, marked_png, tmp_path):
    f = tmp_path / "a.png"
    f.write_bytes(marked_png.read_bytes())

    def boom(*a, **kw):
        raise RuntimeError("model missing")

    import remove_ai_watermarks

    monkeypatch.setattr(remove_ai_watermarks, "remove_visible_detailed", boom)
    with pytest.raises(StripError, match="去除浮水印失敗"):
        strip_file(f, ON)
    assert f.read_bytes() == marked_png.read_bytes()


def test_cache_tells_options_apart():
    assert CleanCache.fingerprint(Options()) != CleanCache.fingerprint(ON)


def test_cli_flag(marked_png, tmp_path, capsys):
    d = tmp_path / "media"
    d.mkdir()
    (d / "a.png").write_bytes(marked_png.read_bytes())
    log = tmp_path / "run.log"
    assert cli.main([str(d), "--yes", "--remove-watermark", "--recheck", "--log", str(log)]) == 0
    out = capsys.readouterr().out
    assert "已清除 1 個" in out and "去除了 AI 浮水印" in out
    assert f"{WATERMARK} " in log.read_text(encoding="utf-8")


def test_gui_option_runs_and_is_remembered(marked_png, tmp_path, monkeypatch):
    import time
    import tkinter as tk

    from strip_metadata.gui import App
    from strip_metadata.settings import Settings

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    try:
        monkeypatch.setattr("strip_metadata.gui.log_dir", lambda: tmp_path)
        cfg = tmp_path / "settings.json"
        asked = []
        app = App(root, settings_path=cfg, fetch_update=lambda: None,
                  confirm=lambda t, m: asked.append(m) or True)
        app.remove_watermark.set(True)
        assert Settings.load(cfg).remove_watermark is True
        assert app.options().remove_watermark is True
        d = tmp_path / "media"
        d.mkdir()
        (d / "a.png").write_bytes(marked_png.read_bytes())
        app.start([d])
        end = time.time() + 60
        while app.state != "done":
            root.update()
            assert time.time() < end
            time.sleep(0.01)
        assert "AI 浮水印" in asked[0]
        assert app.counts[WATERMARK] == 1
        assert "去除了 AI 浮水印" in app.text.get("1.0", "end")
    finally:
        root.destroy()


def _rgb(img):
    from PIL import Image

    return Image.fromarray(img[..., ::-1])


@pytest.mark.parametrize("name", ["anim.webp", "anim.png"])
def test_animated_picture_keeps_its_frames(tmp_path, name):
    from PIL import Image

    frame = _rgb(_with_sparkle(_background()))
    f = tmp_path / name
    frame.save(f, save_all=True, append_images=[frame.rotate(5)], duration=200, loop=0)
    assert not watermark.can_edit(f.read_bytes(), f.suffix)
    assert strip_file(f, ON) in (CLEAN, CLEANED)
    assert Image.open(f).n_frames == 2


def test_16bit_png_is_left_to_metadata_cleaning(tmp_path):
    import cv2

    f = tmp_path / "deep.png"
    ok, buf = cv2.imencode(".png", _with_sparkle(_background()).astype("uint16") * 257)
    data = buf.tobytes()
    f.write_bytes(data[:33] + png_chunk(b"tEXt", b"Comment\0SECRET") + data[33:])
    assert not watermark.can_edit(f.read_bytes(), ".png")
    assert strip_file(f, ON) == CLEANED
    assert SECRET not in f.read_bytes()
    assert cv2.imdecode(__import__("numpy").frombuffer(f.read_bytes(), "uint8"), cv2.IMREAD_UNCHANGED).dtype == "uint16"


def test_still_pictures_are_editable(marked_png, tmp_path):
    assert watermark.can_edit(marked_png.read_bytes(), ".png")
    f = tmp_path / "s.webp"
    _rgb(_with_sparkle(_background())).save(f, lossless=True)
    assert watermark.can_edit(f.read_bytes(), ".webp")


def test_non_ascii_file_name(marked_png, tmp_path):
    d = tmp_path / "相片"
    d.mkdir()
    f = d / "星星.png"
    f.write_bytes(marked_png.read_bytes())
    assert strip_file(f, ON) == WATERMARK
