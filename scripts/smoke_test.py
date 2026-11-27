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
ap.add_argument("--watermark", action="store_true",
                help="also check the exe removes a Gemini sparkle offline (needs the watermark extra here)")
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

if args.watermark:
    import test_watermark as tw

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "marked"
        d.mkdir()
        f = tw._write(d / "gemini.png", tw._with_sparkle(tw._background()))
        before = tw._pixels(f)
        # no model cache and no route to Hugging Face: the bundled model must be used
        env = dict(os.environ, HF_HOME=str(Path(tmp) / "no-hf"),
                   HTTPS_PROXY="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9")
        log = Path(tmp) / "wm.log"
        r = subprocess.run([str(exe), "--cli", str(d), "--yes", "--remove-watermark", "--recheck",
                            "--log", str(log)], env=env, timeout=600)
        text = log.read_text(encoding="utf-8") if log.exists() else "(no log)"
        print(text)
        assert r.returncode == 0, f"watermark run exit code {r.returncode}"
        assert "'watermark': 1" in text, "sparkle not removed"
        x, y, s = tw._sparkle_box()
        assert tw._pixels(f)[y:y + s, x:x + s].mean() < before[y:y + s, x:x + s].mean() - 15
print("smoke test OK")
