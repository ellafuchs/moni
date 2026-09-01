"""Small helpers shared by the entry point (main.py)."""
import re
from pathlib import Path
from urllib.parse import urlparse


def _slug(url: str) -> str:
    """A short, filesystem-safe id for a PDF URL — its trailing number, else the stem."""
    stem = Path(urlparse(url).path).stem
    tail = re.findall(r"\d+", stem)
    return tail[-1] if tail else (re.sub(r"[^A-Za-z0-9_-]+", "_", stem) or "doc")
