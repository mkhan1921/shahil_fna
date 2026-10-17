"""Versioned prompt files (spec §7.2, §15.6). ``load_prompt(name)`` reads ``<name>/v<n>.md``.

Prompts are versioned in git and pinned per run (``VersionPins.prompt_versions``); GEPA writes
candidate versions as ``v{n+1}.md`` for a pull request. Placeholders are ``{{name}}``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent
_VERSION = re.compile(r"^v(\d+)\.md$")


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str  # "v1"
    text: str

    def render(self, **values: object) -> str:
        out = self.text
        for k, v in values.items():
            out = out.replace("{{" + k + "}}", str(v))
        missing = re.findall(r"\{\{(\w+)\}\}", out)
        if missing:
            raise KeyError(f"prompt {self.name}/{self.version} needs {sorted(set(missing))}")
        return out


def versions(name: str) -> list[str]:
    d = PROMPTS_DIR / name
    found = sorted(int(m.group(1)) for p in d.glob("v*.md") if (m := _VERSION.match(p.name)))
    return [f"v{n}" for n in found]


@lru_cache(maxsize=256)
def load_prompt(name: str, version: str | None = None) -> Prompt:
    """The pinned version, or the latest when none is pinned."""
    vs = versions(name)
    if not vs:
        raise FileNotFoundError(f"no prompt versions under {PROMPTS_DIR / name}")
    v = version or vs[-1]
    return Prompt(name=name, version=v, text=(PROMPTS_DIR / name / f"{v}.md").read_text())


def pinned(name: str, pins: dict[str, str] | None) -> Prompt:
    return load_prompt(name, (pins or {}).get(name))
