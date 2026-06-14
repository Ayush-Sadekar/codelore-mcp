import re
from pathlib import Path

# Matches the opening line of a function or class declaration.
# Covers: named functions, async functions, generators, classes, and arrow
# functions / function expressions assigned to const/let/var.
_CHUNK_HEADER_RE = re.compile(
    r"""
    ^[ \t]*                                         # optional indent
    (?:export\s+(?:default\s+)?)?                   # optional export / export default
    (?:
        (?:async\s+)?function\s*\*?\s*\w+           # function declaration
        |class\s+\w+                                # class declaration
        |(?:const|let|var)\s+\w+\s*=\s*            # arrow or assigned function
          (?:async\s+)?\(.*?\)\s*=>
        |(?:const|let|var)\s+\w+\s*=\s*
          (?:async\s+)?function
    )
    """,
    re.VERBOSE | re.MULTILINE,
)

# matches: import ... from './path', import './path', export ... from './path', require('./path')
_IMPORT_RE = re.compile(
    r"""(?:import|export)\s+(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]|require\s*\(\s*['"]([^'"]+)['"]\s*\)""",
    re.MULTILINE,
)
_EXTENSIONS = [".js", ".ts", ".jsx", ".tsx", ".mjs"]


def parse_imports(file_path: Path, repo_root: Path) -> tuple[list[Path], list[str]]:
    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        return [], [f"UnicodeDecodeError in {file_path.relative_to(repo_root)}: {e}"]

    resolved, warnings = [], []

    for m in _IMPORT_RE.finditer(source):
        raw = m.group(1) or m.group(2)
        if not raw.startswith("."):
            continue  # npm package — skip silently

        r = _resolve(raw, file_path, repo_root)
        if r:
            resolved.append(r)
        else:
            warnings.append(f"Unresolved import {raw!r} in {file_path.relative_to(repo_root)}")

    return list(dict.fromkeys(resolved)), warnings


_JS_CHUNK_TYPES: set[str] = {
    "function_declaration",
    "class_declaration",
    "method_definition",
    "generator_function_declaration",
}
_TS_CHUNK_TYPES: set[str] = _JS_CHUNK_TYPES | {
    "interface_declaration",
    "abstract_class_declaration",
    "enum_declaration",
    "type_alias_declaration",
}


def parse_chunks(file_path: Path, repo_root: Path) -> list[tuple[int, int, str]]:
    from ._treesitter import ts_parse_chunks
    ext = file_path.suffix.lower()
    if ext == ".ts":
        def _lang():
            import tree_sitter_typescript as tsts
            from tree_sitter import Language
            return Language(tsts.language_typescript())
        types = _TS_CHUNK_TYPES
    elif ext == ".tsx":
        def _lang():
            import tree_sitter_typescript as tsts
            from tree_sitter import Language
            return Language(tsts.language_tsx())
        types = _TS_CHUNK_TYPES
    else:
        def _lang():
            import tree_sitter_javascript as tsjs
            from tree_sitter import Language
            return Language(tsjs.language())
        types = _JS_CHUNK_TYPES
    result = ts_parse_chunks(file_path, _lang, types)
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
        if _CHUNK_HEADER_RE.match(lines[i]):
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


def _resolve(raw: str, from_file: Path, repo_root: Path) -> Path | None:
    base = (from_file.parent / raw).resolve()

    # try exact path, then with each known extension, then as index file
    candidates = [base] + [base.with_suffix(ext) for ext in _EXTENSIONS] + [base / f"index{ext}" for ext in _EXTENSIONS]

    for c in candidates:
        if c.exists():
            try:
                return c.relative_to(repo_root)
            except ValueError:
                pass

    return None
