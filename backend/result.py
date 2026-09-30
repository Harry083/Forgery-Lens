"""Common result types shared by every technique."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from PIL import Image

# Finding levels, most important first.
NOTABLE = "notable"  # worth a closer look
WEAK = "weak"        # minor
INFO = "info"        # note
LEVELS = (NOTABLE, WEAK, INFO)
LEVEL_LABEL = {NOTABLE: "Worth a closer look", WEAK: "Minor", INFO: "Note"}

# Which question a technique helps answer.
MANIPULATION = "manipulation"
AI = "ai"
PROVENANCE = "provenance"
CATEGORY_LABEL = {
    MANIPULATION: "Editing and manipulation",
    AI: "AI-generated imagery",
    PROVENANCE: "Metadata and provenance",
}


@dataclass
class Finding:
    level: str
    title: str
    detail: str
    technique: str = ""
    category: str = MANIPULATION

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level, "title": self.title, "detail": self.detail,
                "technique": self.technique, "category": self.category}


@dataclass
class View:
    """One image a technique produces, with a caption."""
    label: str
    image: Image.Image
    caption: str = ""


@dataclass
class Result:
    key: str
    title: str
    category: str
    guide: str                      # what to look for, shown with the views
    views: list[View] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    skipped: str = ""               # reason, if the technique could not run
    seconds: float = 0.0

    def add(self, level: str, title: str, detail: str, category: str | None = None) -> None:
        self.findings.append(Finding(level, title, detail, self.key, category or self.category))

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "title": self.title, "category": self.category,
            "skipped": self.skipped, "seconds": round(self.seconds, 3),
            "params": self.params, "metrics": self.metrics,
            "findings": [f.to_dict() for f in self.findings],
            "views": [v.label for v in self.views],
        }
