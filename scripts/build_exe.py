"""Build dist/StripMetadata.exe with PyInstaller (used locally and in CI)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from strip_metadata import __version__  # noqa: E402

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
    "--version-file", str(version_file),
    "--distpath", str(ROOT / "dist"),
    "--workpath", str(ROOT / "build"),
    "--specpath", str(ROOT / "build"),
    str(ROOT / "scripts" / "app_entry.py"),
]
print(" ".join(cmd), flush=True)
sys.exit(subprocess.call(cmd, cwd=ROOT))
