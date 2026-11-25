"""Run the built .exe in --cli mode on generated files and check the result.

usage: smoke_test.py path/to/StripMetadata.exe [--video path/to/ffmpeg.exe]

With --video, a sample video is made with that ffmpeg, then the exe runs with
ffmpeg removed from PATH, proving it finds the ffmpeg bundled next to it.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from conftest import SECRET, make_jpeg, make_mp4, make_png, make_webp  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("exe", type=Path)
ap.add_argument("--video", type=Path, help="ffmpeg.exe to create a test video with")
args = ap.parse_args()
exe = args.exe.resolve()

with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp) / "media"
    (d / "sub").mkdir(parents=True)
    files = [make_jpeg(d / "a.jpg"), make_png(d / "sub" / "b.png"), make_webp(d / "sub" / "c.webp")]
    env = dict(os.environ)
    if args.video:
        ffdir = str(args.video.resolve().parent)
        os.environ["PATH"] = ffdir + os.pathsep + os.environ["PATH"]
        files.append(make_mp4(d / "sub" / "v.mp4"))
        # hide every ffmpeg on PATH from the exe
        env["PATH"] = os.pathsep.join(p for p in env["PATH"].split(os.pathsep)
                                      if p and not (Path(p) / "ffmpeg.exe").exists())
        assert shutil.which("ffmpeg", path=env["PATH"]) is None
    log = Path(tmp) / "run.log"
    r = subprocess.run([str(exe), "--cli", str(d), "--yes", "--log", str(log)], env=env, timeout=300)
    text = log.read_text(encoding="utf-8") if log.exists() else "(no log)"
    print(text)
    assert r.returncode == 0, f"exit code {r.returncode}"
    want = f"'cleaned': {len(files)}"
    assert want in text, f"expected {want} in log"
    for f in files:
        if f.suffix != ".mp4":
            assert SECRET not in f.read_bytes(), f"metadata left in {f.name}"
    if args.video:
        probe = subprocess.run([str(args.video.parent / "ffprobe.exe"), "-v", "error", "-show_entries",
                                "format_tags", "-of", "default", str(files[-1])], capture_output=True, text=True)
        assert "SECRET" not in probe.stdout, probe.stdout
print("smoke test OK")
