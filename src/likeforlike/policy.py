"""Comp policy: which store-weeks count as like-for-like. Loaded from ``comp_policy.yaml``."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from .resources import project_file

REASONS = ("no_prior_year", "extra_week", "refurbishment", "closed_store", "new_store", "comp")


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class CompPolicy:
    restate: bool = True
    ramp_weeks: int = 12  # 0 switches the ramp exclusion off
    refurbishment: bool = True

    def variant(self, **changes: object) -> CompPolicy:
        return replace(self, **changes)

    def label(self) -> str:
        parts = []
        parts.append("restated LY" if self.restate else "no restatement")
        parts.append(f"ramp {self.ramp_weeks}w" if self.ramp_weeks else "no ramp exclusion")
        return ", ".join(parts)


def load_policy(path: Path | None = None) -> CompPolicy:
    path = path or project_file("comp_policy.yaml")
    doc = yaml.safe_load(path.read_text())
    if not isinstance(doc, dict):
        raise PolicyError(f"{path}: expected a mapping at the top level")
    if doc.get("version") != 1:
        raise PolicyError(f"{path}: unsupported policy version {doc.get('version')!r}")
    if tuple(doc.get("precedence", ())) != REASONS:
        raise PolicyError(
            f"{path}: precedence must be {list(REASONS)}, got {doc.get('precedence')!r}"
        )
    ramp = doc.get("ramp", {})
    weeks = int(ramp.get("trading_weeks", 0))
    if weeks < 0:
        raise PolicyError(f"{path}: ramp.trading_weeks must not be negative")
    if doc.get("relocation", {}).get("treat_as") != "new_store_id":
        raise PolicyError(f"{path}: relocation.treat_as must be new_store_id")
    return CompPolicy(
        restate=bool(doc.get("restatement", {}).get("enabled", True)),
        ramp_weeks=weeks if ramp.get("enabled", True) else 0,
        refurbishment=bool(doc.get("refurbishment", {}).get("enabled", True)),
    )
