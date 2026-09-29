"""Analysis settings, with the defaults used by the CLI and web UI."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Settings:
    ela_quality: int = 90             # JPEG quality ELA resaves at
    ela_scale: int = 20               # brightness multiplier for the ELA view
    clone_size: int = 1536            # long edge the clone search works at
    clone_sensitivity: str = "normal"  # strict | normal | sensitive
    ghost_qualities: tuple[int, ...] = tuple(range(50, 100, 5))
    skip: set[str] = field(default_factory=set)   # technique keys to leave out

    def to_dict(self) -> dict:
        d = asdict(self)
        d["skip"] = sorted(self.skip)
        d["ghost_qualities"] = list(self.ghost_qualities)
        return d
