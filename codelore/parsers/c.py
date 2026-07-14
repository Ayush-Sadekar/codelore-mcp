import re
from pathlib import Path

# Only quoted includes ("foo.h") are project-local; angle-bracket includes
# (<stdio.h>) are system/library headers and are skipped silently.
_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*"([^"]+)"', re.MULTILINE)

_CHUNK_TYPES: set[str] = {"function_definition"}


def parse_imports(file_path: Path, repo_root: Path) -> tuple[list[Path], list[str]]:
    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        return [], [f"UnicodeDecodeError in {file_path.relative_to(repo_root)}: {e}"]

    resolved, warnings = [], []

    for m in _INCLUDE_RE.finditer(source):
        raw = m.group(1)
        r = resolve_include(raw, file_path, repo_root)
        if r:
            resolved.append(r)
        else:
            warnings.append(f"Unresolved include {raw!r} in {file_path.relative_to(repo_root)}")

    return list(dict.fromkeys(resolved)), warnings


def parse_chunks(file_path: Path, repo_root: Path) -> list[tuple[int, int, str]]:
    from ._treesitter import ts_parse_chunks, whole_file_fallback
    def _lang():
        import tree_sitter_c as tsc
        from tree_sitter import Language
        return Language(tsc.language())
    result = ts_parse_chunks(file_path, _lang, _CHUNK_TYPES)
    if result is not None:
        return result
    return whole_file_fallback(file_path)


def resolve_include(raw: str, from_file: Path, repo_root: Path) -> Path | None:
    """Shared by c.py and cpp.py: resolve a quoted #include relative to the
    including file first, then the repo root."""
    for base in (from_file.parent, repo_root):
        candidate = base / raw
        if candidate.exists():
            try:
                return candidate.resolve().relative_to(repo_root.resolve())
            except ValueError:
                pass
    return None
