from pathlib import Path

from . import csharp, dart, elixir, go, haskell, javascript, jvm, lua, python, r, shell


def _whole_file_chunk(file_path: Path, repo_root: Path) -> list[tuple[int, int, str]]:
    """Fallback: treat the entire file as a single chunk."""
    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    line_count = source.count("\n") + 1
    return [(1, line_count, source)]


def _no_imports(file_path: Path, repo_root: Path) -> tuple[list[Path], list[str]]:
    """Stub parse_imports for non-code files that have no import semantics."""
    return [], []


REGISTRY: dict[str, object] = {
    # Python
    ".py": python.parse_imports,
    # JavaScript / TypeScript
    ".js": javascript.parse_imports,
    ".jsx": javascript.parse_imports,
    ".ts": javascript.parse_imports,
    ".tsx": javascript.parse_imports,
    ".mjs": javascript.parse_imports,
    # Go
    ".go": go.parse_imports,
    # JVM
    ".java": jvm.parse_imports,
    ".kt": jvm.parse_imports,
    ".scala": jvm.parse_imports,
    # C#
    ".cs": csharp.parse_imports,
    # Dart
    ".dart": dart.parse_imports,
    # Haskell
    ".hs": haskell.parse_imports,
    ".lhs": haskell.parse_imports,
    # Elixir
    ".ex": elixir.parse_imports,
    ".exs": elixir.parse_imports,
    # Lua
    ".lua": lua.parse_imports,
    # Shell
    ".sh": shell.parse_imports,
    ".bash": shell.parse_imports,
    # R
    ".r": r.parse_imports,
    ".R": r.parse_imports,
    # Non-code files — no import semantics, but included for summarization + indexing
    ".md": _no_imports,
    ".json": _no_imports,
    ".yaml": _no_imports,
    ".yml": _no_imports,
    ".toml": _no_imports,
    ".sql": _no_imports,
    ".proto": _no_imports,
    ".graphql": _no_imports,
    ".gql": _no_imports,
}

# Maps file extensions to chunk extractors: (file_path, repo_root) -> [(start, end, text), ...]
# Python uses AST for function/class-level granularity; everything else falls back to whole-file.
CHUNK_REGISTRY: dict[str, object] = {
    ".py": python.parse_chunks,
    # JS/TS and Go now have function-level chunking
    ".js": javascript.parse_chunks,
    ".jsx": javascript.parse_chunks,
    ".ts": javascript.parse_chunks,
    ".tsx": javascript.parse_chunks,
    ".mjs": javascript.parse_chunks,
    ".go": go.parse_chunks,
    # Tree-sitter chunkers for JVM, C#, Haskell, Elixir, Lua, Shell
    ".java": jvm.parse_chunks,
    ".kt": jvm.parse_chunks,
    ".scala": jvm.parse_chunks,
    ".cs": csharp.parse_chunks,
    ".dart": _whole_file_chunk,   # no tree-sitter-dart wheel for Python 3.13
    ".hs": haskell.parse_chunks,
    ".lhs": haskell.parse_chunks,
    ".ex": elixir.parse_chunks,
    ".exs": elixir.parse_chunks,
    ".lua": lua.parse_chunks,
    ".sh": shell.parse_chunks,
    ".bash": shell.parse_chunks,
    ".r": _whole_file_chunk,      # no tree-sitter-r wheel for Python 3.13
    ".R": _whole_file_chunk,
    # Non-code files — whole-file chunks (no sub-structure to extract)
    ".md": _whole_file_chunk,
    ".json": _whole_file_chunk,
    ".yaml": _whole_file_chunk,
    ".yml": _whole_file_chunk,
    ".toml": _whole_file_chunk,
    ".sql": _whole_file_chunk,
    ".proto": _whole_file_chunk,
    ".graphql": _whole_file_chunk,
    ".gql": _whole_file_chunk,
}
