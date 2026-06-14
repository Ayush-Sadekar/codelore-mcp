import re
from pathlib import Path

# Matches a Go function declaration at the start of a line.
# Handles: plain functions, pointer/value receiver methods, and generic functions (Go 1.18+).
_FUNC_RE = re.compile(
    r"^func(?:\s*\([^)]+\))?\s+\w+\s*(?:\[.*?\])?\s*\(",
    re.MULTILINE,
)

# matches single and grouped imports
_SINGLE_RE = re.compile(r'^import\s+"([^"]+)"', re.MULTILINE)
_GROUP_RE = re.compile(r'"([^"]+)"')
_BLOCK_RE = re.compile(r'import\s+\((.*?)\)', re.DOTALL)


def parse_imports(file_path: Path, repo_root: Path) -> tuple[list[Path], list[str]]:
    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        return [], [f"UnicodeDecodeError in {file_path.relative_to(repo_root)}: {e}"]

    raw_imports = []
    for m in _SINGLE_RE.finditer(source):
        raw_imports.append(m.group(1))
    for block in _BLOCK_RE.finditer(source):
        for m in _GROUP_RE.finditer(block.group(1)):
            raw_imports.append(m.group(1))

    resolved, warnings = [], []
    for raw in raw_imports:
        r = _resolve(raw, repo_root)
        if r:
            resolved.append(r)
        # stdlib/external packages silently skipped — no warning needed

    return list(dict.fromkeys(resolved)), warnings


_GO_CHUNK_TYPES: set[str] = {"function_declaration", "method_declaration"}


def parse_chunks(file_path: Path, repo_root: Path) -> list[tuple[int, int, str]]:
    from ._treesitter import ts_parse_chunks
    def _lang():
        import tree_sitter_go as tsgo
        from tree_sitter import Language
        return Language(tsgo.language())
    result = ts_parse_chunks(file_path, _lang, _GO_CHUNK_TYPES)
    if result is not None:
        return result
    return _regex_parse_chunks(file_path)


def _regex_parse_chunks(file_path: Path) -> list[tuple[int, int, str]]:
    """Brace-depth fallback used when tree-sitter is unavailable."""
    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []

    lines = source.splitlines()
    chunks: list[tuple[int, int, str]] = []
    i = 0
    while i < len(lines):
        if _FUNC_RE.match(lines[i]):
            start = i + 1  # 1-indexed
            depth = 0
            found_open = False
            j = i
            while j < len(lines):
                for ch in lines[j]:
                    if ch == "{":
                        depth += 1
                        found_open = True
                    elif ch == "}":
                        depth -= 1
                if found_open and depth == 0:
                    break
                j += 1
            end = j + 1  # 1-indexed
            chunks.append((start, end, "\n".join(lines[i : j + 1])))
            i = j + 1
        else:
            i += 1

    if not chunks:
        line_count = len(lines)
        return [(1, line_count, source)]
    return chunks


def _resolve(import_path: str, repo_root: Path) -> Path | None:
    # Go imports are module paths; try matching any suffix against repo dirs
    parts = import_path.split("/")
    for i in range(len(parts)):
        candidate = repo_root.joinpath(*parts[i:])
        if candidate.is_dir():
            try:
                return candidate.relative_to(repo_root)
            except ValueError:
                pass
    return None
