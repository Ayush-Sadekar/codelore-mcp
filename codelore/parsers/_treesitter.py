"""Shared tree-sitter chunking helper used by individual language parsers."""
from __future__ import annotations

from pathlib import Path


def ts_parse_chunks(
    file_path: Path,
    get_language,
    chunk_types: set[str],
    *,
    collect_fn=None,
) -> list[tuple[int, int, str]] | None:
    """
    Parse a source file with tree-sitter and return (start, end, text) chunks.

    get_language: zero-arg callable returning a tree_sitter.Language. Should
        raise ImportError (or any Exception) if the grammar package is missing.
    chunk_types: node type names to capture as chunks (ignored when collect_fn set).
    collect_fn: optional (node, lines, out) callable for languages that need
        custom node matching (e.g. Elixir's `call` pattern).

    Returns None when tree-sitter or the grammar package is unavailable —
    callers should fall back to their own logic. Returns [] on unreadable file.
    Falls back to a single whole-file chunk when parsing finds no matches.
    """
    try:
        from tree_sitter import Parser
        language = get_language()
        parser = Parser(language)
    except Exception:
        return None

    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []

    tree = parser.parse(source.encode("utf-8"))
    lines = source.splitlines()
    chunks: list[tuple[int, int, str]] = []

    if collect_fn is not None:
        collect_fn(tree.root_node, lines, chunks)
    else:
        _walk(tree.root_node, chunk_types, lines, chunks)

    if not chunks:
        return [(1, max(len(lines), 1), source)]
    return chunks


def _walk(node, types: set[str], lines: list[str], out: list) -> None:
    if node.type in types:
        start = node.start_point[0] + 1  # 1-indexed, inclusive
        end = node.end_point[0] + 1
        out.append((start, end, "\n".join(lines[start - 1:end])))
    for child in node.children:
        _walk(child, types, lines, out)


def whole_file_fallback(file_path: Path) -> list[tuple[int, int, str]]:
    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    return [(1, max(source.count("\n") + 1, 1), source)]
