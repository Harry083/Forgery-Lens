"""Registry of techniques, in the order they run and appear in reports.

Each module exposes KEY, TITLE and run(exhibit, settings) -> Result.
"""

from importlib import import_module

_MODULES = [
    # editing and manipulation
    "ela", "pca", "gradient", "wavelet", "clone", "ghost", "jpeg_grid", "double_jpeg", "resampling", "cfa",
    # AI-generated imagery
    "spectrum", "watermark",
    # metadata, structure and provenance
    "provenance",
]

TECHNIQUES = {}
for _name in _MODULES:
    _mod = import_module(f"{__name__}.{_name}")
    TECHNIQUES[_mod.KEY] = _mod
