from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _render_extra_frontmatter(extra: dict[str, Any]) -> str:
    """Render extra frontmatter fields as YAML lines."""
    lines = []
    for key, value in extra.items():
        if isinstance(value, list):
            items = "\n".join(f"  - {item}" for item in value)
            lines.append(f"{key}:\n{items}")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        else:
            lines.append(f"{key}: {value}")
    return ("\n" + "\n".join(lines)) if lines else ""


@dataclass
class Node:
    path: Path  # relative to repo root
    name: str

    def to_markdown(self, extra_frontmatter: dict[str, Any] | None = None) -> str:
        raise NotImplementedError

    def vault_path(self, vault_root: Path) -> Path:
        raise NotImplementedError


@dataclass
class FileNode(Node):
    imports: list[Path] = field(default_factory=list)  # repo-relative paths of resolved imports
    summary: str = ""

    def to_markdown(self, extra_frontmatter: dict[str, Any] | None = None) -> str:
        links = [p.with_suffix("") for p in self.imports]
        frontmatter_links = "\n".join(f"  - {p.as_posix()}" for p in links) if links else "  []"
        wikilinks = "\n".join(f"- [[{p.as_posix()}]]" for p in links)
        summary_block = self.summary if self.summary else "<!-- summary placeholder -->"
        extra = _render_extra_frontmatter(extra_frontmatter or {})

        return f"""\
---
type: file
path: {self.path.as_posix()}
links:
{frontmatter_links}{extra}
---

## {self.name}

{summary_block}

## Connections
{wikilinks if wikilinks else "_No internal imports found._"}
"""

    def vault_path(self, vault_root: Path) -> Path:
        return vault_root / self.path.with_suffix(".md")


@dataclass
class DirectoryNode(Node):
    children: list[Node] = field(default_factory=list)
    summary: str = ""

    def to_markdown(self, extra_frontmatter: dict[str, Any] | None = None) -> str:
        child_links = "\n".join(
            f"- [[{c.path.with_suffix('').as_posix() if isinstance(c, FileNode) else c.path.as_posix()}]]"
            for c in self.children
        )
        child_paths = "\n".join(
            f"  - {c.path.as_posix()}" for c in self.children
        ) if self.children else "  []"
        summary_block = self.summary if self.summary else "<!-- summary placeholder -->"
        extra = _render_extra_frontmatter(extra_frontmatter or {})

        return f"""\
---
type: directory
path: {self.path.as_posix()}
children:
{child_paths}{extra}
---

{summary_block}

## Contents
{child_links if child_links else "_Empty directory._"}
"""

    def vault_path(self, vault_root: Path) -> Path:
        # sits beside its folder: vault/src/utils.md
        return vault_root / self.path.with_suffix(".md")


@dataclass
class IndexNode(Node):
    top_level: list[Node] = field(default_factory=list)
    # repo_summary: str = ""  # placeholder

    def to_markdown(self, extra_frontmatter: dict[str, Any] | None = None) -> str:
        links = "\n".join(
            f"- [[{n.path.with_suffix('').as_posix() if isinstance(n, FileNode) else n.path.as_posix()}]]"
            for n in self.top_level
        )
        extra = _render_extra_frontmatter(extra_frontmatter or {})

        return f"""\
---
type: index{extra}
---

## Index

<!-- repo summary placeholder -->

## Top Level
{links if links else "_No top-level nodes._"}
"""

    def vault_path(self, vault_root: Path) -> Path:
        return vault_root / "INDEX.md"
