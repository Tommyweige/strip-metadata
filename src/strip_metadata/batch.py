"""Process many files in parallel (shared by the window and the command line)."""
from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

from .cache import CleanCache
from .core import Options, StripError, strip_file

WORKERS = max(2, min(6, os.cpu_count() or 2))


@dataclass
class Result:
    path: Path
    status: str  # CLEANED | WATERMARK | CLEAN | "failed"
    error: str | None
    took: float
    dest: Path | None


def run(targets: Iterable[Path], options: Options, dry_run: bool = False,
        dest_for: Callable[[Path], Path | None] | None = None, cache: CleanCache | None = None,
        stop: threading.Event | None = None, workers: int = WORKERS) -> Iterator[Result]:
    """Yield one Result per file as each finishes (order not kept). Stops early when `stop` is set."""
    targets = list(targets)
    stop = stop or threading.Event()

    def one(p: Path) -> Result:
        t = time.time()
        dest = dest_for(p) if dest_for else None
        try:
            status = strip_file(p, options, dry_run, dest=dest, cache=cache)
            return Result(p, status, None, time.time() - t, dest)
        except StripError as e:
            return Result(p, "failed", str(e), time.time() - t, dest)

    it = iter(targets)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        running = set()
        while True:
            while not stop.is_set() and len(running) < workers * 2:  # feed lazily so stopping is quick
                p = next(it, None)
                if p is None:
                    break
                running.add(ex.submit(one, p))
            if not running:
                break
            done, running = wait(running, return_when=FIRST_COMPLETED)
            for f in done:
                yield f.result()
    if cache:
        cache.save()
