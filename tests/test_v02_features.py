"""Tests for the v0.2 features: workflow keeping, save-copy, dates, settings, updates, Windows integration."""
import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest
from conftest import SECRET, make_jpeg, make_png, png_chunk

from strip_metadata import updates
from strip_metadata.core import (
    CLEANED,
    Options,
    copy_destination,
    copy_root,
    scan,
    strip_file,
    strip_png,
)
from strip_metadata.settings import Settings


def png_with_workflow(path: Path) -> Path:
    base = (Path(__file__).parent / "fixtures" / "base.png").read_bytes()
    extra = (png_chunk(b"tEXt", b'workflow\0{"nodes": []}') + png_chunk(b"tEXt", b'prompt\0{"1": {}}')
             + png_chunk(b"tEXt", b"Author\0SECRET person") + png_chunk(b"caBX", b"SECRET c2pa"))
    path.write_bytes(base[:33] + extra + base[33:])
    return path


# ---------------------------------------------------------------- AI workflow
def test_keep_workflow_keeps_only_workflow_text(tmp_path):
    out = strip_png(png_with_workflow(tmp_path / "x.png").read_bytes(), keep_workflow=True)
    assert b"workflow\0" in out and b"prompt\0" in out
    assert SECRET not in out  # other text and C2PA still removed


def test_workflow_removed_by_default(tmp_path):
    out = strip_png(png_with_workflow(tmp_path / "x.png").read_bytes())
    assert b"workflow\0" not in out and b"prompt\0" not in out


# ---------------------------------------------------------------- save copy
def test_copy_root_names():
    assert copy_root(Path("D:/photos/trip")) == Path("D:/photos/trip_已清除")
    assert copy_root(Path("D:/")) == Path("D:/已清除")


def test_copy_destination_keeps_layout(tmp_path):
    root = tmp_path / "trip"
    f = root / "day1" / "a.jpg"
    assert copy_destination(f, [root]) == tmp_path / "trip_已清除" / "day1" / "a.jpg"
    loose = tmp_path / "other" / "b.png"
    assert copy_destination(loose, [root]) == tmp_path / "other_已清除" / "b.png"


def test_save_copy_leaves_original(tmp_path):
    root = tmp_path / "trip"
    (root / "day1").mkdir(parents=True)
    dirty = make_jpeg(root / "day1" / "a.jpg")
    clean = root / "c.png"
    clean.write_bytes((Path(__file__).parent / "fixtures" / "base.png").read_bytes())
    os.utime(dirty, (1_600_000_000, 1_600_000_000))
    before = dirty.read_bytes()
    for p in (dirty, clean):
        strip_file(p, dest=copy_destination(p, [root]))
    out = tmp_path / "trip_已清除"
    assert dirty.read_bytes() == before
    assert SECRET not in (out / "day1" / "a.jpg").read_bytes()
    assert int((out / "day1" / "a.jpg").stat().st_mtime) == 1_600_000_000
    assert (out / "c.png").read_bytes() == clean.read_bytes()  # clean files are copied too
    targets, _ = scan([tmp_path], skip=[out])
    assert all("_已清除" not in str(p) for p in targets)


def test_new_dates_option(tmp_path):
    p = make_png(tmp_path / "x.png")
    os.utime(p, (1_600_000_000, 1_600_000_000))
    assert strip_file(p, Options(keep_dates=False)) == CLEANED
    assert p.stat().st_mtime > 1_700_000_000


def test_dry_run_with_dest_writes_nothing(tmp_path):
    p = make_png(tmp_path / "x.png")
    assert strip_file(p, dry_run=True, dest=tmp_path / "out" / "x.png") == CLEANED
    assert not (tmp_path / "out").exists()


# ---------------------------------------------------------------- settings
def test_settings_roundtrip_and_bad_files(tmp_path):
    f = tmp_path / "s.json"
    assert Settings.load(f) == Settings()
    s = Settings(keep_workflow=True, save_copy=True, last_update_check=5)
    s.save(f)
    assert Settings.load(f) == s
    f.write_text("{broken", encoding="utf-8")
    assert Settings.load(f) == Settings()
    f.write_text(json.dumps({"keep_workflow": "yes", "save_copy": True}), encoding="utf-8")
    loaded = Settings.load(f)
    assert loaded.keep_workflow is False and loaded.save_copy is True


# ---------------------------------------------------------------- updates
def test_parse_version():
    assert updates.parse_version("v0.10.1") == (0, 10, 1)
    assert updates.parse_version("0.2.0") > updates.parse_version("0.1.9")
    assert updates.parse_version("garbage") == (0,)


def test_newer_release():
    assert updates.newer_release("0.1.1", fetch=lambda: ("0.2.0", "u")) == ("0.2.0", "u")
    assert updates.newer_release("0.2.0", fetch=lambda: ("0.2.0", "u")) is None
    assert updates.newer_release("0.2.0", fetch=lambda: None) is None


# ---------------------------------------------------------------- windows integration
winreg = pytest.importorskip("winreg")
from strip_metadata import integration  # noqa: E402


@pytest.fixture
def reg_root():
    name = rf"Software\StripMetadataTest{os.getpid()}"
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, name)
    yield key
    key.Close()

    def rm(parent, sub):
        with winreg.OpenKey(parent, sub, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    child = winreg.EnumKey(k, 0)
                except OSError:
                    break
                rm(k, child)
        winreg.DeleteKey(parent, sub)
    rm(winreg.HKEY_CURRENT_USER, name)


def test_menu_install_and_remove(reg_root):
    assert not integration.menu_installed(reg_root)
    integration.install_menu('"C:\\Apps\\StripMetadata.exe"', root=reg_root)
    assert integration.menu_installed(reg_root)
    with winreg.OpenKey(reg_root, r"Software\Classes\Directory\shell\StripMetadata\command") as k:
        assert winreg.QueryValueEx(k, "")[0] == '"C:\\Apps\\StripMetadata.exe" "%1"'
    with winreg.OpenKey(reg_root, r"Software\Classes\Directory\Background\shell\StripMetadata\command") as k:
        assert winreg.QueryValueEx(k, "")[0].endswith('"%V"')
    with winreg.OpenKey(reg_root, r"Software\Classes\SystemFileAssociations\.mp4\shell\StripMetadata") as k:
        assert winreg.QueryValueEx(k, "MUIVerb")[0] == "清除相片資訊"
    integration.uninstall_menu(reg_root)
    assert not integration.menu_installed(reg_root)
    integration.uninstall_menu(reg_root)  # removing twice is fine


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_single_instance_forwarding():
    port = free_port()
    got: list[list[str]] = []
    ev = threading.Event()
    server = integration.InstanceServer(lambda ps: (got.append(ps), ev.set()), port=port)
    try:
        with pytest.raises(OSError):
            integration.InstanceServer(lambda ps: None, port=port)  # second window cannot claim it
        assert integration.forward_to_running(["D:\\a b\\照片.jpg"], port=port)
        assert ev.wait(3)
        assert got == [["D:\\a b\\照片.jpg"]]
    finally:
        server.close()
    time.sleep(0.1)
    assert not integration.forward_to_running(["x"], port=port)  # nobody listening
