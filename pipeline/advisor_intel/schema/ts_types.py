"""A small reader for the FNA app's TypeScript sources, used to generate and check the slot catalog (§4).

It understands exactly what ``lib/fna/types.ts``, ``catalog.ts`` and ``riskProfile.ts`` use: exported
interfaces with one field per line, string-literal union type aliases, inline unions, arrays and
``Record<K, V>``, and the ``EXPENSE_ITEMS`` and ``RISK_QUESTIONS`` literal tables. It is not a
general TypeScript parser; the catalog test fails loudly if the sources drift outside this shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_INTERFACE = re.compile(r"export interface (\w+)\s*\{(.*?)\n\}", re.DOTALL)
_TYPE_ALIAS = re.compile(r"export type (\w+)\s*=\s*(.*?);", re.DOTALL)
_FIELD = re.compile(r"^\s*(\w+)(\?)?\s*:\s*(.+?);\s*(?://.*)?$")
_LITERAL = re.compile(r"'([^']*)'")


@dataclass
class TsField:
    name: str
    type: str
    optional: bool = False
    doc: str = ""


@dataclass
class TsModule:
    interfaces: dict[str, dict[str, TsField]] = field(default_factory=dict)
    unions: dict[str, list[str]] = field(default_factory=dict)  # string-literal unions only
    aliases: dict[str, str] = field(default_factory=dict)  # every other alias, raw

    def literal_values(self, ts_type: str) -> list[str] | None:
        """The string literals a field type allows: a named union, an inline union, or None."""
        t = ts_type.strip()
        if t in self.unions:
            return self.unions[t]
        if t in self.aliases:
            return self.literal_values(self.aliases[t])
        if "'" in t and all(p.strip().startswith("'") for p in t.split("|") if p.strip()):
            return _LITERAL.findall(t)
        if "|" in t:
            parts = [p.strip() for p in t.split("|") if p.strip()]
            out: list[str] = []
            for p in parts:
                if p in self.unions:
                    out += self.unions[p]
                elif p in self.aliases and (sub := self.literal_values(self.aliases[p])) is not None:
                    out += sub
                elif p.startswith("'"):
                    out += _LITERAL.findall(p)
                else:
                    return None
            return out
        return None


def _strip_comments(body: str) -> list[tuple[str, str]]:
    """Field lines with the doc comment that precedes each."""
    lines = body.splitlines()
    out: list[tuple[str, str]] = []
    doc: list[str] = []
    in_block = False
    for raw in lines:
        line = raw.strip()
        if in_block:
            doc.append(line.strip("*/ ").strip())
            if line.endswith("*/"):
                in_block = False
            continue
        if line.startswith(("/**", "/*")):
            text = line.strip("/* ").strip()
            if text:
                doc.append(text)
            in_block = not line.endswith("*/")
            continue
        if line.startswith("//") or not line:
            continue
        out.append((line, " ".join(d for d in doc if d)))
        doc = []
    return out


def parse_types(text: str) -> TsModule:
    mod = TsModule()
    for name, rhs in _TYPE_ALIAS.findall(text):
        rhs_clean = re.sub(r"//[^\n]*", "", rhs)
        lits = _LITERAL.findall(rhs_clean)
        parts = [p.strip() for p in rhs_clean.replace("\n", " ").split("|") if p.strip()]
        if lits and all(p.startswith("'") for p in parts):
            mod.unions[name] = lits
        else:
            mod.aliases[name] = " ".join(rhs_clean.split())
    for name, body in _INTERFACE.findall(text):
        fields: dict[str, TsField] = {}
        for line, doc in _strip_comments(body):
            m = _FIELD.match(line)
            if not m:
                raise ValueError(f"cannot read field line in interface {name}: {line!r}")
            fname, opt, ftype = m.group(1), bool(m.group(2)), m.group(3).strip()
            fields[fname] = TsField(fname, ftype, opt, doc)
        mod.interfaces[name] = fields
    return mod


@dataclass
class ExpenseItemDef:
    key: str
    label: str
    category: str
    essential: bool
    hint: str = ""


_EXPENSE = re.compile(
    r"\{\s*key:\s*'([^']+)',\s*label:\s*'((?:[^'\\]|\\.)*)',\s*category:\s*'([^']+)',\s*essential:\s*(true|false)(?:,\s*hint:\s*'((?:[^'\\]|\\.)*)')?\s*\}"
)


def parse_expense_items(text: str) -> list[ExpenseItemDef]:
    start = text.index("export const EXPENSE_ITEMS")
    end = text.index("];", start)
    block = text[start:end]
    return [
        ExpenseItemDef(k, lbl.replace("\\'", "'"), cat, ess == "true", (hint or "").replace("\\'", "'"))
        for k, lbl, cat, ess, hint in _EXPENSE.findall(block)
    ]


@dataclass
class RiskQuestionDef:
    id: str
    dimension: str
    question: str
    options: list[tuple[str, int]]


_QUESTION = re.compile(
    r"\{\s*id:\s*'([^']+)',\s*dimension:\s*'([^']+)',\s*question:\s*'((?:[^'\\]|\\.)*)',\s*options:\s*\[(.*?)\]\s*,?\s*\}", re.DOTALL
)
_OPTION = re.compile(r"\{\s*label:\s*'((?:[^'\\]|\\.)*)',\s*score:\s*(\d+)\s*\}")


def parse_risk_questions(text: str) -> list[RiskQuestionDef]:
    start = text.index("export const RISK_QUESTIONS")
    end = text.index("\n];", start)
    block = text[start:end]
    out = []
    for qid, dim, q, opts in _QUESTION.findall(block):
        out.append(
            RiskQuestionDef(qid, dim, q.replace("\\'", "'"), [(lbl.replace("\\'", "'"), int(s)) for lbl, s in _OPTION.findall(opts)])
        )
    return out


@dataclass
class AppSources:
    types: TsModule
    expense_items: list[ExpenseItemDef]
    risk_questions: list[RiskQuestionDef]
    hashes: dict[str, str]


def read_app_sources(repo_dir: Path) -> AppSources:
    import hashlib

    paths = {
        "lib/fna/types.ts": repo_dir / "lib/fna/types.ts",
        "lib/fna/catalog.ts": repo_dir / "lib/fna/catalog.ts",
        "lib/fna/riskProfile.ts": repo_dir / "lib/fna/riskProfile.ts",
    }
    texts = {k: p.read_text() for k, p in paths.items()}
    return AppSources(
        types=parse_types(texts["lib/fna/types.ts"]),
        expense_items=parse_expense_items(texts["lib/fna/catalog.ts"]),
        risk_questions=parse_risk_questions(texts["lib/fna/riskProfile.ts"]),
        hashes={k: hashlib.sha256(t.encode()).hexdigest()[:16] for k, t in texts.items()},
    )
