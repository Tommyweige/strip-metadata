"""Build dist/StripMetadata.exe with PyInstaller (used locally and in CI)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm", "--clean", "--onefile", "--windowed",
    "--name", "StripMetadata",
    "--icon", str(ROOT / "assets" / "app.ico"),
    "--paths", str(ROOT / "src"),
    "--collect-all", "tkinterdnd2",
    "--distpath", str(ROOT / "dist"),
    "--workpath", str(ROOT / "build"),
    "--specpath", str(ROOT / "build"),
    str(ROOT / "scripts" / "app_entry.py"),
]
print(" ".join(cmd), flush=True)
sys.exit(subprocess.call(cmd, cwd=ROOT))
