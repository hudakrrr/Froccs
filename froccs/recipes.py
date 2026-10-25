"""
Recipes: known-good settings for specific programs (like Lutris install scripts).

Each recipe is a small JSON file in froccs/recipes/. Fröccs matches the
installer's file name against "match" (a regular expression) and copies the
settings into the new app, where they stay editable in Settings.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

RECIPE_DIR = Path(__file__).resolve().parent / "recipes"


@dataclass
class Recipe:
    id: str
    name: str
    match: str
    dotnet: bool = False
    graphics: str = "dxvk"
    verbs: list = field(default_factory=list)
    dll_overrides: dict = field(default_factory=dict)
    registry: list = field(default_factory=list)   # [key, name, type, data]
    env: dict = field(default_factory=dict)
    file_types: list = field(default_factory=list)
    notes: str = ""
    warning: str = ""
    source: str = ""


def load_all() -> list[Recipe]:
    out = []
    for f in sorted(RECIPE_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        known = Recipe.__dataclass_fields__
        out.append(Recipe(id=f.stem, **{k: v for k, v in data.items() if k in known}))
    return out


def get(recipe_id: str) -> Optional[Recipe]:
    return next((r for r in load_all() if r.id == recipe_id), None)


def find(*names: str) -> Optional[Recipe]:
    """First recipe whose pattern matches any of the given names."""
    text = " ".join(n for n in names if n)
    text = re.sub(r"[_\-.]+", " ", text)
    for r in load_all():
        if re.search(r.match, text, re.I):
            return r
    return None
