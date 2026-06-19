from pathlib import Path
from typing import Any

from .nodes import DirectoryNode, FileNode, IndexNode, Node
from .parsers import REGISTRY

IGNORE_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache"}


def build_tree(
    repo_root: Path,
    explanations: dict[str, str] | None = None,
    dir_explanations: dict[str, str] | None = None,
    extra_frontmatter: dict[str, Any] | None = None,
) -> tuple[IndexNode, list[Node], list[str]]:
    all_nodes: list[Node] = []
    all_warnings: list[str] = []
    dir_cache: dict[Path, DirectoryNode] = {}

    def get_or_create_dir(rel_dir: Path) -> DirectoryNode:
        if rel_dir in dir_cache:
            return dir_cache[rel_dir]
        summary = (dir_explanations or {}).get(rel_dir.as_posix(), "")
        node = DirectoryNode(path=rel_dir, name=rel_dir.name, summary=summary)
        dir_cache[rel_dir] = node
        all_nodes.append(node)
        return node

    for code_file in sorted(repo_root.rglob("*")):
        if not code_file.is_file():
            continue

        rel = code_file.relative_to(repo_root)

        if any(part in IGNORE_DIRS for part in rel.parts):
            continue

        parser = REGISTRY.get(code_file.suffix)
        if parser is None:
            continue

        imports, warnings = parser(code_file, repo_root)
        all_warnings.extend(warnings)

        summary = (explanations or {}).get(rel.as_posix(), "")
        file_node = FileNode(path=rel, name=code_file.name, imports=imports, summary=summary)
        all_nodes.append(file_node)

        if rel.parent != Path("."):
            parent = get_or_create_dir(rel.parent)
            parent.children.append(file_node)
            _ensure_ancestor_chain(rel.parent, dir_cache, all_nodes)

    for rel_dir, dir_node in dir_cache.items():
        if rel_dir.parent != Path("."):
            parent = get_or_create_dir(rel_dir.parent)
            if dir_node not in parent.children:
                parent.children.append(dir_node)

    top_level = [n for n in all_nodes if n.path.parent == Path(".")]
    index = IndexNode(path=Path("INDEX"), name="Index", top_level=top_level)

    return index, all_nodes, all_warnings


def _ensure_ancestor_chain(rel_dir: Path, cache: dict, all_nodes: list[Node]) -> None:
    parts = rel_dir.parts
    for i in range(1, len(parts)):
        ancestor = Path(*parts[:i])
        if ancestor not in cache:
            node = DirectoryNode(path=ancestor, name=ancestor.name)
            cache[ancestor] = node
            all_nodes.append(node)


def write_vault(
    index: IndexNode,
    all_nodes: list[Node],
    warnings: list[str],
    vault_root: Path,
    extra_frontmatter: dict[str, Any] | None = None,
) -> None:
    vault_root.mkdir(parents=True, exist_ok=True)
    fm = extra_frontmatter or {}

    for node in [index] + all_nodes:
        out = node.vault_path(vault_root)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(node.to_markdown(fm), encoding="utf-8")

    if warnings:
        (vault_root / "warnings.log").write_text("\n".join(warnings) + "\n", encoding="utf-8")
        print(f"  {len(warnings)} warnings written to warnings.log")

    print(f"Vault written to {vault_root} ({1 + len(all_nodes)} files)")
