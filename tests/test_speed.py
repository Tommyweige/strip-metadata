"""Repeat runs skip unchanged clean files; files are processed in parallel."""
import os
import threading
import time

from conftest import SECRET, make_jpeg, make_png

from strip_metadata import batch, cli
from strip_metadata import core as core_mod
from strip_metadata.cache import CleanCache
from strip_metadata.core import CLEAN, CLEANED, Options, strip_file


def test_cache_skips_unchanged_clean_files(tmp_path, monkeypatch):
    p = make_png(tmp_path / "a.png")
    cache = CleanCache(tmp_path / "c.json")
    assert strip_file(p, cache=cache) == CLEANED
    cache.save()

    cache = CleanCache(tmp_path / "c.json")  # next launch
    monkeypatch.setattr(core_mod, "_strip", lambda *a: (_ for _ in ()).throw(AssertionError("opened")))
    assert strip_file(p, cache=cache) == CLEAN and cache.hits == 1


def test_cache_rechecks_edited_file_and_other_options(tmp_path):
    p = make_png(tmp_path / "a.png")
    cache = CleanCache(tmp_path / "c.json")
    strip_file(p, cache=cache)
    # someone puts metadata back (same mtime forced): size differs, so it is checked again
    st = p.stat()
    make_png(p)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert strip_file(p, cache=cache) == CLEANED and cache.hits == 0
    # different options are a different question
    assert strip_file(p, Options(keep_workflow=True), cache=cache) == CLEAN and cache.hits == 0
    assert strip_file(p, cache=cache) == CLEAN and cache.hits == 0  # one entry per file: last check wins
    assert strip_file(p, cache=cache) == CLEAN and cache.hits == 1


def test_cache_not_used_for_save_copy_or_dirty_dry_run(tmp_path):
    p = make_jpeg(tmp_path / "a.jpg")
    cache = CleanCache(tmp_path / "c.json")
    assert strip_file(p, dry_run=True, cache=cache) == CLEANED
    assert strip_file(p, dry_run=True, cache=cache) == CLEANED  # still dirty, never cached
    strip_file(p, dest=tmp_path / "out" / "a.jpg", cache=cache)
    assert not cache.entries


def test_cache_survives_broken_file(tmp_path):
    f = tmp_path / "c.json"
    f.write_text("{nope", encoding="utf-8")
    assert CleanCache(f).entries == {}


def test_batch_runs_in_parallel_and_reports_all(tmp_path, monkeypatch):
    files = [make_png(tmp_path / f"{i}.png") for i in range(12)]
    active, peak = [0], [0]
    lock = threading.Lock()
    real = core_mod._strip

    def slow(*a):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        try:
            return real(*a)
        finally:
            with lock:
                active[0] -= 1
    monkeypatch.setattr(core_mod, "_strip", slow)
    results = list(batch.run(files, Options(), workers=4))
    assert sorted(r.path for r in results) == sorted(files)
    assert all(r.status == CLEANED for r in results)
    assert peak[0] > 1
    assert all(SECRET not in f.read_bytes() for f in files)


def test_batch_stop_stops_early(tmp_path):
    files = [make_png(tmp_path / f"{i}.png") for i in range(50)]
    stop = threading.Event()
    seen = 0
    for _ in batch.run(files, Options(), stop=stop, workers=2):
        seen += 1
        stop.set()
    assert seen < 50


def test_cli_second_run_skips(media_dir, capsys):
    assert cli.main([str(media_dir), "--yes"]) == 0
    capsys.readouterr()
    assert cli.main([str(media_dir), "--yes"]) == 0
    assert "直接略過" in capsys.readouterr().out
    assert cli.main([str(media_dir), "--yes", "--recheck"]) == 0
    assert "直接略過" not in capsys.readouterr().out
