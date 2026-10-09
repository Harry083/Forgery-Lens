"""Run every technique over one exhibit."""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from . import __version__
from .exhibit import Exhibit
from .result import AI, LEVELS, Finding, Result
from .settings import Settings
from .techniques import TECHNIQUES


@dataclass
class Analysis:
    exhibit: Exhibit
    settings: Settings
    results: list[Result] = field(default_factory=list)
    started: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    seconds: float = 0.0
    version: str = __version__

    @property
    def findings(self) -> list[Finding]:
        out = [f for r in self.results for f in r.findings]
        return sorted(out, key=lambda f: LEVELS.index(f.level))

    def counts(self, category: str | None = None) -> dict[str, int]:
        c = {lv: 0 for lv in LEVELS}
        for f in self.findings:
            if category is None or f.category == category:
                c[f.level] += 1
        return c

    def result(self, key: str) -> Result | None:
        return next((r for r in self.results if r.key == key), None)

    def to_dict(self) -> dict:
        return {
            "tool": "Clarity", "version": self.version,
            "analysed_at": self.started.isoformat(), "seconds": round(self.seconds, 2),
            "exhibit": self.exhibit.describe(), "settings": self.settings.to_dict(),
            "counts": self.counts(), "ai_counts": self.counts(AI),
            "findings": [f.to_dict() for f in self.findings],
            "techniques": [r.to_dict() for r in self.results],
            "metadata": self.exhibit.meta.to_dict(),
        }


def analyse(ex: Exhibit, settings: Settings | None = None,
            progress: Callable[[str], None] | None = None) -> Analysis:
    settings = settings or Settings()
    an = Analysis(ex, settings)
    t_all = time.perf_counter()
    for key, mod in TECHNIQUES.items():
        if key in settings.skip:
            continue
        if progress:
            progress(mod.TITLE)
        t = time.perf_counter()
        try:
            res = mod.run(ex, settings)
        except Exception as e:  # one technique failing shouldn't lose the rest
            res = Result(key, mod.TITLE, getattr(mod, "CATEGORY", "manipulation"), "",
                         skipped=f"failed: {e.__class__.__name__}: {e}")
            res.metrics["traceback"] = traceback.format_exc(limit=3)
        res.seconds = time.perf_counter() - t
        an.results.append(res)
    an.seconds = time.perf_counter() - t_all
    return an
