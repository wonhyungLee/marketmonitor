"""Shared path helpers.

We keep these helpers tiny and dependency-free so they can be imported
from both the FastAPI app and standalone scripts.
"""

from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    """Return repository root (directory that contains `app/`)."""
    return Path(__file__).resolve().parent.parent


def find_indicator_dir(base_dir: Path | None = None) -> Path:
    """Locate the indicator CSV directory.

    Supports:
    - `지표데이터` (preferred)
    - `indicator_data` / `indicators` (optional alternative)
    - legacy zip-escaped folder names like `#Uc9c0#Ud45c...`

    Returns a Path (may not exist).
    """
    base = base_dir or repo_root()

    candidates = [
        base / "지표데이터",
        base / "indicator_data",
        base / "indicators",
    ]
    for d in candidates:
        if d.exists():
            return d

    # Legacy: folder names that look like "#Uxxxx#Uyyyy...".
    try:
        for child in base.iterdir():
            if child.is_dir() and child.name.startswith("#U") and "#Ud" in child.name:
                return child
    except FileNotFoundError:
        pass

    return base / "지표데이터"
