"""Optional removal of known visible AI watermarks (e.g. the Gemini sparkle) from photos.

Built on the `remove-ai-watermarks` library (Apache-2.0,
https://github.com/wiltodelta/remove-ai-watermarks), installed with the `watermark` extra.
Unlike metadata stripping this changes pixels: the photo is re-encoded at maximum quality,
so it only runs when the user turns it on. Only images are handled, not videos.

The fill uses the MI-GAN model. The .exe ships it in `hf/` next to the app and runs offline;
a source install downloads it from Hugging Face once on first use.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
from importlib.util import find_spec
from pathlib import Path

BACKEND = "migan"
# Removal decodes a whole photo and runs a model that already uses every core; doing one
# at a time keeps memory bounded when the batch runs several files in parallel.
_lock = threading.Lock()
_env_ready = False


class WatermarkError(Exception):
    pass


def available() -> bool:
    """True when the library and the model runtime are installed."""
    return all(find_spec(m) is not None for m in ("remove_ai_watermarks", "cv2", "onnxruntime"))


def bundled_model_dir() -> Path | None:
    """The Hugging Face cache shipped with the .exe, if any."""
    if getattr(sys, "frozen", False):
        d = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "hf"
        if d.is_dir():
            return d
    return None


def _prepare_env() -> None:
    global _env_ready
    if _env_ready:
        return
    if (d := bundled_model_dir()) is not None:
        # Read before huggingface_hub is imported; keeps the packaged app off the network.
        os.environ["HF_HUB_CACHE"] = str(d)
        os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    _env_ready = True


def remove(path: Path, dry_run: bool = False) -> tuple[bytes | None, list[str]]:
    """Find known visible AI marks in the image at `path` and fill them in.

    Returns (new file bytes, labels of removed marks); bytes is None when nothing was found
    or in a dry run. The original file is never touched. Raises WatermarkError on failure.
    """
    if not available():
        raise WatermarkError("沒有安裝去除浮水印的元件")
    _prepare_env()
    path = Path(path)
    with _lock:
        try:
            import remove_ai_watermarks as raiw

            if dry_run:
                report = raiw.remove_visible_detailed(path, backend=BACKEND, strip_metadata=False)
                return None, list(report.labels)
            with tempfile.TemporaryDirectory(prefix="stripmeta_wm_") as tmp:
                out = Path(tmp) / ("out" + path.suffix.lower())
                report = raiw.remove_visible_detailed(path, out, backend=BACKEND,
                                                      strip_metadata=False, write_noop=False)
                if not report.labels:
                    return None, []
                return out.read_bytes(), list(report.labels)
        except WatermarkError:
            raise
        except Exception as e:  # the library raises many kinds; the original stays untouched
            raise WatermarkError(f"去除浮水印失敗：{e}") from e
