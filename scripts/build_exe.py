"""Build dist/StripMetadata.exe with PyInstaller (used locally and in CI).

When the `watermark` extra is installed, the watermark-removal library and its MI-GAN
model are bundled so the app removes visible AI watermarks offline. --no-watermark leaves
them out.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from strip_metadata import __version__, watermark  # noqa: E402

MIGAN_REPO, MIGAN_FILE = "andraniksargsyan/migan", "migan.onnx"
# Pulled in only by features the app does not use; keeps the exe from ballooning.
EXCLUDE = ["torch", "torchvision", "torchaudio", "transformers", "diffusers", "scipy",
           "matplotlib", "pandas", "mcp", "audioseal", "librosa", "portable_ffmpeg", "portable_ffmpeg_full"]

WATERMARK_MODULES = ["remove_ai_watermarks", "cv2", "onnxruntime", "huggingface_hub", "numpy", "c2pa", "av"]
WITH_WATERMARK = watermark.available() and "--no-watermark" not in sys.argv


def c2pa_dll() -> Path:
    """Only the runtime DLL: the wheel also carries a ~280 MB import library and debug symbols."""
    import c2pa

    return Path(c2pa.__file__).parent / "libs" / "c2pa_c.dll"


def watermark_args() -> list[str]:
    """PyInstaller arguments that bundle the watermark library and an offline model cache."""
    from huggingface_hub import hf_hub_download

    cache = ROOT / "build" / "hf"
    shutil.rmtree(cache, ignore_errors=True)
    hf_hub_download(repo_id=MIGAN_REPO, filename=MIGAN_FILE, cache_dir=cache)
    for link in cache.rglob("*"):  # bundle real files, never symlinks
        if link.is_symlink():
            target = link.resolve()
            link.unlink()
            shutil.copyfile(target, link)
    shutil.rmtree(cache / ".locks", ignore_errors=True)
    args = ["--collect-all", "remove_ai_watermarks", "--copy-metadata", "remove-ai-watermarks",
            "--collect-submodules", "av",
            "--add-binary", f"{c2pa_dll()}{os.pathsep}c2pa/libs", "--add-data", f"{cache}{os.pathsep}hf"]
    for mod in EXCLUDE:
        args += ["--exclude-module", mod]
    return args

# Windows version resource (Properties > Details); code signing needs ProductName/ProductVersion.
nums = tuple(int(n) for n in __version__.split(".")[:3]) + (0,)
VERSION_INFO = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Tommyweige'),
      StringStruct('FileDescription', 'StripMetadata - remove photo and video metadata'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', 'StripMetadata'),
      StringStruct('LegalCopyright', 'MIT License'),
      StringStruct('OriginalFilename', 'StripMetadata.exe'),
      StringStruct('ProductName', 'StripMetadata'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
version_file = ROOT / "build" / "version_info.txt"
version_file.parent.mkdir(exist_ok=True)
version_file.write_text(VERSION_INFO, encoding="utf-8")

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm", "--clean", "--onefile", "--windowed",
    "--name", "StripMetadata",
    "--icon", str(ROOT / "assets" / "app.ico"),
    "--paths", str(ROOT / "src"),
    "--collect-all", "tkinterdnd2",
    *(watermark_args() if WITH_WATERMARK else [a for m in WATERMARK_MODULES for a in ("--exclude-module", m)]),
    "--version-file", str(version_file),
    "--distpath", str(ROOT / "dist"),
    "--workpath", str(ROOT / "build"),
    "--specpath", str(ROOT / "build"),
    str(ROOT / "scripts" / "app_entry.py"),
]
print(" ".join(cmd), flush=True)
sys.exit(subprocess.call(cmd, cwd=ROOT))
