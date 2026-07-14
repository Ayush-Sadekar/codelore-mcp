import re
from pathlib import Path

# `mod foo;` links to a file on disk; `use ...` paths mostly reference crates /
# already-declared modules, so only `mod` declarations are resolved here.
_MOD_RE = re.compile(r'^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+(\w+)\s*;', re.MULTILINE)

_CHUNK_TYPES: set[str] = {"function_item", "impl_item"}


def parse_imports(file_path: Path, repo_root: Path) -> tuple[list[Path], list[str]]:
    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        return [], [f"UnicodeDecodeError in {file_path.relative_to(repo_root)}: {e}"]

    resolved, warnings = [], []

    for m in _MOD_RE.finditer(source):
        name = m.group(1)
        r = _resolve(name, file_path, repo_root)
        if r:
            resolved.append(r)
        # inline `mod foo { ... }` bodies have no file to resolve — skip silently

    return list(dict.fromkeys(resolved)), warnings


def parse_chunks(file_path: Path, repo_root: Path) -> list[tuple[int, int, str]]:
    from ._treesitter import ts_parse_chunks, whole_file_fallback
    def _lang():
        import tree_sitter_rust as tsrust
        from tree_sitter import Language
        return Language(tsrust.language())
    result = ts_parse_chunks(file_path, _lang, _CHUNK_TYPES)
    if result is not None:
        return result
    return whole_file_fallback(file_path)


def _resolve(name: str, from_file: Path, repo_root: Path) -> Path | None:
    parent = from_file.parent
    for candidate in (parent / f"{name}.rs", parent / name / "mod.rs"):
        if candidate.exists():
            try:
                return candidate.resolve().relative_to(repo_root.resolve())
            except ValueError:
                pass
    return None
