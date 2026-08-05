from __future__ import annotations

import shutil
from pathlib import Path

from dre import config


def run_dir(run_id: str) -> Path:
    d = config.RUNS_DIR / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def store_run_images(run_id: str, prev_image: Path, revised_image: Path) -> tuple[Path, Path]:
    d = run_dir(run_id)
    prev_dest = d / f"prev{prev_image.suffix}"
    revised_dest = d / f"revised{revised_image.suffix}"
    shutil.copy2(prev_image, prev_dest)
    shutil.copy2(revised_image, revised_dest)
    return prev_dest, revised_dest
